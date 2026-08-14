"""Central order/position lifecycle manager."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from backend.config import settings
from backend.core.exchange.base import OrderRequest, OrderResponse
from backend.core.position_manager import PositionManager
from backend.db.models import Order as OrderModel, OrderStatusEnum

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloseResult:
    """Fill-aware outcome of one close request.

    ``realized_pnl`` is cumulative for the position at the time of the
    response.  Consumers can therefore safely process repeated updates by
    comparing it with the last value they applied.
    """

    order_ids: List[str] = field(default_factory=list)
    requested_quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    fully_closed: bool = False
    realized_pnl: Decimal = Decimal("0")
    status: str = "unknown"

    def __iter__(self):
        """Preserve the old order-id iterable behavior for callers."""
        return iter(self.order_ids)

    def __len__(self) -> int:
        return len(self.order_ids)


class OrderManager:
    def __init__(self, exchange_adapter, position_manager: PositionManager, db_session_factory=None):
        self.exchange = exchange_adapter
        self.position_manager = position_manager
        self.db_session_factory = db_session_factory or position_manager.db_session_factory
        self.open_orders: Dict[str, OrderResponse] = {}
        self.retry_counts: Dict[str, int] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._is_spot = settings.EXCHANGE_MARKET_TYPE.lower() == "spot"

    async def _get_spot_balance(self, symbol: str) -> tuple[Decimal, Decimal]:
        """Return (free, total) base balance for a Spot symbol."""
        if not self._is_spot or self.exchange is None:
            return Decimal("0"), Decimal("0")
        try:
            base_asset = symbol.split("/")[0]
            raw = await asyncio.to_thread(self.exchange.exchange.fetch_balance)
            free = Decimal(str(raw.get("free", {}).get(base_asset, 0)))
            total = Decimal(str(raw.get("total", {}).get(base_asset, free)))
            if total < free:
                total = free
            return free, total
        except Exception:
            return Decimal("0"), Decimal("0")

    async def _get_free_base_balance(self, symbol: str) -> Decimal:
        free, _ = await self._get_spot_balance(symbol)
        return free

    def _spot_min_amount(self, symbol: str) -> Optional[Decimal]:
        """Return the exchange minimum Spot amount when the adapter exposes it."""
        if not self._is_spot or self.exchange is None:
            return None
        try:
            raw_exchange = self.exchange.exchange
            markets = getattr(raw_exchange, "markets", {}) or {}
            market = markets.get(symbol, {}) or {}
            limits = market.get("limits") or {}
            amount_limits = limits.get("amount") or {}
            minimum = amount_limits.get("min")
            if minimum is not None:
                value = Decimal(str(minimum))
                return value if value > 0 else None
        except Exception:
            pass
        return None

    def _spot_quantity_is_placeable(self, symbol: str, quantity: Decimal) -> bool:
        """Check a Spot quantity before it reaches CCXT amount_to_precision."""
        if quantity <= 0:
            return False
        minimum = self._spot_min_amount(symbol)
        return minimum is None or quantity >= minimum

    async def _get_current_price(self, symbol: str) -> Optional[Decimal]:
        if self.exchange is None:
            return None
        try:
            ticker = await asyncio.to_thread(self.exchange.fetch_ticker, symbol)
            return ticker.price if ticker else None
        except Exception:
            return None

    def _is_sl_valid_for_position(self, side: str, sl_price: Decimal, current_price: Decimal) -> bool:
        if side.lower() == "buy":
            return sl_price < current_price
        return sl_price > current_price

    def _is_tp_valid_for_position(self, side: str, tp_price: Decimal, current_price: Decimal) -> bool:
        if side.lower() == "buy":
            return tp_price > current_price
        return tp_price < current_price

    async def start(self):
        # PositionManager.start() must run before this method so persisted open
        # positions are available. Rebuild missing protection before monitoring.
        await self._recover_protective_orders()
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(self._order_monitor_loop())

    async def stop(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    @staticmethod
    def _protective_client_id(position_id: str, role: str, index: int = 0) -> str:
        compact = position_id.replace("-", "")[:20]
        suffix = f"{role[:1]}{index}"
        return f"ats{compact}{suffix}"[:32]

    async def _recover_protective_orders(self) -> None:
        """Restore missing SL/TP after restart without blind duplicate creation."""
        for position in list(self.position_manager.positions.values()):
            if position.quantity <= 0 or position.status != "OPEN":
                continue
            try:
                is_stale = await self._check_position_staleness(position)
                if is_stale:
                    logger.warning(
                        "Position %s stale (not on exchange); closing in DB",
                        position.position_id,
                    )
                    await self.position_manager.close_position(
                        position.position_id, "stale_not_on_exchange"
                    )
                    continue
                await self._ensure_position_protection(position)
            except Exception:
                logger.exception("Protective-order recovery failed for %s", position.position_id)

    async def _check_position_staleness(self, position) -> bool:
        """Check if a position exists on the exchange. Returns True if stale."""
        if self.exchange is None:
            return False
        if settings.EXCHANGE_MARKET_TYPE.lower() == "futures":
            return await self._check_futures_staleness(position)
        return await self._check_spot_staleness(position)

    async def _check_futures_staleness(self, position) -> bool:
        """For futures, check if position exists on exchange."""
        try:
            remote_positions = await asyncio.to_thread(
                self.exchange.fetch_positions, position.symbol
            )
            for remote in remote_positions:
                if (remote.symbol == position.symbol
                        and remote.side.lower() == position.side.lower()
                        and remote.quantity > 0):
                    return False
            return True
        except Exception as exc:
            logger.debug("Cannot verify futures position %s: %s", position.symbol, exc)
            return False

    async def _check_spot_staleness(self, position) -> bool:
        """For spot, check if the position still exists on the exchange.

        A position is stale when EITHER:
        1. (BUY only) The total base balance (free + used) is zero, OR
        2. There are no open protective orders AND the position has been
           open for more than 15 minutes (suggesting SL/TP already executed).

        For SELL positions, only condition 2 applies because selling base
        does not reduce the base balance to zero (we receive quote instead).
        """
        try:
            # For BUY positions: check if base balance is zero
            if position.side == "buy":
                base_asset = position.symbol.split("/")[0]
                raw = await asyncio.to_thread(self.exchange.exchange.fetch_balance)
                free = Decimal(str(raw.get("free", {}).get(base_asset, 0)))
                used = Decimal(str(raw.get("used", {}).get(base_asset, 0)))
                total = free + used
                if total <= 0:
                    return True

            # Check if protective orders exist on the exchange
            open_orders = await asyncio.to_thread(
                self.exchange.fetch_open_orders, position.symbol
            )
            has_protective = False
            for order in (open_orders or []):
                if order.status in {"open", "partial"}:
                    has_protective = True
                    break
            # If no protective orders and position is older than 15 minutes,
            # it was likely closed externally (SL/TP filled, manual close)
            if not has_protective:
                age_seconds = time.time() - position.entry_time
                if age_seconds > 900:  # 15 minutes
                    logger.warning(
                        "Spot position %s (side=%s) has no protective orders "
                        "and is %.0fs old; marking stale",
                        position.symbol, position.side, age_seconds,
                    )
                    return True
            return False
        except Exception as exc:
            logger.debug("Cannot verify spot position %s: %s", position.symbol, exc)
            return False

    # ── Spot staged-TP helpers ────────────────────────────────────────────
    # On Binance Spot every open SELL order reserves BTC from free_base.
    # Placing all TPs simultaneously can consume the entire free balance,
    # leaving nothing for SL.  The staged architecture solves this by
    # keeping at most ONE TP + ONE SL active at any time.
    #
    # Lifecycle:
    #   1. Place TP1 (claims qty / num_remaining_tps of free BTC).
    #   2. Place SL for (position_qty - tp1_qty).  SL fully covers the
    #      portion NOT claimed by TP1.
    #   3. When TP1 fills → cancel SL → place TP2 → place new SL for the
    #      reduced position.
    #   4. Repeat until all TPs filled or SL triggers.
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _next_tp_index(position) -> Optional[int]:
        """Return the index of the next TP that has NOT been filled.

        Returns ``None`` when all TP levels have been placed/filled.
        """
        for idx in range(len(position.tp_prices)):
            key = f"tp{idx}"
            # A TP is "unfilled" if its order id is NOT in tp_order_ids
            # AND it hasn't been placed in the current staged cycle.
            # We detect "placed" by checking if its client id has an active
            # exchange order (or was already filled and removed).
            if idx not in position._filled_tp_indices:
                return idx
        return None

    async def _place_spot_staged_tp(
        self, position, tp_index: int, opposite: str, protective_ids: list,
    ) -> Optional[str]:
        """Place a single staged TP for Spot and return its order_id."""
        if tp_index >= len(position.tp_prices):
            return None
        key = f"tp{tp_index}"
        client_id = position.protective_client_ids.get(key)
        if not client_id:
            client_id = self._protective_client_id(position.position_id, "tp", tp_index)
            position.protective_client_ids[key] = client_id
            await self.position_manager._persist_position(position)

        # Check for existing active order
        existing = await self._find_protective_by_client_id(position.symbol, client_id)
        if existing is not None:
            position.tp_order_ids = [existing.order_id]
            self.open_orders[existing.order_id] = existing
            await self._persist_order(existing, position.position_id)
            return existing.order_id

        # Validate TP direction
        current_price = await self._get_current_price(position.symbol)
        if current_price is not None and not self._is_tp_valid_for_position(
            position.side, position.tp_prices[tp_index], current_price
        ):
            logger.warning(
                "TP %s would trigger immediately (market=%s, side=%s, symbol=%s); "
                "skipping staged TP level %d",
                position.tp_prices[tp_index], current_price, position.side,
                position.symbol, tp_index,
            )
            return None

        # Stage quantity: for the next TP, use (position_qty / remaining_tps).
        # This ensures TP1 gets its fair share while leaving room for SL.
        # When only one TP remains, it claims all free_base (SL cannot be
        # placed — the position is temporarily unprotected until TP fills).
        num_remaining = Decimal(str(len(position.tp_prices) - tp_index))
        qty = position.quantity / num_remaining

        free_base = await self._get_free_base_balance(position.symbol) if position.side == "buy" else Decimal("0")
        qty = min(qty, free_base)
        if qty <= 0:
            logger.info(
                "No free base for staged TP%d on %s (position=%s qty=%s free_base=%s)",
                tp_index, position.symbol, position.position_id, position.quantity, free_base,
            )
            return None

        # A tiny remainder is not a valid Binance Spot order.  Prefer the SL
        # for the whole position rather than allowing a TP precision error to
        # abort protection setup.
        if not self._spot_quantity_is_placeable(position.symbol, qty):
            minimum = self._spot_min_amount(position.symbol)
            logger.warning(
                "Skipping staged TP%d on %s: quantity %s is below minimum %s; "
                "SL will remain the priority protective order",
                tp_index, position.symbol, qty, minimum,
            )
            return None

        req = OrderRequest(
            symbol=position.symbol, side=opposite, type="take_profit",
            quantity=qty, price=position.tp_prices[tp_index],
            stopPrice=position.tp_prices[tp_index], client_order_id=client_id,
        )
        resp = await self._place_order_with_retry(req)
        self.open_orders[resp.order_id] = resp
        position.tp_order_ids = [resp.order_id]
        protective_ids.append(resp.order_id)
        await self._persist_order(resp, position.position_id)
        return resp.order_id

    async def _place_spot_staged_sl(
        self, position, opposite: str, protective_ids: list,
    ) -> Optional[str]:
        """Place SL covering the remainder after the active staged TP.

        SL quantity = min(position_qty, free_base_after_tp).
        fetch_balance already reflects BTC reserved by the active TP order,
        so free_base is the actual available amount.
        """
        if position.sl_price is None:
            return None

        current_price = await self._get_current_price(position.symbol)
        if current_price is not None and not self._is_sl_valid_for_position(
            position.side, position.sl_price, current_price
        ):
            logger.warning(
                "SL %s would trigger immediately (market=%s, side=%s, symbol=%s); "
                "skipping staged SL",
                position.sl_price, current_price, position.side, position.symbol,
            )
            return None

        # Re-fetch free_base — this already reflects BTC reserved by open
        # TP orders on the exchange side.
        free_base = await self._get_free_base_balance(position.symbol) if position.side == "buy" else Decimal("0")
        sl_qty = min(position.quantity, free_base)
        if sl_qty <= 0:
            logger.info(
                "No free base for staged SL on %s (position=%s qty=%s free_base=%s; "
                "position temporarily unprotected until TP fills)",
                position.symbol, position.position_id, position.quantity, free_base,
            )
            return None

        if not self._spot_quantity_is_placeable(position.symbol, sl_qty):
            minimum = self._spot_min_amount(position.symbol)
            # If the active TP already covers the whole position except for a
            # dust remainder smaller than Binance's minimum amount, there is
            # no valid SL order that can be submitted for that remainder.
            # Treat the position as TP-covered instead of generating a local
            # precision error on every recovery cycle.
            active_tp_qty = Decimal("0")
            if position.tp_order_ids:
                active_tp = self.open_orders.get(position.tp_order_ids[0])
                if active_tp is not None:
                    active_tp_qty = Decimal(active_tp.quantity or 0)
            if minimum is not None and active_tp_qty > 0 and active_tp_qty + sl_qty >= position.quantity:
                logger.info(
                    "Skipping staged SL on %s: remaining %s is below minimum %s "
                    "and active TP %s covers the position",
                    position.symbol, sl_qty, minimum, active_tp_qty,
                )
                return None
            logger.warning(
                "Skipping staged SL on %s: quantity %s is below minimum %s; "
                "position %s cannot receive a valid Spot SL at its current size",
                position.symbol, sl_qty, minimum, position.position_id,
            )
            return None

        client_id = position.protective_client_ids.get("sl")
        if not client_id:
            client_id = self._protective_client_id(position.position_id, "sl")
            position.protective_client_ids["sl"] = client_id
            await self.position_manager._persist_position(position)

        existing = await self._find_protective_by_client_id(position.symbol, client_id)
        if existing is not None:
            if existing.quantity != sl_qty:
                await self.cancel_order(existing.order_id, position.symbol)
            else:
                self.open_orders[existing.order_id] = existing
                protective_ids.append(existing.order_id)
                await self._persist_order(existing, position.position_id)
                await self.position_manager.update_position_sl(
                    position.position_id, position.sl_price, existing.order_id,
                )
                return existing.order_id

        req = OrderRequest(
            symbol=position.symbol, side=opposite, type="stop_loss",
            quantity=sl_qty, stopPrice=position.sl_price,
            client_order_id=client_id,
        )
        resp = await self._place_order_with_retry(req)
        self.open_orders[resp.order_id] = resp
        protective_ids.append(resp.order_id)
        await self._persist_order(resp, position.position_id)
        await self.position_manager.update_position_sl(
            position.position_id, position.sl_price, resp.order_id,
        )
        return resp.order_id

    async def _ensure_position_protection(self, position) -> None:
        opposite = "sell" if position.side == "buy" else "buy"

        if self._is_spot:
            await self._ensure_spot_staged_protection(position, opposite)
        else:
            await self._ensure_futures_protection(position, opposite)

    async def _ensure_futures_protection(self, position, opposite: str) -> None:
        """Futures: SL + all TPs can use full position (reduceOnly)."""
        if position.quantity <= 0:
            return

        previous_tp_ids = list(position.tp_order_ids)
        position.tp_order_ids = []

        # Place TPs
        if position.tp_prices:
            base_qty = position.quantity / Decimal(len(position.tp_prices))
            for idx, tp_price in enumerate(position.tp_prices):
                key = f"tp{idx}"
                client_id = position.protective_client_ids.get(key)
                if not client_id:
                    client_id = self._protective_client_id(position.position_id, "tp", idx)
                    position.protective_client_ids[key] = client_id
                    await self.position_manager._persist_position(position)
                existing = await self._find_protective_by_client_id(position.symbol, client_id)
                if existing is None and idx < len(previous_tp_ids):
                    existing = await self._find_order_by_id(position.symbol, previous_tp_ids[idx])
                if existing is not None:
                    position.tp_order_ids.append(existing.order_id)
                    self.open_orders[existing.order_id] = existing
                    await self._persist_order(existing, position.position_id)
                    continue
                qty = position.quantity - base_qty * idx if idx == len(position.tp_prices) - 1 else base_qty
                if qty <= 0:
                    continue
                current_price = await self._get_current_price(position.symbol)
                if current_price is not None and not self._is_tp_valid_for_position(
                    position.side, tp_price, current_price
                ):
                    logger.warning(
                        "TP %s would trigger immediately (market=%s, side=%s, symbol=%s); "
                        "skipping TP level %d during recovery",
                        tp_price, current_price, position.side, position.symbol, idx,
                    )
                    continue
                req = OrderRequest(
                    symbol=position.symbol, side=opposite, type="take_profit",
                    quantity=qty, price=tp_price, stopPrice=tp_price,
                    client_order_id=client_id,
                )
                resp = await self._place_order_with_retry(req)
                self.open_orders[resp.order_id] = resp
                position.tp_order_ids.append(resp.order_id)
                await self._persist_order(resp, position.position_id)

        # Place SL
        if position.sl_price is not None:
            client_id = position.protective_client_ids.get("sl")
            if not client_id:
                client_id = self._protective_client_id(position.position_id, "sl")
                position.protective_client_ids["sl"] = client_id
                await self.position_manager._persist_position(position)
            existing = await self._find_protective_by_client_id(position.symbol, client_id)
            if existing is None:
                existing = await self._find_order_by_id(position.symbol, position.sl_order_id)
            if existing is not None:
                position.sl_order_id = existing.order_id
                await self._persist_order(existing, position.position_id)
                await self.position_manager.update_position_sl(position.position_id, position.sl_price, existing.order_id)
            else:
                current_price = await self._get_current_price(position.symbol)
                if current_price is not None and not self._is_sl_valid_for_position(
                    position.side, position.sl_price, current_price
                ):
                    logger.warning(
                        "SL %s would trigger immediately; skipping recovery SL",
                        position.sl_price,
                    )
                    if position.sl_order_id:
                        position.sl_order_id = None
                        await self.position_manager._persist_position(position)
                else:
                    if position.sl_order_id:
                        position.sl_order_id = None
                        await self.position_manager._persist_position(position)
                    req = OrderRequest(
                        symbol=position.symbol, side=opposite, type="stop_loss",
                        quantity=position.quantity, stopPrice=position.sl_price,
                        client_order_id=client_id,
                    )
                    resp = await self._place_order_with_retry(req)
                    self.open_orders[resp.order_id] = resp
                    await self._persist_order(resp, position.position_id)
                    await self.position_manager.update_position_sl(position.position_id, position.sl_price, resp.order_id)

        await self.position_manager._persist_position(position)

    async def _ensure_spot_staged_protection(self, position, opposite: str) -> None:
        """Spot staged: recover active TP/SL before checking free base.

        On restart, an existing SELL TP/SL can reserve the entire base balance.
        Checking ``free_base`` before discovering those orders incorrectly
        reported "No free base" and could cause duplicate protection attempts.
        """
        if position.quantity <= 0:
            return

        previous_tp_ids = list(position.tp_order_ids)
        filled_indices = set()
        active_tp_index: Optional[int] = None

        # First recover an already-active staged TP.  Older persisted positions
        # may have only tp_order_ids, so use the deterministic client id plus
        # the persisted exchange order id as fallbacks.
        for idx in range(len(position.tp_prices)):
            key = f"tp{idx}"
            client_id = position.protective_client_ids.get(key)
            had_client_id = bool(client_id)
            lookup_client_id = client_id or self._protective_client_id(
                position.position_id, "tp", idx
            )

            existing = await self._find_protective_by_client_id(
                position.symbol, lookup_client_id
            )
            if existing is None and idx < len(previous_tp_ids):
                existing = await self._find_order_by_id(
                    position.symbol, previous_tp_ids[idx]
                )

            if existing is not None:
                if not client_id:
                    position.protective_client_ids[key] = lookup_client_id
                if active_tp_index is None:
                    active_tp_index = idx
                    position.tp_order_ids = [existing.order_id]
                    self.open_orders[existing.order_id] = existing
                    await self._persist_order(existing, position.position_id)
                else:
                    logger.warning(
                        "Multiple active staged TPs found for %s; keeping TP%d and not creating another",
                        position.symbol, active_tp_index,
                    )
                continue

            # Only an identity that was already persisted means this TP had
            # previously been submitted.  A deterministic lookup id generated
            # just for this recovery pass must NOT be treated as a filled TP:
            # otherwise recovery would cancel a valid SL and recreate it on
            # every cycle.
            if had_client_id:
                filled_indices.add(idx)

        if active_tp_index is not None:
            filled_indices.discard(active_tp_index)

        # Recover an already-active SL before looking at free base.  A valid
        # SL may itself reserve the remaining base, so free_base can be zero.
        sl_client_id = position.protective_client_ids.get("sl")
        if not sl_client_id:
            sl_client_id = self._protective_client_id(position.position_id, "sl")
            position.protective_client_ids["sl"] = sl_client_id

        existing_sl = await self._find_protective_by_client_id(position.symbol, sl_client_id)
        if existing_sl is None:
            existing_sl = await self._find_order_by_id(position.symbol, position.sl_order_id)

        if existing_sl is not None:
            position.sl_order_id = existing_sl.order_id
            self.open_orders[existing_sl.order_id] = existing_sl
            await self._persist_order(existing_sl, position.position_id)

        # After a TP fill the old SL was sized for the pre-fill position.
        # Cancel it so the reduced position can receive a correctly sized SL.
        if filled_indices and position.sl_order_id:
            if existing_sl is not None:
                await self.cancel_order(position.sl_order_id, position.symbol)
            position.sl_order_id = None
            existing_sl = None

        # If both staged orders are already active, recovery is complete.
        if active_tp_index is not None and existing_sl is not None:
            position._filled_tp_indices = filled_indices
            await self.position_manager._persist_position(position)
            return

        # If a TP is already active, do not try to create it again.  Only the
        # missing SL needs to be restored.
        if active_tp_index is not None:
            position._filled_tp_indices = filled_indices
            await self._place_spot_staged_sl(position, opposite, [])
            await self.position_manager._persist_position(position)
            return

        free_base = await self._get_free_base_balance(position.symbol) if position.side == "buy" else Decimal("0")
        if free_base <= 0:
            # Log detailed diagnostics: is the zero caused by existing protective
            # orders reserving all base, or is the account genuinely empty?
            used_base = Decimal("0")
            total_base = Decimal("0")
            try:
                fb, tb = await self._get_spot_balance(position.symbol)
                total_base = tb
                used_base = tb - fb
            except Exception:
                pass
            existing_count = 0
            if position.sl_order_id:
                existing_count += 1
            existing_count += len(position.tp_order_ids)
            logger.info(
                "No free base for staged protective orders on %s "
                "(position=%s qty=%s free_base=%s total_base=%s "
                "reserved_by_protective=%s existing_protective_orders=%d)",
                position.symbol, position.position_id, position.quantity,
                free_base, total_base, used_base, existing_count,
            )
            return

        next_tp = None
        for idx in range(len(position.tp_prices)):
            if idx not in filled_indices:
                next_tp = idx
                break

        position._filled_tp_indices = filled_indices
        protective_ids = []

        if next_tp is not None:
            await self._place_spot_staged_tp(position, next_tp, opposite, protective_ids)

        # Place/recover the SL for the remaining free base.
        await self._place_spot_staged_sl(position, opposite, protective_ids)
        await self.position_manager._persist_position(position)

    async def _place_spot_initial_protection(
        self, position_id: str, symbol: str, side: str,
        actual_qty: Decimal, sl_price, tp_list: list,
        protective_ids: list,
    ) -> None:
        """Place initial staged protection for a new Spot position.

        Only ONE TP is placed, followed by SL for the remainder.
        """
        position = await self.position_manager.get_position(position_id)
        if position is None:
            return
        opposite = "sell" if side == "buy" else "buy"
        position._filled_tp_indices = set()

        # Determine which TP index to start with
        next_tp = 0 if tp_list else None
        if next_tp is not None:
            await self._place_spot_staged_tp(position, next_tp, opposite, protective_ids)
        await self._place_spot_staged_sl(position, opposite, protective_ids)
        await self.position_manager._persist_position(position)

    async def _place_futures_initial_protection(
        self, position_id: str, symbol: str, side: str,
        actual_qty: Decimal, sl_price, tp_list: list,
        protective_ids: list,
    ) -> None:
        """Place all TPs + SL for Futures (reduceOnly, no balance reservation)."""
        opposite = "sell" if side == "buy" else "buy"

        if tp_list:
            tp_qty = actual_qty / Decimal(len(tp_list))
            tp_current_price = await self._get_current_price(symbol)
            for index, tp_price in enumerate(tp_list):
                current_qty = actual_qty - tp_qty * index if index == len(tp_list) - 1 else tp_qty
                if current_qty <= 0:
                    continue
                tp_price_dec = Decimal(tp_price)
                if tp_current_price is not None and not self._is_tp_valid_for_position(
                    side, tp_price_dec, tp_current_price
                ):
                    logger.warning(
                        "TP %s would trigger immediately; skipping TP level %d",
                        tp_price_dec, index,
                    )
                    continue
                tp_client_id = self._protective_client_id(position_id, "tp", index)
                position = await self.position_manager.get_position(position_id)
                if position is not None:
                    position.protective_client_ids[f"tp{index}"] = tp_client_id
                    await self.position_manager._persist_position(position)
                tp = OrderRequest(
                    symbol=symbol, side=opposite, type="take_profit",
                    quantity=current_qty, price=tp_price_dec,
                    stopPrice=tp_price_dec, client_order_id=tp_client_id,
                )
                tp_resp = await self._place_order_with_retry(tp)
                self.open_orders[tp_resp.order_id] = tp_resp
                protective_ids.append(tp_resp.order_id)
                await self._persist_order(tp_resp, position_id)
                position = await self.position_manager.get_position(position_id)
                if position is not None:
                    position.tp_order_ids.append(tp_resp.order_id)
                    await self.position_manager._persist_position(position)

        if sl_price is not None:
            sl_price_dec = Decimal(sl_price)
            current_price = await self._get_current_price(symbol)
            if current_price is not None and not self._is_sl_valid_for_position(
                side, sl_price_dec, current_price
            ):
                logger.warning(
                    "SL %s would trigger immediately; opening without SL",
                    sl_price_dec,
                )
            else:
                sl_client_id = self._protective_client_id(position_id, "sl")
                position = await self.position_manager.get_position(position_id)
                if position is not None:
                    position.protective_client_ids["sl"] = sl_client_id
                    await self.position_manager._persist_position(position)
                sl = OrderRequest(
                    symbol=symbol, side=opposite, type="stop_loss",
                    quantity=actual_qty, stopPrice=sl_price_dec,
                    client_order_id=sl_client_id,
                )
                sl_resp = await self._place_order_with_retry(sl)
                self.open_orders[sl_resp.order_id] = sl_resp
                protective_ids.append(sl_resp.order_id)
                await self._persist_order(sl_resp, position_id)
                await self.position_manager.update_position_sl(position_id, sl_price_dec, sl_resp.order_id)

    async def _find_protective_by_client_id(self, symbol: str, client_id: str):
        finder = getattr(self.exchange, "find_order_by_client_id", None)
        if callable(finder):
            try:
                found = await asyncio.to_thread(finder, symbol, client_id)
                if found is not None:
                    terminal = {
                        "closed", "filled", "canceled", "cancelled",
                        "expired", "rejected", "done",
                    }
                    if str(getattr(found, "status", "") or "").lower() not in terminal:
                        return found
            except Exception:
                pass
        # Fallback for adapters that do not expose a native client-id lookup.
        try:
            orders = await asyncio.to_thread(self.exchange.fetch_open_orders, symbol)
            for order in orders or []:
                if getattr(order, "client_order_id", None) == client_id:
                    return order
        except Exception:
            pass
        return None

    async def _find_order_by_id(self, symbol: str, order_id: Optional[str]):
        if not order_id:
            return None
        fetcher = getattr(self.exchange, "fetch_order", None)
        if not callable(fetcher):
            return None
        try:
            order = await asyncio.to_thread(fetcher, symbol, order_id)
            if order is not None and str(getattr(order, "status", "")).lower() not in {
                "closed", "filled", "canceled", "cancelled", "expired", "rejected"
            }:
                return order
        except Exception:
            pass
        return None

    async def open_position(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        order_type: str = "limit",
        price: Optional[Decimal] = None,
        sl_price: Optional[Decimal] = None,
        tp_prices: Optional[List[Decimal]] = None,
        strategy_name: str = "unknown",
    ) -> str:
        """Open an entry and persist the position before installing protection.

        The exchange is the source of truth for the actually filled entry size.
        Persisting the position immediately after the entry prevents a crash
        between Entry and SL/TP creation from losing the position during recovery.
        """
        side = side.lower()
        quantity = Decimal(quantity)
        if quantity <= 0:
            raise ValueError("Position quantity must be positive")

        entry = OrderRequest(
            symbol=symbol, side=side, type=order_type.lower(),
            quantity=quantity, price=price,
        )
        entry_resp = await self._place_order_with_retry(entry)
        self.open_orders[entry_resp.order_id] = entry_resp
        await self._persist_order(entry_resp)

        entry_status = str(entry_resp.status).lower()
        if entry_status not in {"closed", "filled"}:
            raise RuntimeError(
                f"Entry order {entry_resp.order_id} is not filled (status={entry_resp.status}); "
                "protective orders were not placed"
            )

        actual_qty = entry_resp.filled_quantity or entry_resp.quantity or quantity
        if actual_qty <= 0:
            actual_qty = quantity
        actual_entry = entry_resp.avg_price or price
        if actual_entry is None or actual_entry <= 0:
            raise RuntimeError("Exchange did not return a valid entry price")

        tp_list = [Decimal(x) for x in (tp_prices or [])]
        # Persist the position BEFORE any protective order is submitted.
        position_id = await self.position_manager.create_position(
            position_id=str(uuid.uuid4()),
            exchange=self.exchange.get_exchange_name(),
            symbol=symbol, side=side, quantity=actual_qty,
            entry_price=actual_entry, sl_price=sl_price, tp_prices=tp_list,
            entry_order_id=entry_resp.order_id, strategy=strategy_name,
        )
        await self._persist_order(entry_resp, position_id)
        if entry_resp.fill_delta > 0 or entry_resp.filled_quantity > 0:
            fill_for_position = entry_resp
            if fill_for_position.filled_quantity <= 0:
                fill_for_position.filled_quantity = actual_qty
            await self.position_manager.record_fill(position_id, fill_for_position, "entry")

        sl_id: Optional[str] = None
        tp_ids: List[str] = []
        protective_ids: List[str] = []
        free_base = await self._get_free_base_balance(symbol) if side.lower() == "buy" else Decimal("0")

        # ── Protective order placement ────────────────────────────────────
        # Futures: SL + all TPs use full position (reduceOnly, no balance
        # reservation).  Place all simultaneously.
        #
        # Spot staged: On Binance Spot every open SELL order reserves BTC
        # from free_base.  We place at most ONE TP + ONE SL at a time.
        # When a TP fills the monitor advances to the next TP level and
        # re-places the SL for the reduced position.
        # ──────────────────────────────────────────────────────────────────
        sellable_qty = min(actual_qty, free_base) if self._is_spot else actual_qty

        try:
            if self._is_spot:
                await self._place_spot_initial_protection(
                    position_id, symbol, side, actual_qty, sl_price,
                    tp_list, protective_ids,
                )
            else:
                await self._place_futures_initial_protection(
                    position_id, symbol, side, actual_qty, sl_price,
                    tp_list, protective_ids,
                )

            return position_id
        except Exception:
            logger.exception(
                "Protective order setup failed for entry %s; flattening actual position %s",
                entry_resp.order_id, actual_qty,
            )
            for order_id in protective_ids:
                await self.cancel_order(order_id, symbol)
            try:
                emergency_qty = min(actual_qty, free_base) if self._is_spot else actual_qty
                emergency = OrderRequest(
                    symbol=symbol,
                    side="sell" if side == "buy" else "buy",
                    type="market", quantity=emergency_qty,
                )
                emergency_resp = await self._place_order_with_retry(emergency)
                await self._persist_order(emergency_resp, position_id)
                if emergency_resp.filled_quantity > 0 or emergency_resp.fill_delta > 0:
                    await self.position_manager.record_fill(position_id, emergency_resp, "exit")
                await self.position_manager.close_position(position_id, "protective_setup_failed")
            except Exception:
                logger.critical(
                    "EMERGENCY FLATTEN FAILED for %s after protective order failure; "
                    "position %s remains persisted for recovery", symbol, position_id,
                )
            raise

    async def close_position(
        self,
        position_id: str,
        close_type: str = "full",
        percentage: Decimal = Decimal("1"),
        reason: str = "manual",
    ) -> CloseResult:
        """Submit a close request and report only exchange-confirmed progress.

        A terminal order status without a fill is not evidence that exposure was
        closed.  The returned result is therefore based on the observed fill
        quantity, while ``fully_closed`` is true only after PositionManager has
        atomically persisted the closed position and its Trade row.
        """
        position = await self.position_manager.get_position(position_id)
        if not position:
            raise ValueError(f"Position {position_id} not found")
        percentage = max(Decimal("0"), min(Decimal("1"), Decimal(percentage)))
        close_qty = position.quantity * percentage
        if close_qty <= 0:
            return CloseResult(
                requested_quantity=Decimal("0"),
                remaining_quantity=position.quantity,
                realized_pnl=self.position_manager.realized_pnl(position),
                status="not_requested",
            )

        request = OrderRequest(
            symbol=position.symbol,
            side="sell" if position.side == "buy" else "buy",
            type="market",
            quantity=close_qty,
        )
        resp = await self._place_order_with_retry(request)
        await self._persist_order(resp, position_id)

        # Never allow a malformed exchange response to over-close the local
        # position.  ``filled`` is cumulative for this newly-created close
        # order; ``fill_delta`` is useful for adapters that only report deltas.
        reported_filled = max(
            Decimal("0"),
            resp.filled_quantity or Decimal("0"),
            resp.fill_delta or Decimal("0"),
        )
        filled_limit = min(close_qty, position.quantity)
        if reported_filled > filled_limit:
            logger.error(
                "Close order %s reported %s filled for a request of %s; capping locally",
                resp.order_id,
                reported_filled,
                filled_limit,
            )
            resp.filled_quantity = min(resp.filled_quantity or Decimal("0"), filled_limit)
            resp.fill_delta = min(resp.fill_delta or Decimal("0"), filled_limit)

        filled = Decimal("0")
        if resp.fill_delta > 0 or (resp.filled_quantity or Decimal("0")) > 0:
            filled = await self.position_manager.record_fill(position_id, resp, "exit")
        if filled <= 0:
            logger.warning(
                "Close order %s has no confirmed fill (status=%s); keeping position open",
                resp.order_id,
                resp.status,
            )
            return CloseResult(
                order_ids=[resp.order_id],
                requested_quantity=close_qty,
                filled_quantity=Decimal("0"),
                remaining_quantity=position.quantity,
                fully_closed=False,
                realized_pnl=self.position_manager.realized_pnl(position),
                status=str(resp.status).lower(),
            )

        remaining = max(Decimal("0"), position.quantity - min(filled, position.quantity))
        if remaining <= 0:
            await self._cancel_protective_orders(position)
            await self.position_manager.close_position(position_id, reason)
            fully_closed = await self.position_manager.get_position(position_id) is None
            if not fully_closed:
                # Persistence failure leaves the position in memory for
                # reconciliation.  Do not report a confirmed full close.
                remaining = position.quantity
        else:
            await self.position_manager.update_position_quantity(position_id, remaining)
            fully_closed = False

        return CloseResult(
            order_ids=[resp.order_id],
            requested_quantity=close_qty,
            filled_quantity=filled,
            remaining_quantity=remaining,
            fully_closed=fully_closed,
            realized_pnl=self.position_manager.realized_pnl(position),
            status=str(resp.status).lower(),
        )

    async def adjust_breakeven(
        self, position_id: str, trigger_pnl_percent: Decimal = Decimal("2")
    ) -> None:
        position = await self.position_manager.get_position(position_id)
        if not position:
            return
        threshold = position.quantity * position.entry_price * trigger_pnl_percent / Decimal("100")
        if position.current_pnl < threshold:
            return
        current_price = await self._get_current_price(position.symbol)
        if current_price is not None and not self._is_sl_valid_for_position(
            position.side, position.entry_price, current_price
        ):
            logger.warning(
                "Breakeven SL %s would trigger immediately (market=%s, side=%s, symbol=%s); skipping",
                position.entry_price, current_price, position.side, position.symbol,
            )
            return
        if position.sl_order_id:
            await self.cancel_order(position.sl_order_id, position.symbol)
        request = OrderRequest(
            symbol=position.symbol,
            side="sell" if position.side == "buy" else "buy",
            type="stop_loss",
            quantity=position.quantity,
            stopPrice=position.entry_price,
        )
        resp = await self._place_order_with_retry(request)
        self.open_orders[resp.order_id] = resp
        await self._persist_order(resp, position_id)
        await self.position_manager.update_position_sl(
            position_id, position.entry_price, resp.order_id
        )

    async def update_trailing_stop(
        self, position_id: str, trail_percent: Decimal = Decimal("0.5")
    ) -> None:
        position = await self.position_manager.get_position(position_id)
        if not position or not position.trailing_enabled:
            return
        ticker = await asyncio.to_thread(self.exchange.fetch_ticker, position.symbol)
        current = ticker.price
        if position.side == "buy":
            new_sl = current * (Decimal("1") - trail_percent / 100)
            improves = position.sl_price is None or new_sl > position.sl_price
        else:
            new_sl = current * (Decimal("1") + trail_percent / 100)
            improves = position.sl_price is None or new_sl < position.sl_price
        if not improves:
            return
        if not self._is_sl_valid_for_position(position.side, new_sl, current):
            logger.warning(
                "Trailing SL %s would trigger immediately (market=%s, side=%s, symbol=%s); skipping",
                new_sl, current, position.side, position.symbol,
            )
            return
        if position.sl_order_id:
            await self.cancel_order(position.sl_order_id, position.symbol)
        request = OrderRequest(
            symbol=position.symbol,
            side="sell" if position.side == "buy" else "buy",
            type="stop_loss",
            quantity=position.quantity,
            stopPrice=new_sl,
        )
        resp = await self._place_order_with_retry(request)
        self.open_orders[resp.order_id] = resp
        await self._persist_order(resp, position_id)
        await self.position_manager.update_position_sl(
            position_id, new_sl, resp.order_id
        )

    async def _place_order_with_retry(self, order: OrderRequest) -> OrderResponse:
        """Submit one logical order and reconcile ambiguous submissions.

        The request receives a stable client order id before the first network
        call. If create_order raises after the exchange may have accepted it,
        the manager searches for that exact client id. It only raises the
        ambiguous-outcome error when reconciliation cannot prove what happened;
        it never blindly submits the order a second time.
        """
        if not order.client_order_id:
            order.client_order_id = "ats-" + uuid.uuid4().hex[:17]
        key = order.client_order_id
        try:
            response = await asyncio.to_thread(self.exchange.create_order, order)
            self.retry_counts.pop(key, None)
            if not response.symbol:
                response.symbol = order.symbol
            if response.quantity is None:
                response.quantity = order.quantity
            if response.side is None:
                response.side = order.side
            if response.order_type is None:
                response.order_type = order.type
            if not response.client_order_id:
                response.client_order_id = order.client_order_id
            if response.price is None:
                response.price = order.price or order.stopPrice
            if response.created_at <= 0:
                response.created_at = time.time()
            return response
        except Exception as exc:
            self.retry_counts[key] = 1

            # Local validation failures happen before a network order can be
            # submitted.  They are definitive and must never enter the
            # ambiguous client-id reconciliation path.
            if isinstance(exc, ValueError):
                self.retry_counts.pop(key, None)
                logger.error(
                    "Order rejected before submission for %s/%s: %s",
                    order.symbol, order.type, exc,
                )
                raise

            # InsufficientFunds is a definitive rejection from the exchange,
            # not an ambiguous network outcome. Do not perform client-id
            # reconciliation for a request Binance explicitly rejected.
            if exc.__class__.__name__ == "InsufficientFunds":
                logger.error(
                    "Order rejected for %s/%s: insufficient exchange balance: %s",
                    order.symbol,
                    order.type,
                    exc,
                )
                raise RuntimeError(
                    f"Order rejected: insufficient exchange balance: {exc}"
                ) from exc

            logger.error(
                "Ambiguous order submission for %s/%s: %s. "
                "Attempting reconciliation by client order id %s.",
                order.symbol,
                order.type,
                exc,
                key,
            )
            finder = getattr(self.exchange, "find_order_by_client_id", None)
            if callable(finder):
                try:
                    recovered = await asyncio.to_thread(
                        finder, order.symbol, key
                    )
                except Exception as reconcile_exc:
                    logger.warning(
                        "Order reconciliation failed for client id %s: %s",
                        key,
                        reconcile_exc,
                    )
                    recovered = None
                if recovered is not None:
                    if not recovered.symbol:
                        recovered.symbol = order.symbol
                    if recovered.client_order_id is None:
                        recovered.client_order_id = key
                    if recovered.quantity is None:
                        recovered.quantity = order.quantity
                    if recovered.side is None:
                        recovered.side = order.side
                    if recovered.order_type is None:
                        recovered.order_type = order.type
                    logger.warning(
                        "Recovered previously accepted order %s using client id %s",
                        recovered.order_id,
                        key,
                    )
                    return recovered
            raise RuntimeError(
                f"Order submission outcome is unknown; automatic retry disabled: {exc}"
            ) from exc

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            result = await asyncio.to_thread(self.exchange.cancel_order, symbol, order_id)
            if result:
                existing = self.open_orders.get(order_id)
                if existing:
                    existing.status = "canceled"
                    await self._persist_order(existing)
                self.open_orders.pop(order_id, None)
            return bool(result)
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    async def _order_monitor_iteration(self):
        for order_id, order in list(self.open_orders.items()):
            symbol = order.symbol
            if not symbol:
                continue
            updated = await self._fetch_order_status(order_id, symbol, current=order)
            previous_filled = order.filled_quantity or Decimal("0")
            cumulative_filled = updated.filled_quantity or Decimal("0")
            updated.fill_delta = max(Decimal("0"), cumulative_filled - previous_filled)
            if updated.filled_quantity is None:
                updated.filled_quantity = previous_filled
            if order.created_at > 0 and (updated.created_at <= 0 or updated.created_at > order.created_at):
                updated.created_at = order.created_at
            if updated.price is None:
                updated.price = order.price
            self.open_orders[order_id] = updated
            await self._persist_order(updated)
            position_before_update = await self.position_manager.get_position_by_order(order_id)
            changed = (updated.status != order.status or updated.filled_quantity != order.filled_quantity)
            if changed:
                await self.position_manager.on_order_update(order_id, updated)
                if updated.status in {"closed", "filled"} and position_before_update is not None:
                    if updated.order_type == "take_profit":
                        position_after_update = await self.position_manager.get_position(position_before_update.position_id)
                        if position_after_update is not None and position_after_update.quantity > 0:
                            # Identify the TP level before PositionManager removes
                            # a terminal order from tp_order_ids.
                            tp_index = None
                            for idx, key in enumerate(
                                [f"tp{i}" for i in range(len(position_before_update.tp_prices))]
                            ):
                                if position_before_update.protective_client_ids.get(key) == updated.client_order_id:
                                    tp_index = idx
                                    break
                            fully_filled = (
                                updated.status in {"closed", "filled"}
                                and updated.quantity is not None
                                and updated.filled_quantity >= updated.quantity
                            )
                            if self._is_spot:
                                # Spot staged: advance to next TP + re-place SL
                                await self._advance_spot_staged_tp(
                                    position_after_update,
                                    filled_tp_index=tp_index,
                                )
                            else:
                                # Futures: resize TPs FIRST (they reserve margin),
                                # then resize SL with remaining.
                                await self._resize_stop_loss(position_after_update)
                                await self._resize_take_profits(
                                    position_after_update,
                                    exclude_tp_index=tp_index if fully_filled else None,
                                )
                        elif position_after_update is None:
                            await self._cancel_protective_orders(position_before_update, exclude={order_id})
                    elif updated.order_type == "stop_loss":
                        await self._cancel_protective_orders(position_before_update, exclude={order_id})
            if updated.status in {"closed", "filled", "canceled", "cancelled", "expired", "rejected"}:
                self.open_orders.pop(order_id, None)

    async def _order_monitor_loop(self):
        iteration = 0
        while True:
            try:
                await self._order_monitor_iteration()
                await self._cleanup_stuck_orders()
                # Every 60 iterations (~5 min), reconcile exchange orders with tracked state
                iteration += 1
                if iteration % 60 == 0:
                    await self._reconcile_exchange_orders()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Order monitor loop error: %s", exc)
                await asyncio.sleep(10)

    async def _reconcile_exchange_orders(self) -> None:
        """Fetch open orders from exchange and reconcile with tracked state.

        This catches fills that were missed by the order monitor (e.g., during
        a brief network issue) and ensures all protective orders are tracked.
        """
        if self.exchange is None:
            return
        symbols_seen: set = set()
        for order in list(self.open_orders.values()):
            if order.symbol:
                symbols_seen.add(order.symbol)
        for position in self.position_manager.positions.values():
            if position.status == "OPEN":
                symbols_seen.add(position.symbol)
        for symbol in symbols_seen:
            try:
                exchange_orders = await asyncio.to_thread(
                    self.exchange.fetch_open_orders, symbol
                )
            except Exception:
                continue
            exchange_ids = set()
            for eo in (exchange_orders or []):
                if eo.order_id:
                    exchange_ids.add(eo.order_id)
                # Track any protective order found on exchange but not tracked
                if eo.order_id and eo.order_id not in self.open_orders:
                    position = await self.position_manager.get_position_by_order(eo.order_id)
                    if position is not None and position.status == "OPEN":
                        self.open_orders[eo.order_id] = eo
                        await self._persist_order(eo, position.position_id)
            # Check tracked orders that are no longer on exchange (may have filled)
            for order_id in list(self.open_orders.keys()):
                order = self.open_orders.get(order_id)
                if order is None or order.symbol != symbol:
                    continue
                if order.status not in {"open", "partial"}:
                    continue
                if order_id not in exchange_ids:
                    # Order not in open orders — may have filled
                    updated = await self._fetch_order_status(order_id, symbol, current=order)
                    if updated.status != order.status:
                        await self.position_manager.on_order_update(order_id, updated)
                    if updated.status in {"closed", "filled", "canceled", "cancelled", "expired", "rejected"}:
                        self.open_orders.pop(order_id, None)

    async def _fetch_order_status(self, order_id: str, symbol: str, current: Optional[OrderResponse] = None) -> OrderResponse:
        try:
            return await asyncio.to_thread(self.exchange.fetch_order, symbol, order_id)
        except Exception as exc:
            logger.debug("fetch_order unavailable/failed for %s: %s; checking open orders", order_id, exc)
        try:
            orders = await asyncio.to_thread(self.exchange.fetch_open_orders, symbol)
        except Exception as exc:
            logger.warning("Unable to reconcile order %s: %s", order_id, exc)
            return current or OrderResponse(order_id=order_id, status="open", symbol=symbol)
        for order in orders:
            if order.order_id == order_id:
                return order
        # Missing from open orders does NOT prove a fill. Preserve the last
        # known state and let the stuck-order timeout handle cancellation.
        return current or OrderResponse(order_id=order_id, status="open", symbol=symbol)

    async def _resize_stop_loss(self, position) -> None:
        """Keep the protective stop quantity equal to the remaining position."""
        if not position.sl_price or position.quantity <= 0:
            return
        current_price = await self._get_current_price(position.symbol)
        if current_price is not None and not self._is_sl_valid_for_position(
            position.side, position.sl_price, current_price
        ):
            logger.warning(
                "Resize SL %s would trigger immediately (market=%s, side=%s, symbol=%s); skipping",
                position.sl_price, current_price, position.side, position.symbol,
            )
            return
        old_sl_id = position.sl_order_id
        if old_sl_id:
            await self.cancel_order(old_sl_id, position.symbol)
        client_id = position.protective_client_ids.get("sl") or self._protective_client_id(position.position_id, "sl")
        position.protective_client_ids["sl"] = client_id
        await self.position_manager._persist_position(position)
        free_base = await self._get_free_base_balance(position.symbol) if position.side == "buy" else Decimal("0")
        sl_qty = min(position.quantity, free_base) if self._is_spot else position.quantity
        request = OrderRequest(
            symbol=position.symbol,
            side="sell" if position.side == "buy" else "buy",
            type="stop_loss",
            quantity=sl_qty,
            stopPrice=position.sl_price,
            client_order_id=client_id,
        )
        response = await self._place_order_with_retry(request)
        self.open_orders[response.order_id] = response
        await self._persist_order(response, position.position_id)
        await self.position_manager.update_position_sl(
            position.position_id, position.sl_price, response.order_id
        )

    async def _resize_take_profits(self, position, exclude_tp_index: Optional[int] = None) -> None:
        """Rebuild remaining TP quantities after a partial/filled TP.

        Multiple TP orders are initially sized from the entry quantity. After
        one TP partially fills, leaving the other orders unchanged can make
        their total quantity exceed the remaining position and potentially
        reverse the Futures position.  Cancel all linked TP orders first, then
        recreate only the still-relevant levels against the current quantity.
        """
        if position.quantity <= 0 or not position.tp_prices:
            return
        current_ids = list(position.tp_order_ids)
        for order_id in current_ids:
            if not order_id:
                continue
            if not await self.cancel_order(order_id, position.symbol):
                raise RuntimeError(f"Unable to cancel TP order {order_id} before resize")

        position.tp_order_ids = []
        active_indices = [
            idx for idx in range(len(position.tp_prices))
            if idx != exclude_tp_index
        ]
        if not active_indices:
            await self.position_manager._persist_position(position)
            return

        opposite = "sell" if position.side == "buy" else "buy"
        free_base = await self._get_free_base_balance(position.symbol) if position.side == "buy" else Decimal("0")
        remaining = min(position.quantity, free_base) if self._is_spot else position.quantity
        base_qty = remaining / Decimal(len(active_indices))
        current_price = await self._get_current_price(position.symbol)
        for n, idx in enumerate(active_indices):
            qty = remaining - base_qty * n if n == len(active_indices) - 1 else base_qty
            if qty <= 0:
                continue
            if current_price is not None and not self._is_tp_valid_for_position(
                position.side, position.tp_prices[idx], current_price
            ):
                logger.warning(
                    "Resize TP %s would trigger immediately (market=%s, side=%s, symbol=%s); "
                    "skipping TP level %d",
                    position.tp_prices[idx], current_price, position.side, position.symbol, idx,
                )
                continue
            key = f"tp{idx}"
            client_id = position.protective_client_ids.get(key) or self._protective_client_id(position.position_id, "tp", idx)
            position.protective_client_ids[key] = client_id
            req = OrderRequest(
                symbol=position.symbol, side=opposite, type="take_profit",
                quantity=qty, price=position.tp_prices[idx],
                stopPrice=position.tp_prices[idx], client_order_id=client_id,
            )
            resp = await self._place_order_with_retry(req)
            self.open_orders[resp.order_id] = resp
            position.tp_order_ids.append(resp.order_id)
            await self._persist_order(resp, position.position_id)
        await self.position_manager._persist_position(position)

    async def _advance_spot_staged_tp(self, position, filled_tp_index: Optional[int] = None) -> None:
        """Advance to the next staged TP after a fill on Spot.

        When a TP fills:
          1. Cancel the current SL (it covered the old position).
          2. Mark the filled TP index.
          3. Place the next TP (if any remain).
          4. Place a new SL covering the reduced position.
        """
        if position.quantity <= 0:
            return
        opposite = "sell" if position.side == "buy" else "buy"

        # Cancel the existing SL
        if position.sl_order_id:
            await self.cancel_order(position.sl_order_id, position.symbol)
            position.sl_order_id = None

        # Mark the filled TP
        if filled_tp_index is not None:
            if not hasattr(position, "_filled_tp_indices"):
                position._filled_tp_indices = set()
            position._filled_tp_indices.add(filled_tp_index)

        # Find next unfilled TP
        next_tp = None
        for idx in range(len(position.tp_prices)):
            if idx not in position._filled_tp_indices:
                next_tp = idx
                break

        # Cancel any remaining TP orders (they have wrong quantities)
        current_tp_ids = list(position.tp_order_ids)
        for oid in current_tp_ids:
            if oid:
                await self.cancel_order(oid, position.symbol)
        position.tp_order_ids = []

        # Place next staged TP + new SL
        if next_tp is not None:
            await self._place_spot_staged_tp(position, next_tp, opposite, [])
        await self._place_spot_staged_sl(position, opposite, [])
        await self.position_manager._persist_position(position)

    async def _cancel_protective_orders(self, position, exclude: Optional[set[str]] = None) -> None:
        exclude = exclude or set()
        order_ids = [position.sl_order_id, *position.tp_order_ids]
        for order_id in order_ids:
            if not order_id or order_id in exclude:
                continue
            # The in-memory registry can be empty after a restart.  Always
            # attempt cancellation for linked protective orders; the exchange
            # adapter safely reports "not found" when already terminal.
            await self.cancel_order(order_id, position.symbol)

    async def _cleanup_stuck_orders(self):
        now = time.time()
        timeout = settings.STUCK_ORDER_TIMEOUT_SECONDS
        for order_id, order in list(self.open_orders.items()):
            if order.status not in {"open", "partial"}:
                continue
            # Protective orders (SL/TP linked to a live position) must never
            # be canceled by the stuck-order cleanup.  After a restart the
            # in-memory OrderResponse defaults created_at=0, making recovered
            # orders appear infinitely old.  The order monitor will reconcile
            # their real exchange status; the stuck timeout is only for
            # non-protective orders that have no position linkage.
            position = await self.position_manager.get_position_by_order(order_id)
            if position is not None and position.status == "OPEN":
                continue
            age = now - (order.created_at or now)
            if age >= timeout:
                await self.cancel_order(order_id, order.symbol)
                logger.warning("Canceled stuck order %s after %.1fs", order_id, age)

    async def _persist_order(self, order: OrderResponse, position_id: Optional[str] = None):
        if not self.db_session_factory or not order.order_id:
            return
        try:
            exchange_name = self.exchange.get_exchange_name()
            status = str(order.status).lower()
            if status == "cancelled":
                status = "canceled"
            # CCXT uses ``closed`` for a fully filled order.  ``filled`` is
            # also accepted by some adapters/mocks, but must never be
            # persisted as ``open``.  Rejected is terminal as well.
            if status == "filled":
                status = "closed"
            if status not in {x.value for x in OrderStatusEnum}:
                status = "open"
            values = dict(
                exchange=exchange_name,
                symbol=order.symbol or "",
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                side=order.side or "buy",
                order_type=order.order_type or "market",
                quantity=order.quantity or Decimal("0"),
                price=order.price or order.avg_price,
                status=OrderStatusEnum(status),
                filled_quantity=order.filled_quantity,
                avg_price=order.avg_price,
                fee_cost=order.fee_cost,
                fee_currency=order.fee_currency,
            )
            async with self.db_session_factory() as session:
                existing = None
                if self._is_uuid(order.order_id):
                    existing = await session.get(OrderModel, uuid.UUID(order.order_id))
                if existing is None:
                    from sqlalchemy import select
                    result = await session.execute(
                        select(OrderModel).where(
                            OrderModel.exchange == exchange_name,
                            OrderModel.order_id == order.order_id,
                        )
                    )
                    existing = result.scalar_one_or_none()
                if existing:
                    for key, value in values.items():
                        setattr(existing, key, value)
                    if position_id and existing.position_id is None:
                        existing.position_id = uuid.UUID(position_id)
                else:
                    session.add(
                        OrderModel(
                            id=uuid.uuid4(),
                            position_id=uuid.UUID(position_id) if position_id else None,
                            **values,
                        )
                    )
                await session.commit()
        except Exception as exc:
            logger.error("Order persistence failed for %s: %s", order.order_id, exc)

    @staticmethod
    def _is_uuid(value: str) -> bool:
        try:
            uuid.UUID(value)
            return True
        except (ValueError, TypeError, AttributeError):
            return False
