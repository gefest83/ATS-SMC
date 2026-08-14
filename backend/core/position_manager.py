"""Position lifecycle, persistence and recovery."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import select

from backend.config import settings
from backend.core.exchange.base import Exchange, OrderResponse, PositionData
from backend.db.models import Position as PositionModel, PositionStatusEnum, Trade

logger = logging.getLogger(__name__)


class Position:
    def __init__(
        self,
        position_id: str,
        exchange: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        sl_price: Optional[Decimal] = None,
        tp_prices: Optional[List[Decimal]] = None,
        strategy: str = "unknown",
    ):
        self.position_id = position_id
        self.exchange = exchange
        self.symbol = symbol
        self.side = side.lower()
        self.quantity = Decimal(quantity)
        self.initial_quantity = Decimal(quantity)
        self.entry_price = Decimal(entry_price)
        self.sl_price = Decimal(sl_price) if sl_price is not None else None
        self.tp_prices = [Decimal(x) for x in (tp_prices or [])]
        self.strategy = strategy
        self.entry_time = time.time()
        self.status = "OPEN"
        self.trailing_enabled = False
        self.breakeven_enabled = False
        self.trail_stop_price = None
        self.current_pnl = Decimal("0")
        self.current_rr = Decimal("0")
        self.risk_percent = Decimal("0")
        self.sl_order_id = None
        self.tp_order_ids: List[str] = []
        self.entry_order_id = None
        self.close_reason = None
        self.close_time = None
        self.entry_cost = Decimal("0")
        self.entry_fee_quote = Decimal("0")
        self.exit_notional = Decimal("0")
        self.exit_quantity = Decimal("0")
        self.exit_fee_quote = Decimal("0")
        self.fee_unconverted = Decimal("0")
        self.fee_currencies = set()
        self.fill_progress: Dict[str, Decimal] = {}
        self.reconciliation_pending = False
        self.reconciliation_message = None
        self.reconciliation_trade_fingerprint = None
        self.protective_client_ids: Dict[str, str] = {}
        self._filled_tp_indices: set = set()

    def update_quantity(self, new_qty: Decimal) -> None:
        self.quantity = Decimal(new_qty)

    def set_sl(self, sl_price: Decimal, sl_order_id: str) -> None:
        self.sl_price = Decimal(sl_price)
        self.sl_order_id = sl_order_id

    def mark_closed(self, reason: str) -> None:
        self.status = "CLOSED"
        self.close_reason = reason
        self.close_time = time.time()


class PositionManager:
    def __init__(
        self,
        db_session_factory=None,
        exchange_name: str = "unknown",
        exchange: Optional[Exchange] = None,
        on_position_closed=None,
        symbol: Optional[str] = None,
    ):
        self.db_session_factory = db_session_factory
        self.exchange_name = exchange_name
        self.exchange = exchange
        # A symbol-scoped bot must recover only its own persisted positions.
        # Leaving this unset preserves the existing manager-wide behavior for
        # callers that intentionally manage multiple symbols.
        self.symbol = symbol
        self.positions: Dict[str, Position] = {}
        # Serialize local close/recovery transitions. DB uniqueness also protects
        # against duplicate Trade rows across multiple processes.
        self._close_locks: Dict[str, asyncio.Lock] = {}
        self._sync_task: Optional[asyncio.Task] = None
        self.on_position_closed = on_position_closed
        # Track when reconciliation started for positions missing on exchange.
        # Close automatically after RECONCILIATION_TIMEOUT_SECONDS to prevent
        # indefinite drift between local state and exchange reality.
        self._reconciliation_start: Dict[str, float] = {}
        self.RECONCILIATION_TIMEOUT_SECONDS = 120

    async def start(self) -> int:
        await self.recover_open_positions()
        if self._sync_task is None:
            self._sync_task = asyncio.create_task(self._recovery_sync_loop())
        return len(self.positions)

    async def stop(self):
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

    async def create_position(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        sl_price: Optional[Decimal] = None,
        tp_prices: Optional[List[Decimal]] = None,
        entry_order_id: Optional[str] = None,
        sl_order_id: Optional[str] = None,
        tp_order_ids: Optional[List[str]] = None,
        strategy: str = "unknown",
        position_id: Optional[str] = None,
        exchange: Optional[str] = None,
    ) -> str:
        position_id = position_id or str(uuid.uuid4())
        position = Position(
            position_id,
            exchange or self.exchange_name,
            symbol,
            side,
            quantity,
            entry_price,
            sl_price,
            tp_prices,
            strategy,
        )
        position.entry_order_id = entry_order_id
        position.sl_order_id = sl_order_id
        position.tp_order_ids = list(tp_order_ids or [])
        self.positions[position_id] = position
        await self._persist_position(position)
        return position_id

    async def get_position(self, position_id: str) -> Optional[Position]:
        return self.positions.get(position_id)

    async def get_position_by_order(self, order_id: str) -> Optional[Position]:
        for position in self.positions.values():
            if order_id in {position.entry_order_id, position.sl_order_id, *position.tp_order_ids}:
                return position
        return None

    async def update_position_quantity(self, position_id: str, new_quantity: Decimal):
        pos = self.positions.get(position_id)
        if not pos:
            return
        pos.update_quantity(new_quantity)
        if pos.quantity <= 0:
            await self.close_position(position_id, "quantity_depleted")
        else:
            await self._persist_position(pos)

    async def update_position_sl(self, position_id: str, new_sl: Decimal, sl_order_id: str):
        pos = self.positions.get(position_id)
        if pos:
            pos.set_sl(new_sl, sl_order_id)
            await self._persist_position(pos)

    @staticmethod
    def _quote_currency(symbol: str) -> str:
        if ":" in symbol:
            return symbol.split(":", 1)[1].upper()
        if "/" in symbol:
            return symbol.split("/", 1)[1].upper()
        return settings.FUTURES_SETTLE_ASSET.upper()

    def _effective_fill_delta(self, position: Position, order: OrderResponse) -> Decimal:
        """Return only newly executed quantity, including after restart.

        CCXT's ``filled`` value is cumulative.  ``fill_delta`` is normally
        supplied by OrderManager, but after a restart that in-memory baseline
        is gone.  Persisting per-order progress lets recovery remain idempotent.
        """
        order_id = str(order.order_id)
        previous = position.fill_progress.get(order_id, Decimal("0"))
        cumulative = order.filled_quantity or Decimal("0")
        delta_from_cumulative = max(Decimal("0"), cumulative - previous)
        delta = max(Decimal("0"), order.fill_delta or Decimal("0"), delta_from_cumulative)
        position.fill_progress[order_id] = max(previous, cumulative, previous + delta)
        return delta

    async def record_fill(self, position_id: str, order: OrderResponse, role: str) -> Decimal:
        """Accumulate executed notional/fees exactly once per new fill delta."""
        pos = self.positions.get(position_id)
        if not pos:
            return Decimal("0")
        qty = self._effective_fill_delta(pos, order)
        if qty <= 0:
            await self._persist_position(pos)
            return Decimal("0")
        price = order.avg_price or order.price or pos.entry_price
        quote = self._quote_currency(pos.symbol)
        fee_items = list(getattr(order, "fee_items", []) or [])
        if not fee_items and order.fee_cost is not None:
            # Backward-compatible fallback for adapters that only populate
            # the aggregate fields.
            fee_items = [(order.fee_cost, (order.fee_currency or "").upper())]
        for fee, currency in fee_items:
            currency = (currency or "").upper()
            if currency:
                pos.fee_currencies.add(currency)
            # A missing currency is treated as settlement/quote fee, but a
            # multi-currency response is no longer collapsed into one value.
            effective_currency = currency or quote
            if effective_currency == quote:
                if role == "entry":
                    pos.entry_fee_quote += fee
                else:
                    pos.exit_fee_quote += fee
            else:
                pos.fee_unconverted += fee
        if role == "entry":
            pos.entry_cost += qty * price
            if pos.initial_quantity > 0:
                pos.entry_price = pos.entry_cost / pos.initial_quantity
        else:
            pos.exit_quantity += qty
            pos.exit_notional += qty * price
        await self._persist_position(pos)
        return qty

    def realized_pnl(self, position: Position) -> Decimal:
        if position.exit_quantity <= 0:
            return Decimal("0")
        if position.side == "buy":
            gross = position.exit_notional - (position.entry_price * position.exit_quantity)
        else:
            gross = (position.entry_price * position.exit_quantity) - position.exit_notional
        return gross - position.entry_fee_quote - position.exit_fee_quote

    async def close_position(self, position_id: str, reason: str):
        lock = self._close_locks.setdefault(position_id, asyncio.Lock())
        async with lock:
            pos = self.positions.get(position_id)
            if not pos:
                return
            if pos.status == "CLOSED":
                return

            pos.mark_closed(reason)
            persisted = await self._persist_trade(pos, reason)

            if not persisted:
                # Do not delete an in-memory position when the DB transaction
                # failed. Keep it OPEN so restart/recovery can reconcile it
                # against exchange fills instead of losing the close event.
                pos.status = "OPEN"
                logger.error(
                    "Close transaction failed for %s; keeping position pending recovery",
                    position_id,
                )
                return

            self.positions.pop(position_id, None)
            self._close_locks.pop(position_id, None)
            if self.on_position_closed:
                try:
                    result = self.on_position_closed(pos, reason)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("Position close callback failed for %s", position_id)

    async def on_order_update(self, order_id: str, order: OrderResponse):
        terminal = {"closed", "filled", "canceled", "cancelled", "expired", "rejected"}
        for pos in list(self.positions.values()):
            linked = {pos.entry_order_id, pos.sl_order_id, *pos.tp_order_ids}
            if order_id not in linked:
                continue
            if order_id == pos.entry_order_id:
                await self.record_fill(pos.position_id, order, "entry")
                continue
            if order_id == pos.sl_order_id:
                if order.fill_delta > 0 or (order.filled_quantity or Decimal("0")) > pos.fill_progress.get(order_id, Decimal("0")):
                    filled = await self.record_fill(pos.position_id, order, "exit")
                    if filled > 0:
                        remaining = max(Decimal("0"), pos.quantity - filled)
                        if remaining <= 0:
                            await self.close_position(pos.position_id, "stop_loss")
                        else:
                            pos.update_quantity(remaining)
                            await self._persist_position(pos)
                # A terminal status without a newly observed fill is not proof
                # that the whole position was closed. Never fabricate a fill.
                continue
            if order_id in pos.tp_order_ids:
                filled = Decimal("0")
                if order.fill_delta > 0 or (order.filled_quantity or Decimal("0")) > pos.fill_progress.get(order_id, Decimal("0")):
                    filled = await self.record_fill(pos.position_id, order, "exit")
                if filled > 0:
                    remaining = max(Decimal("0"), pos.quantity - filled)
                    if remaining <= 0:
                        await self.close_position(pos.position_id, "take_profit")
                    else:
                        pos.update_quantity(remaining)
                        if order.status in terminal:
                            pos.tp_order_ids = [oid for oid in pos.tp_order_ids if oid != order_id]
                        await self._persist_position(pos)
                elif order.status in terminal:
                    # Terminal with no new fill: remove the order reference,
                    # but keep the position untouched.
                    pos.tp_order_ids = [oid for oid in pos.tp_order_ids if oid != order_id]
                    await self._persist_position(pos)
                continue
            if order.status in terminal:
                await self._persist_position(pos)

    async def _reconcile_from_trade_history(self, position: Position) -> bool:
        """Rebuild exit fills from private trade history when state diverges.

        The exchange position is the source of truth for current exposure, but
        it cannot explain a missing position by itself.  Private fills provide
        the evidence needed to distinguish a real close from a transient API
        discrepancy or a manual intervention.  The reconstruction is
        idempotent via a fingerprint persisted with the position.
        """
        if not self.exchange or not hasattr(self.exchange, "fetch_my_trades"):
            return False
        since = max(0, int(position.entry_time * 1000) - 60_000)
        trades = await asyncio.to_thread(self.exchange.fetch_my_trades, position.symbol, since, 1000)
        if not trades:
            return False

        entry_side = position.side.lower()
        exit_side = "sell" if entry_side == "buy" else "buy"
        entry_qty = Decimal("0")
        entry_cost = Decimal("0")
        entry_fee_quote = Decimal("0")
        exit_qty = Decimal("0")
        exit_notional = Decimal("0")
        exit_fee_quote = Decimal("0")
        unconverted = Decimal("0")
        currencies = set()
        evidence = []
        quote = self._quote_currency(position.symbol)

        for trade in trades:
            if not isinstance(trade, dict):
                continue
            trade_symbol = trade.get("symbol")
            if trade_symbol and trade_symbol != position.symbol:
                # Futures exchanges can return the unified :USDT symbol while
                # the stored position uses the pre-normalized symbol.
                if self.exchange.normalize_symbol(str(trade_symbol)) != self.exchange.normalize_symbol(position.symbol):
                    continue
            timestamp = int(trade.get("timestamp") or 0)
            # Do not pull fills from before this position was opened.  The
            # fetch window intentionally starts 60s early to tolerate clock
            # skew, but older fills for the same symbol must not contaminate
            # the reconstructed entry/exit quantities or PnL.
            entry_timestamp_ms = int(position.entry_time * 1000)
            if timestamp and timestamp < entry_timestamp_ms:
                continue
            side = str(trade.get("side") or "").lower()
            amount = Decimal(str(trade.get("amount") or 0))
            price = Decimal(str(trade.get("price") or 0))
            if amount <= 0 or price <= 0 or side not in {entry_side, exit_side}:
                continue
            raw_fees = trade.get("fee")
            if raw_fees is None:
                raw_fees = trade.get("fees")
            fee_items = raw_fees if isinstance(raw_fees, list) else [raw_fees]
            normalized_fees = []
            for fee in fee_items:
                if not isinstance(fee, dict) or fee.get("cost") is None:
                    continue
                try:
                    fee_cost = Decimal(str(fee.get("cost")))
                except (TypeError, ValueError, ArithmeticError):
                    continue
                fee_currency = str(fee.get("currency") or "").upper()
                normalized_fees.append((fee_cost, fee_currency))

            trade_id = str(trade.get("id") or trade.get("order") or f"{timestamp}:{side}:{amount}:{price}")
            fee_evidence = ",".join(f"{cost}:{currency}" for cost, currency in normalized_fees)
            evidence.append(f"{trade_id}:{side}:{amount}:{price}:{fee_evidence}")
            for fee_cost, fee_currency in normalized_fees:
                if fee_currency:
                    currencies.add(fee_currency)
                # A CCXT fee without an explicit currency is treated as the
                # settlement/quote fee, matching OrderResponse.record_fill.
                # Never subtract a fee in an unknown currency from quote PnL.
                effective_currency = fee_currency or quote
                if side == entry_side:
                    if effective_currency == quote:
                        entry_fee_quote += fee_cost
                    elif fee_cost:
                        unconverted += fee_cost
                else:
                    if effective_currency == quote:
                        exit_fee_quote += fee_cost
                    elif fee_cost:
                        unconverted += fee_cost

            if side == entry_side:
                entry_qty += amount
                entry_cost += amount * price
            else:
                exit_qty += amount
                exit_notional += amount * price

        if not evidence or entry_qty <= 0:
            return False
        fingerprint = "|".join(sorted(evidence))
        if fingerprint == position.reconciliation_trade_fingerprint:
            return False

        position.entry_cost = entry_cost
        position.entry_fee_quote = entry_fee_quote
        position.entry_price = entry_cost / entry_qty
        position.initial_quantity = max(position.initial_quantity, entry_qty)
        position.exit_quantity = min(exit_qty, position.initial_quantity)
        position.exit_notional = exit_notional
        position.exit_fee_quote = exit_fee_quote
        position.fee_unconverted = unconverted
        position.fee_currencies = currencies
        position.quantity = max(Decimal("0"), position.initial_quantity - position.exit_quantity)
        position.reconciliation_trade_fingerprint = fingerprint

        if position.quantity <= 0:
            await self.close_position(position.position_id, "exchange_trade_reconciliation")
        else:
            position.reconciliation_pending = True
            position.reconciliation_message = (
                f"trade history reconstructed {position.exit_quantity} exited; "
                f"{position.quantity} remains"
            )
            await self._persist_position(position)
        return True

    async def sync_with_exchange(self) -> None:
        """Reconcile testnet/live positions with the exchange.

        For futures: compare remote position quantity against local state.
        For spot: verify the base asset still exists. If total base is zero
        and no protective orders remain, the position was closed externally.
        """
        if not self.exchange or settings.TRADING_MODE.lower() not in {"testnet", "live"}:
            return
        if settings.EXCHANGE_MARKET_TYPE.lower() == "futures":
            await self._sync_futures_positions()
        else:
            await self._sync_spot_positions()

    async def _sync_spot_positions(self) -> None:
        """Spot: detect positions closed externally (SL/TP fill, manual close).

        A spot position is considered closed when:
        1. (BUY only) The total base balance (free + used) is zero, AND
           There are no open protective sell orders.
        2. (SELL only) There are no open protective buy orders AND the
           position has been open for more than 15 minutes.

        If protective orders remain, the position may still be closing.
        """
        try:
            raw = await asyncio.to_thread(self.exchange.exchange.fetch_balance)
        except Exception as exc:
            logger.debug("Spot sync: balance fetch failed: %s", exc)
            return

        for position_id, pos in list(self.positions.items()):
            if pos.status != "OPEN":
                continue

            # For BUY positions: check base balance
            if pos.side == "buy":
                base_asset = pos.symbol.split("/")[0]
                try:
                    free = Decimal(str(raw.get("free", {}).get(base_asset, 0)))
                    used = Decimal(str(raw.get("used", {}).get(base_asset, 0)))
                    total = free + used
                except Exception:
                    continue

                if total > 0:
                    # Position is alive — base exists (free or reserved by orders)
                    continue

                # Total base is zero. Check if there are open protective orders
                # that might still be pending. If yes, the position may close soon.
                try:
                    open_orders = await asyncio.to_thread(
                        self.exchange.fetch_open_orders, pos.symbol
                    )
                    has_protective = any(
                        o.status in {"open", "partial"}
                        for o in (open_orders or [])
                    )
                except Exception:
                    has_protective = False

                if has_protective:
                    logger.info(
                        "Spot sync: %s base=0 but protective orders exist; waiting",
                        pos.symbol,
                    )
                    continue

                # Base is zero AND no protective orders — position was closed
                logger.warning(
                    "Spot sync: %s base=0, no protective orders — detecting external close",
                    pos.symbol,
                )

            # For SELL positions: check if protective orders are gone and position is old
            elif pos.side == "sell":
                try:
                    open_orders = await asyncio.to_thread(
                        self.exchange.fetch_open_orders, pos.symbol
                    )
                    has_protective = any(
                        o.status in {"open", "partial"}
                        for o in (open_orders or [])
                    )
                except Exception:
                    has_protective = False

                if has_protective:
                    # Protective orders still active — position may still be closing
                    continue

                # No protective orders. Check if position is old enough (>15 min)
                age_seconds = time.time() - pos.entry_time
                if age_seconds <= 900:  # 15 minutes
                    # Too early to assume closed — could be newly opened
                    continue

                logger.warning(
                    "Spot sync: %s (SELL) no protective orders and %.0fs old — detecting external close",
                    pos.symbol, age_seconds,
                )
            else:
                continue

            # Try trade-history reconciliation first
            resolved = False
            try:
                resolved = await self._reconcile_from_trade_history(pos)
            except Exception as exc:
                logger.warning("Trade-history reconciliation failed for %s: %s", pos.symbol, exc)

            if resolved and pos.position_id not in self.positions:
                continue
            if resolved and pos.quantity <= 0:
                continue

            # Force close — the position is gone from the exchange
            await self.close_position(pos.position_id, "spot_balance_zero")

    async def _sync_futures_positions(self) -> None:
        """Futures: compare remote position quantity against local state."""
        try:
            remote = await asyncio.to_thread(self.exchange.fetch_positions, None)
        except Exception as exc:
            logger.warning("Exchange position sync failed: %s", exc)
            return

        by_symbol = {p.symbol: p for p in remote if p.quantity > 0}
        for position_id, pos in list(self.positions.items()):
            remote_pos = by_symbol.get(pos.symbol)
            if remote_pos is None:
                resolved = False
                try:
                    resolved = await self._reconcile_from_trade_history(pos)
                except Exception as exc:
                    logger.warning("Trade-history reconciliation failed for %s: %s", pos.symbol, exc)
                if resolved and pos.position_id not in self.positions:
                    self._reconciliation_start.pop(position_id, None)
                    continue
                if resolved and pos.quantity <= 0:
                    self._reconciliation_start.pop(position_id, None)
                    continue
                # Close positions that have been missing on exchange for too long
                start = self._reconciliation_start.get(position_id)
                if start is None:
                    self._reconciliation_start[position_id] = time.time()
                    pos.reconciliation_pending = True
                    pos.reconciliation_message = "remote position missing; fill history required"
                    logger.error("Position %s missing on exchange; keeping local state pending reconciliation", pos.symbol)
                    await self._persist_position(pos)
                    continue
                elapsed = time.time() - start
                if elapsed > self.RECONCILIATION_TIMEOUT_SECONDS:
                    logger.warning(
                        "Position %s missing on exchange for %.0fs; closing as stale",
                        pos.symbol, elapsed,
                    )
                    self._reconciliation_start.pop(position_id, None)
                    await self.close_position(position_id, "stale_not_on_exchange")
                    continue
                pos.reconciliation_pending = True
                pos.reconciliation_message = f"remote position missing; reconciliation pending ({elapsed:.0f}s)"
                await self._persist_position(pos)
                continue
            if remote_pos.side and remote_pos.side.lower() != pos.side.lower():
                pos.reconciliation_pending = True
                pos.reconciliation_message = f"side mismatch: local={pos.side} remote={remote_pos.side}"
                logger.error("Position side mismatch for %s: local=%s remote=%s", pos.symbol, pos.side, remote_pos.side)
                await self._persist_position(pos)
                continue
            if remote_pos.quantity != pos.quantity:
                try:
                    await self._reconcile_from_trade_history(pos)
                except Exception as exc:
                    logger.warning("Trade-history reconciliation failed for %s: %s", pos.symbol, exc)
                if pos.position_id not in self.positions:
                    continue
                if remote_pos.quantity != pos.quantity:
                    pos.reconciliation_pending = True
                    pos.reconciliation_message = f"quantity mismatch: local={pos.quantity} remote={remote_pos.quantity}"
                    logger.error("Position quantity mismatch for %s: local=%s remote=%s", pos.symbol, pos.quantity, remote_pos.quantity)
            else:
                pos.reconciliation_pending = False
                pos.reconciliation_message = None
            pos.current_pnl = getattr(remote_pos, "unrealized_pnl", Decimal("0"))
            mark_price = getattr(remote_pos, "mark_price", Decimal("0"))
            if mark_price > 0 and pos.entry_price > 0:
                risk_unit = abs(pos.entry_price - (pos.sl_price or pos.entry_price))
                if risk_unit > 0:
                    pos.current_rr = abs(mark_price - pos.entry_price) / risk_unit
            await self._persist_position(pos)

    async def _persist_trade(self, position: Position, reason: str) -> bool:
        """Atomically persist the closed Position and its aggregate Trade.

        A crash must not leave a committed Trade with an OPEN position, or an
        OPEN position deleted from memory with no Trade. The unique
        ``Trade.position_id`` constraint additionally makes recovery idempotent
        across process restarts.
        """
        if not self.db_session_factory:
            return True

        try:
            pnl = self.realized_pnl(position)
            fee = position.entry_fee_quote + position.exit_fee_quote
            qty = position.initial_quantity
            price = position.entry_price
            hold = max(0, int((position.close_time or time.time()) - position.entry_time))
            position_uuid = uuid.UUID(position.position_id)

            metadata = json.dumps(
                {
                    "entry_order_id": position.entry_order_id,
                    "sl_order_id": position.sl_order_id,
                    "tp_order_ids": position.tp_order_ids,
                    "close_reason": reason,
                    "close_time": position.close_time,
                    "entry_time": position.entry_time,
                    "initial_quantity": str(position.initial_quantity),
                    "entry_cost": str(position.entry_cost),
                    "entry_fee_quote": str(position.entry_fee_quote),
                    "exit_notional": str(position.exit_notional),
                    "exit_quantity": str(position.exit_quantity),
                    "exit_fee_quote": str(position.exit_fee_quote),
                    "fee_unconverted": str(position.fee_unconverted),
                    "fee_currencies": sorted(position.fee_currencies),
                    "fill_progress": {k: str(v) for k, v in position.fill_progress.items()},
                    "reconciliation_pending": position.reconciliation_pending,
                    "reconciliation_message": position.reconciliation_message,
                    "reconciliation_trade_fingerprint": position.reconciliation_trade_fingerprint,
                    "protective_client_ids": position.protective_client_ids,
                }
            )
            position_values = dict(
                exchange=position.exchange,
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                quantity=position.quantity,
                sl_price=position.sl_price,
                tp1_price=position.tp_prices[0] if len(position.tp_prices) > 0 else None,
                tp2_price=position.tp_prices[1] if len(position.tp_prices) > 1 else None,
                tp3_price=position.tp_prices[2] if len(position.tp_prices) > 2 else None,
                status=PositionStatusEnum.CLOSED,
                trailing_enabled=position.trailing_enabled,
                breakeven_enabled=position.breakeven_enabled,
                trail_stop_price=position.trail_stop_price,
                current_pnl=position.current_pnl,
                current_rr=position.current_rr,
                risk_percent=position.risk_percent,
                strategy=position.strategy,
                metadata_json=metadata,
            )

            async with self.db_session_factory() as session:
                # Lock the aggregate row when the database supports it. This
                # serializes concurrent close/recovery attempts in-process and
                # across PostgreSQL workers.
                db_position = await session.get(
                    PositionModel, position_uuid, with_for_update=True
                )
                if db_position is None:
                    db_position = PositionModel(id=position_uuid, **position_values)
                    session.add(db_position)
                    await session.flush()
                else:
                    for key, value in position_values.items():
                        setattr(db_position, key, value)

                existing = await session.execute(
                    select(Trade.id)
                    .where(Trade.position_id == position_uuid)
                    .limit(1)
                    .with_for_update()
                )
                if existing.scalar_one_or_none() is None:
                    session.add(
                        Trade(
                            id=uuid.uuid4(),
                            position_id=position_uuid,
                            exchange=position.exchange,
                            symbol=position.symbol,
                            side=position.side,
                            order_type="position",
                            quantity=qty,
                            price=price,
                            cost=position.entry_cost,
                            fee=fee,
                            strategy=position.strategy,
                            exit_reason=reason,
                            pnl=pnl,
                            pnl_percent=(
                                pnl / position.entry_cost * Decimal("100")
                                if position.entry_cost
                                else Decimal("0")
                            ),
                            hold_time_seconds=hold,
                        )
                    )

                # One commit covers Position CLOSED + Trade insert/update.
                await session.commit()

            if position.fee_unconverted > 0:
                logger.warning(
                    "Trade %s closed with %s %s fee(s) not converted",
                    position.position_id,
                    position.fee_unconverted,
                    ",".join(sorted(position.fee_currencies)),
                )
            return True
        except Exception:
            logger.exception("Atomic trade/position persistence failed for %s", position.position_id)
            return False

    async def _persist_position(self, position: Position):
        if not self.db_session_factory:
            return
        try:
            position_uuid = uuid.UUID(position.position_id)
            metadata = json.dumps(
                {
                    "entry_order_id": position.entry_order_id,
                    "sl_order_id": position.sl_order_id,
                    "tp_order_ids": position.tp_order_ids,
                    "close_reason": position.close_reason,
                    "close_time": position.close_time,
                    "entry_time": position.entry_time,
                    "initial_quantity": str(position.initial_quantity),
                    "entry_cost": str(position.entry_cost),
                    "entry_fee_quote": str(position.entry_fee_quote),
                    "exit_notional": str(position.exit_notional),
                    "exit_quantity": str(position.exit_quantity),
                    "exit_fee_quote": str(position.exit_fee_quote),
                    "fee_unconverted": str(position.fee_unconverted),
                    "fee_currencies": sorted(position.fee_currencies),
                    "fill_progress": {k: str(v) for k, v in position.fill_progress.items()},
                    "reconciliation_pending": position.reconciliation_pending,
                    "reconciliation_message": position.reconciliation_message,
                    "reconciliation_trade_fingerprint": position.reconciliation_trade_fingerprint,
                    "protective_client_ids": position.protective_client_ids,
                }
            )
            values = dict(
                exchange=position.exchange,
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                quantity=position.quantity,
                sl_price=position.sl_price,
                tp1_price=position.tp_prices[0] if len(position.tp_prices) > 0 else None,
                tp2_price=position.tp_prices[1] if len(position.tp_prices) > 1 else None,
                tp3_price=position.tp_prices[2] if len(position.tp_prices) > 2 else None,
                status=(
                    PositionStatusEnum.CLOSED
                    if position.status == "CLOSED"
                    else PositionStatusEnum.OPEN
                ),
                trailing_enabled=position.trailing_enabled,
                breakeven_enabled=position.breakeven_enabled,
                trail_stop_price=position.trail_stop_price,
                current_pnl=position.current_pnl,
                current_rr=position.current_rr,
                risk_percent=position.risk_percent,
                strategy=position.strategy,
                metadata_json=metadata,
            )
            async with self.db_session_factory() as session:
                existing = await session.get(PositionModel, position_uuid)
                if existing:
                    for key, value in values.items():
                        setattr(existing, key, value)
                else:
                    session.add(PositionModel(id=position_uuid, **values))
                await session.commit()
        except Exception as exc:
            logger.error("Position persistence failed for %s: %s", position.position_id, exc)

    async def recover_open_positions(self):
        if not self.db_session_factory:
            return
        try:
            async with self.db_session_factory() as session:
                conditions = [PositionModel.status == PositionStatusEnum.OPEN]
                if self.symbol:
                    conditions.append(PositionModel.symbol == self.symbol)
                if self.exchange_name and self.exchange_name != "unknown":
                    conditions.append(PositionModel.exchange == self.exchange_name)
                result = await session.execute(
                    select(PositionModel).where(*conditions)
                )
                rows = result.scalars().all()
            for row in rows:
                try:
                    metadata = json.loads(row.metadata_json or "{}")
                    pos = Position(
                        str(row.id),
                        row.exchange.value if hasattr(row.exchange, "value") else str(row.exchange),
                        row.symbol,
                        row.side,
                        Decimal(row.quantity),
                        Decimal(row.entry_price),
                        Decimal(row.sl_price) if row.sl_price is not None else None,
                        [
                            Decimal(x)
                            for x in (row.tp1_price, row.tp2_price, row.tp3_price)
                            if x is not None
                        ],
                        row.strategy or "unknown",
                    )
                    pos.entry_order_id = metadata.get("entry_order_id")
                    pos.sl_order_id = metadata.get("sl_order_id")
                    pos.entry_time = float(metadata.get("entry_time") or row.entry_time.timestamp())
                    pos.initial_quantity = Decimal(metadata.get("initial_quantity") or row.quantity)
                    pos.entry_cost = Decimal(metadata.get("entry_cost") or "0")
                    pos.entry_fee_quote = Decimal(metadata.get("entry_fee_quote") or "0")
                    pos.exit_notional = Decimal(metadata.get("exit_notional") or "0")
                    pos.exit_quantity = Decimal(metadata.get("exit_quantity") or "0")
                    pos.exit_fee_quote = Decimal(metadata.get("exit_fee_quote") or "0")
                    pos.fee_unconverted = Decimal(metadata.get("fee_unconverted") or "0")
                    pos.fee_currencies = set(metadata.get("fee_currencies") or [])
                    pos.fill_progress = {k: Decimal(v) for k, v in (metadata.get("fill_progress") or {}).items()}
                    pos.reconciliation_pending = bool(metadata.get("reconciliation_pending", False))
                    pos.reconciliation_message = metadata.get("reconciliation_message")
                    pos.reconciliation_trade_fingerprint = metadata.get("reconciliation_trade_fingerprint")
                    pos.protective_client_ids = dict(metadata.get("protective_client_ids") or {})
                    if pos.entry_cost > 0 and pos.initial_quantity > 0:
                        pos.entry_price = pos.entry_cost / pos.initial_quantity
                    pos.tp_order_ids = metadata.get("tp_order_ids") or []
                    pos.trailing_enabled = bool(row.trailing_enabled)
                    pos.breakeven_enabled = bool(row.breakeven_enabled)
                    pos.trail_stop_price = row.trail_stop_price
                    pos.current_pnl = row.current_pnl or Decimal("0")
                    pos.current_rr = row.current_rr or Decimal("0")
                    pos.risk_percent = row.risk_percent or Decimal("0")
                    self.positions[pos.position_id] = pos
                except Exception:
                    logger.exception("Failed to recover position %s", row.id)
        except Exception as exc:
            logger.error("Position recovery failed: %s", exc)

    async def _recovery_sync_loop(self):
        while True:
            try:
                await asyncio.sleep(30)
                await self.sync_with_exchange()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Position reconciliation loop error")
