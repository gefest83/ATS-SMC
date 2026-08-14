"""Single-symbol SMC trading engine with a complete paper lifecycle."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from decimal import Decimal
from typing import Optional

import time

from backend.config import settings
from backend.core.analysis.market_analyzer import MarketAnalyzer
from backend.core.analysis.signal_generator import SignalGenerator
from backend.core.execution.executor import ExecutorManager
from backend.core.monitoring.telegram import TelegramNotifier
from backend.core.order_manager import OrderManager
from backend.core.position_manager import PositionManager
from backend.core.risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class SMCBot:
    def __init__(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        market_data_provider=None,
        entry_lock: Optional[asyncio.Lock] = None,
        open_trade_gate=None,
    ):
        self.symbol = symbol or settings.SYMBOL
        self.timeframe = timeframe or settings.TIMEFRAME
        # Paper mode accepts an explicitly injected local provider, but never
        # constructs an exchange adapter implicitly. This keeps paper tests and
        # deployments isolated from REST/WebSocket clients and network calls.
        self.market_data_provider = market_data_provider
        self.exchange = None
        self._exchange_name = settings.EXCHANGE.lower().strip()
        self._exchange_closed = False
        self._lifecycle_lock = asyncio.Lock()
        # Multi-symbol mode shares this lock across symbol bots so spot
        # entries cannot all size against the same stale free-quote balance.
        self._entry_lock = entry_lock or asyncio.Lock()
        self._open_trade_gate = open_trade_gate
        self._stop_complete = False
        from backend.db.session import AsyncSessionLocal
        self.analyzer = MarketAnalyzer(self.symbol, self.timeframe)
        self.signal_gen = SignalGenerator(min_rr=settings.MIN_RR_RATIO)
        self.risk_manager = RiskManager(
            settings.INITIAL_EQUITY,
            db_session_factory=AsyncSessionLocal,
            state_scope=f"{self._exchange_name}:{self.symbol}",
        )
        self.executor_manager = ExecutorManager(None)
        self.position_manager = PositionManager(
            db_session_factory=AsyncSessionLocal,
            exchange_name=self._exchange_name,
            exchange=None,
            on_position_closed=self._on_live_position_closed,
            symbol=self.symbol,
        )
        self.order_manager = OrderManager(
            None,
            self.position_manager,
            db_session_factory=AsyncSessionLocal,
        )
        self.notifier = TelegramNotifier()
        self.running = False
        self.last_signal: Optional[dict] = None
        self._paper_position: Optional[dict] = None
        self._last_entry_candle_ts: Optional[int] = None
        self._free_quote_balance: Decimal = Decimal("0")
        # Diagnostic state (read-only by dashboard, no trading logic impact)
        self._last_error: Optional[str] = None
        self._loop_count: int = 0
        self._last_loop_time: Optional[float] = None
        self._started_at: Optional[float] = None

    def has_active_position(self) -> bool:
        """Check if this bot has an active position (paper or live/testnet).

        Paper mode tracks positions in ``_paper_position``.
        Live/testnet mode tracks positions in ``position_manager.positions``.
        """
        if self._paper_position is not None:
            return True
        pm = getattr(self, "position_manager", None)
        if pm is not None:
            positions = getattr(pm, "positions", None)
            if positions is not None and len(positions) > 0:
                return True
        return False

    def get_live_position_count(self) -> int:
        """Return the number of live/testnet positions from PositionManager."""
        pm = getattr(self, "position_manager", None)
        if pm is not None:
            positions = getattr(pm, "positions", None)
            if positions is not None:
                return len(positions)
        return 0

    def _ensure_exchange(self):
        """Create and wire one adapter only for explicitly remote modes."""
        if settings.TRADING_MODE.lower().strip() == "paper":
            raise RuntimeError(
                "Paper mode cannot construct an exchange; inject a local market-data provider"
            )
        if self.exchange is not None and not self._exchange_closed:
            return self.exchange

        if settings.TRADING_MODE.lower().strip() not in {"live", "testnet"}:
            raise RuntimeError(
                f"Unsupported trading mode: {settings.TRADING_MODE}"
            )
        from backend.core.exchange.factory import create_exchange
        from backend.core.execution.executor import LiveExecutor

        self.exchange = create_exchange()
        self._exchange_name = self.exchange.get_exchange_name()
        self._exchange_closed = False
        self.order_manager.exchange = self.exchange
        self.position_manager.exchange = self.exchange
        self.position_manager.exchange_name = self._exchange_name
        if settings.TRADING_MODE.lower().strip() in {"live", "testnet"}:
            self.executor_manager.live = LiveExecutor(self.exchange)
        return self.exchange

    async def _on_live_position_closed(self, position, reason: str) -> None:
        # PositionManager invokes this only after the closed Position and its
        # Trade have been persisted.  The position id makes duplicate delivery
        # from a strategy request and this callback harmless.
        realized_pnl = self.position_manager.realized_pnl(position)
        close_applied = self.risk_manager.trade_closed(
            float(realized_pnl), event_id=position.position_id
        )

        # PositionManager is authoritative for the symbol.  A recovered
        # runtime counter can be stale after a restart or an earlier close
        # callback, so synchronize the per-symbol risk counter with the
        # positions that are actually still open.
        # Some lightweight/test PositionManager implementations do not expose
        # the in-memory positions mapping. In that case RiskManager.trade_closed()
        # remains the source of truth and we must not fail the close callback.
        positions = getattr(self.position_manager, "positions", None)
        if positions is not None:
            self.risk_manager.open_trades = len(positions)

        # The shared multi-symbol gate must follow the same idempotency rule as
        # RiskManager.  A duplicate close callback must not release capacity
        # twice and make the account-wide counter drift below reality.
        open_trade_gate = getattr(self, "_open_trade_gate", None)
        if close_applied and open_trade_gate is not None:
            open_trade_gate.release()

        logger.info(
            "Risk close sync: symbol=%s reason=%s open_trades=%d "
            "live_positions=%d gate_count=%s close_applied=%s pnl=%.4f",
            getattr(self, "symbol", "?"),
            reason,
            self.risk_manager.open_trades,
            len(positions) if positions is not None else -1,
            getattr(open_trade_gate, "_count", "N/A") if open_trade_gate is not None else "N/A",
            close_applied,
            float(realized_pnl),
        )

        await self._persist_runtime_state()
        short_id = position.position_id[:8] if hasattr(position, 'position_id') else '?'

        # Calculate exit price from realized PnL
        exit_price = None
        if hasattr(position, 'exit_notional') and position.exit_quantity > 0:
            exit_price = float(position.exit_notional / position.exit_quantity)
        elif float(realized_pnl) != 0 and position.quantity > 0:
            qty = float(position.initial_quantity)
            if position.side == "buy":
                exit_price = float(position.entry_price) + float(realized_pnl) / qty
            else:
                exit_price = float(position.entry_price) - float(realized_pnl) / qty

        await self.notifier.notify_position_closed(
            symbol=position.symbol,
            side=(position.side or "").upper(),
            quantity=float(position.initial_quantity),
            entry_price=float(position.entry_price),
            exit_price=exit_price,
            close_reason=reason,
            realized_pnl=realized_pnl,
            position_id=position.position_id,
            entry_time=position.entry_time if hasattr(position, 'entry_time') else None,
            close_time=position.close_time if hasattr(position, 'close_time') else None,
        )

    @staticmethod
    def _serialize_paper_position(position):
        if not position:
            return None
        payload = dict(position)
        for key, value in list(payload.items()):
            if isinstance(value, Decimal):
                payload[key] = str(value)
        return json.dumps(payload)

    @staticmethod
    def _restore_paper_position(payload):
        if not payload:
            return None
        try:
            data = json.loads(payload)
            decimal_keys = {"entry", "sl", "tp1", "tp2", "tp3", "size", "remaining", "tp_qty", "unrealized_pnl"}
            for key in decimal_keys:
                if key in data and data[key] is not None:
                    data[key] = Decimal(str(data[key]))
            data["tp_hit"] = [bool(x) for x in data.get("tp_hit", [False, False, False])]
            if "opened_at" not in data:
                data["opened_at"] = time.time()
            return data
        except Exception:
            logger.exception("Failed to restore paper position")
            return None

    async def _persist_runtime_state(self):
        await self.risk_manager.persist_state(self._serialize_paper_position(self._paper_position))

    def fetch_ohlcv(self, limit: int = 200, timeframe: Optional[str] = None) -> list:
        mode = settings.TRADING_MODE.lower().strip()
        effective_tf = timeframe or self.timeframe
        if mode == "paper":
            provider = self.market_data_provider
            if provider is None:
                raise RuntimeError(
                    "Paper mode requires an explicit local market-data provider"
                )
            fetcher = getattr(provider, "fetch_ohlcv", None)
            if not callable(fetcher):
                raise TypeError(
                    "Paper market-data provider must define fetch_ohlcv"
                )
            result = fetcher(self.symbol, effective_tf, limit=limit)
            if inspect.isawaitable(result):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise TypeError(
                    "Paper market-data provider must expose a synchronous "
                    "fetch_ohlcv method"
                )
            if not isinstance(result, list):
                raise TypeError(
                    "Paper market-data provider fetch_ohlcv must return a list"
                )
            return result
        exchange = self._ensure_exchange()
        return exchange.fetch_ohlcv(self.symbol, effective_tf, limit=limit)

    def validate_startup(self) -> None:
        """Fail before task creation when the selected data source is invalid."""
        mode = settings.TRADING_MODE.lower().strip()
        if mode == "paper":
            # This is intentionally a local provider call only. Paper mode must
            # never use the exchange factory as a market-data fallback.
            self.fetch_ohlcv(limit=1)
            return
        if mode in {"live", "testnet"}:
            return
        raise RuntimeError(f"Unsupported trading mode: {settings.TRADING_MODE}")

    async def _manage_paper_position(self, price: Decimal) -> None:
        pos = self._paper_position
        if not pos:
            return

        side = pos["side"]
        realized = Decimal("0")
        reason = None

        def pnl_for(exit_price: Decimal, qty: Decimal) -> Decimal:
            if side == "buy":
                return (exit_price - pos["entry"]) * qty
            return (pos["entry"] - exit_price) * qty

        # Stop-loss closes all remaining quantity.
        hit_sl = (side == "buy" and price <= pos["sl"]) or (side == "sell" and price >= pos["sl"])
        if hit_sl:
            realized = pnl_for(pos["sl"], pos["remaining"])
            reason = "stop_loss"
            pos["remaining"] = Decimal("0")
        else:
            # Each TP closes one third of the original position.
            for index, key in enumerate(("tp1", "tp2", "tp3")):
                if pos["tp_hit"][index] or pos["remaining"] <= 0:
                    continue
                hit_tp = (side == "buy" and price >= pos[key]) or (side == "sell" and price <= pos[key])
                if hit_tp:
                    qty = min(pos["tp_qty"], pos["remaining"])
                    realized += pnl_for(pos[key], qty)
                    pos["remaining"] -= qty
                    pos["tp_hit"][index] = True
                    self.risk_manager.apply_pnl(float(pnl_for(pos[key], qty)))
                    logger.info("[PAPER] TP%d hit: %s PnL=%s", index + 1, self.symbol, pnl_for(pos[key], qty))
                    if pos["remaining"] <= 0:
                        reason = "take_profit"
                        break

        if realized and reason == "stop_loss":
            self.risk_manager.apply_pnl(float(realized))

        if pos["remaining"] <= 0:
            self.risk_manager.trade_closed(0.0, event_id=f"paper_{self.symbol}_{id(pos)}")
            logger.info("[PAPER] Position closed: %s reason=%s realized_pnl=%s", self.symbol, reason, realized)
            paper_pos_id = f"paper_{self.symbol}_{id(pos)}"
            entry_price = float(pos["entry"])
            # Calculate exit price from realized PnL
            exit_price = None
            if realized != 0 and pos.get("size") and float(pos["size"]) > 0:
                qty = float(pos["size"])
                if pos["side"] == "buy":
                    exit_price = entry_price + float(realized) / qty
                else:
                    exit_price = entry_price - float(realized) / qty
            await self.notifier.notify_position_closed(
                symbol=self.symbol,
                side=pos["side"].upper(),
                quantity=float(pos.get("size", 0)),
                entry_price=entry_price,
                exit_price=exit_price,
                close_reason=reason or "unknown",
                realized_pnl=realized,
                position_id=paper_pos_id,
            )
            self._paper_position = None
            await self._persist_runtime_state()
            return

        # Keep the displayed equity aligned with realized PnL only; do not
        # repeatedly add unrealized PnL on every polling cycle.
        unrealized = pnl_for(price, pos["remaining"])
        pos["unrealized_pnl"] = unrealized
        self.risk_manager.update_equity(
            self.risk_manager.current_equity
        )
        await self._persist_runtime_state()

    async def run(self, poll_seconds: int = 60):
        mode = settings.TRADING_MODE.lower().strip()
        if mode in {"live", "testnet"}:
            self._ensure_exchange()
        else:
            self.validate_startup()

        self._stop_complete = False
        self.running = True
        self._started_at = time.monotonic()
        self._exchange_closed = False if self.exchange is not None else self._exchange_closed
        recovered_positions = await self.position_manager.start()
        paper_payload = await self.risk_manager.restore_state()
        restored_paper = self._restore_paper_position(paper_payload)
        if restored_paper is not None and settings.TRADING_MODE.lower() == "paper":
            self._paper_position = restored_paper
        await self._persist_runtime_state()
        await self.order_manager.start()
        # Exchange recovery is authoritative for live/testnet positions.  A
        # stale persisted counter must never inflate the live open-trade count.
        # Run AFTER order_manager.start() so staleness checks have closed
        # any positions that no longer exist on the exchange.
        if settings.TRADING_MODE.lower() in {"live", "testnet"}:
            actual_positions = self.get_live_position_count()
            self.risk_manager.open_trades = actual_positions
            if self._open_trade_gate is not None:
                self._open_trade_gate.register(self.symbol, actual_positions)
            logger.info(
                "Risk init: symbol=%s recovered_positions=%d actual=%d "
                "open_trades=%d gate_count=%s",
                self.symbol,
                recovered_positions,
                actual_positions,
                self.risk_manager.open_trades,
                getattr(self._open_trade_gate, "_count", "N/A") if self._open_trade_gate is not None else "N/A",
            )
        logger.info("Starting SMC engine on %s %s (%s mode)", self.symbol, self.timeframe, settings.TRADING_MODE)
        while self.running:
            self._loop_count += 1
            self._last_loop_time = time.monotonic()
            try:
                # Periodic risk counter sync: every 10 loops, recalculate from actual positions
                if self._loop_count % 10 == 0 and settings.TRADING_MODE.lower() in {"live", "testnet"}:
                    live_count = self.get_live_position_count()
                    if self.risk_manager.open_trades != live_count:
                        logger.warning(
                            "Risk drift corrected: symbol=%s open_trades=%d -> %d",
                            self.symbol, self.risk_manager.open_trades, live_count,
                        )
                        self.risk_manager.open_trades = live_count
                ohlcv = await asyncio.to_thread(self.fetch_ohlcv)
                analysis = self.analyzer.analyze(ohlcv)
                if not analysis:
                    await asyncio.sleep(poll_seconds)
                    continue
                price = analysis["current_price"]
                if settings.TRADING_MODE.lower() in {"testnet", "live"}:
                    try:
                        balance = await asyncio.to_thread(self.exchange.fetch_balance)
                        if settings.EXCHANGE_MARKET_TYPE.lower() == "futures":
                            quote = balance.get("USDT") or balance.get("USD")
                            if quote is not None:
                                self.risk_manager.update_equity(float(quote))
                            self._free_quote_balance = quote or Decimal("0")
                        else:
                            equity = Decimal("0")
                            quote_asset = "USDT"
                            try:
                                raw = await asyncio.to_thread(self.exchange.exchange.fetch_balance)
                                self._free_quote_balance = Decimal(str(raw.get("free", {}).get(quote_asset, 0)))
                            except Exception:
                                self._free_quote_balance = balance.get(quote_asset, Decimal("0"))
                            for asset, total in balance.items():
                                if asset == quote_asset:
                                    equity += total
                                elif total > 0:
                                    try:
                                        ticker = await asyncio.to_thread(
                                            self.exchange.fetch_ticker,
                                            f"{asset}/{quote_asset}",
                                        )
                                        equity += total * ticker.price
                                    except Exception:
                                        pass
                            self.risk_manager.update_equity(float(equity))
                    except Exception as exc:
                        logger.warning("Live equity reconciliation failed: %s", exc)
                if settings.TRADING_MODE.lower() == "paper":
                    await self._manage_paper_position(price)
                side = self.signal_gen.generate_signal(analysis)
                candle_ts = int(ohlcv[-1][0]) if ohlcv and len(ohlcv[-1]) > 0 else None
                new_signal_candle = candle_ts is None or candle_ts != self._last_entry_candle_ts
                has_pos = self.has_active_position()
                can_trade = self.risk_manager.can_open_trade()

                if side and new_signal_candle:
                    levels = self.signal_gen.build_levels(analysis, side)
                    score = self.signal_gen.score(analysis)

                    # ALWAYS send signal notification first — strategy detected this
                    await self.notifier.notify_signal(
                        symbol=self.symbol,
                        side=side,
                        timeframe=self.timeframe,
                        signal_price=float(levels["entry"]),
                        sl=float(levels["stop_loss"]),
                        tp1=float(levels["tp1"]),
                        tp2=float(levels["tp2"]),
                        tp3=float(levels["tp3"]),
                        risk_pct=settings.RISK_PER_TRADE_PCT,
                        score=score,
                    )

                    # Then check if blocked
                    if has_pos:
                        logger.info(
                            "Signal %s on %s: BLOCKED reason=position_active open_trades=%d",
                            side, self.symbol, self.risk_manager.open_trades,
                        )
                        await self.notifier.notify_signal_blocked(
                            symbol=self.symbol,
                            side=side,
                            reason="уже существует открытая позиция",
                        )
                        self._last_entry_candle_ts = candle_ts
                    elif not can_trade:
                        logger.info(
                            "Signal %s on %s: BLOCKED reason=risk_check open_trades=%d max=%s",
                            side, self.symbol, self.risk_manager.open_trades,
                            settings.MAX_OPEN_TRADES,
                        )
                        await self.notifier.notify_signal_blocked(
                            symbol=self.symbol,
                            side=side,
                            reason="достигнут лимит открытых сделок",
                        )
                        self._last_entry_candle_ts = candle_ts
                    else:
                        if settings.TRADING_MODE.lower() == "paper":
                            size = self.risk_manager.calculate_position_size(
                                float(levels["entry"]), float(levels["stop_loss"])
                            )
                            self.last_signal = {
                                "side": side, "size": size,
                                "candle_timestamp": candle_ts, **levels,
                            }
                            self._last_entry_candle_ts = candle_ts
                            if size > 0:
                                self._paper_position = {
                                    "side": side.lower(), "entry": levels["entry"],
                                    "sl": levels["stop_loss"], "tp1": levels["tp1"],
                                    "tp2": levels["tp2"], "tp3": levels["tp3"],
                                    "size": Decimal(str(size)),
                                    "remaining": Decimal(str(size)),
                                    "tp_qty": Decimal(str(size)) / Decimal("3"),
                                    "tp_hit": [False, False, False],
                                    "unrealized_pnl": Decimal("0"),
                                    "opened_at": time.time(),
                                }
                                self.risk_manager.trade_opened()
                                await self._persist_runtime_state()
                                paper_pos_id = f"paper_{self.symbol}_{id(self._paper_position)}"
                                await self.notifier.notify_order_opened(
                                    symbol=self.symbol,
                                    side=side,
                                    quantity=size,
                                    entry_price=float(levels["entry"]),
                                    sl=float(levels["stop_loss"]),
                                    tp1=float(levels["tp1"]),
                                    tp2=float(levels["tp2"]),
                                    tp3=float(levels["tp3"]),
                                    risk_pct=settings.RISK_PER_TRADE_PCT,
                                    position_id=paper_pos_id,
                                )
                        else:
                            # The quote/base balance is shared across all symbols
                            # in multi-symbol Spot mode. Re-read it while holding
                            # the shared entry lock, then size and submit atomically.
                            # Otherwise BTC/ETH/SOL can all see the same USDT balance
                            # and collectively submit more than the account owns.
                            async with self._entry_lock:
                                gate_reserved = False
                                if self._open_trade_gate is not None:
                                    gate_reserved = self._open_trade_gate.try_reserve()
                                    if not gate_reserved:
                                        logger.warning(
                                            "Max open trades reached (%s) across multi-symbol account",
                                            settings.MAX_OPEN_TRADES,
                                        )
                                        self._last_entry_candle_ts = candle_ts
                                        await asyncio.sleep(poll_seconds)
                                        continue
                                size = self.risk_manager.calculate_position_size(
                                    float(levels["entry"]), float(levels["stop_loss"])
                                )
                                if settings.EXCHANGE_MARKET_TYPE.lower() == "futures":
                                    pass
                                elif side.lower() == "buy":
                                    try:
                                        raw = await asyncio.to_thread(
                                            self.exchange.exchange.fetch_balance
                                        )
                                        free_quote = Decimal(
                                            str(raw.get("free", {}).get("USDT", 0))
                                        )
                                    except Exception:
                                        free_quote = Decimal("0")
                                    self._free_quote_balance = free_quote
                                    if free_quote > 0:
                                        max_size = free_quote / Decimal(str(levels["entry"]))
                                        size = min(
                                            Decimal(str(size)), max_size
                                        )
                                        size = float(size)
                                    else:
                                        size = 0
                                elif side.lower() == "sell":
                                    base_asset = self.symbol.split("/")[0]
                                    try:
                                        raw = await asyncio.to_thread(
                                            self.exchange.exchange.fetch_balance
                                        )
                                        free_base = Decimal(
                                            str(raw.get("free", {}).get(base_asset, 0))
                                        )
                                    except Exception:
                                        free_base = Decimal("0")
                                    if free_base > 0:
                                        size = min(
                                            Decimal(str(size)), free_base
                                        )
                                        size = float(size)
                                    else:
                                        size = 0

                                self.last_signal = {
                                    "side": side, "size": size,
                                    "candle_timestamp": candle_ts, **levels,
                                }
                                self._last_entry_candle_ts = candle_ts

                                if size > 0:
                                    try:
                                        await self.order_manager.open_position(
                                            symbol=self.symbol,
                                            side=side.lower(),
                                            quantity=Decimal(str(size)),
                                            order_type="market",
                                            sl_price=levels["stop_loss"],
                                            tp_prices=[
                                                levels["tp1"], levels["tp2"], levels["tp3"]
                                            ],
                                            strategy_name="smc",
                                        )
                                    except Exception:
                                        if gate_reserved and self._open_trade_gate is not None:
                                            self._open_trade_gate.release()
                                        logger.exception(
                                            "Live position opening failed; risk counter was not incremented"
                                        )
                                    else:
                                        self.risk_manager.trade_opened()
                                        logger.info(
                                            "Risk open sync: symbol=%s open_trades=%d "
                                            "gate_count=%s",
                                            getattr(self, "symbol", "?"),
                                            self.risk_manager.open_trades,
                                            getattr(self._open_trade_gate, "_count", "N/A") if self._open_trade_gate is not None else "N/A",
                                        )
                                        await self._persist_runtime_state()
                                        live_pos_id = "unknown"
                                        pm = getattr(self, "position_manager", None)
                                        if pm is not None:
                                            live_positions = getattr(pm, "positions", {})
                                            if live_positions:
                                                live_pos_id = list(live_positions.keys())[-1]
                                        await self.notifier.notify_order_opened(
                                            symbol=self.symbol,
                                            side=side,
                                            quantity=size,
                                            entry_price=float(levels["entry"]),
                                            sl=float(levels["stop_loss"]),
                                            tp1=float(levels["tp1"]),
                                            tp2=float(levels["tp2"]),
                                            tp3=float(levels["tp3"]),
                                            risk_pct=settings.RISK_PER_TRADE_PCT,
                                            position_id=live_pos_id,
                                        )
                                elif gate_reserved and self._open_trade_gate is not None:
                                    self._open_trade_gate.release()
                await asyncio.sleep(poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Error in SMC loop: %s", exc)
                await asyncio.sleep(10)

    async def stop(self):
        """Stop every child task and close the adapter exactly once."""
        async with self._lifecycle_lock:
            if self._stop_complete:
                return
            self.running = False

            try:
                await self._persist_runtime_state()
            except Exception:
                logger.exception("Failed to persist runtime state during shutdown")

            for component in (
                self.order_manager,
                self.position_manager,
                self.notifier,
            ):
                stop = getattr(component, "stop", None)
                if not callable(stop):
                    continue
                try:
                    result = stop()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception(
                        "Failed to stop %s", type(component).__name__
                    )

            exchange = self.exchange
            close = getattr(exchange, "close", None) if exchange is not None else None
            if callable(close) and not self._exchange_closed:
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("Failed to close engine exchange")
                finally:
                    self._exchange_closed = True

            self._stop_complete = True
