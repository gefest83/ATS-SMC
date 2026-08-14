"""Multi-symbol engine wrapper that runs independent SMCBot instances."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Dict, List, Optional

from backend.config import settings
from backend.core.engine.smc_bot import SMCBot
from backend.core.monitoring.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

# UTC+3 offset for user-facing timestamps
_UTC3_OFFSET_SECONDS = 3 * 3600


def _now_utc3_str() -> str:
    """Return current time as HH:MM:SS in UTC+3."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=_UTC3_OFFSET_SECONDS)
    return now.strftime("%H:%M:%S")


class SharedOpenTradeGate:
    """Account-wide open-position limit shared by all symbol bots."""

    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self._count = 0
        self._registered: Dict[str, int] = {}

    def register(self, symbol: str, count: int) -> None:
        """Register recovered positions for one symbol exactly once.

        Subsequent calls for the same symbol are silently ignored to prevent
        stale/duplicate registrations from inflating the gate counter.
        Use recalculate_gate() to fully resync from actual positions.
        """
        if symbol in self._registered:
            return
        value = max(0, int(count))
        self._registered[symbol] = value
        self._count += value

    def try_reserve(self) -> bool:
        if self._count >= self.limit:
            return False
        self._count += 1
        return True

    def release(self) -> None:
        self._count = max(0, self._count - 1)

    @property
    def count(self) -> int:
        return self._count

    @property
    def registered(self) -> Dict[str, int]:
        return dict(self._registered)


class MultiSymbolEngine:
    """Manages multiple independent SMCBot instances for different symbols.

    Each symbol gets its own isolated SMCBot with independent state:
    - market data
    - candles
    - analysis
    - signal
    - position
    - entry, SL, TP1/TP2/TP3
    - recovery/protection
    - last signal

    An error in one symbol does NOT stop the others.
    """

    def __init__(self, market_data_provider=None):
        self._bots: Dict[str, SMCBot] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._market_data_provider = market_data_provider
        self.running = False
        self._stop_event = asyncio.Event()
        # One account has one shared Spot balance. Symbol bots must serialize
        # entry sizing/submission so they cannot all spend the same free quote.
        self._entry_lock = asyncio.Lock()
        # One account-wide open-position limit is shared by all symbol bots.
        self._open_trade_gate = SharedOpenTradeGate(settings.MAX_OPEN_TRADES)
        # Engine-level Telegram notifier (separate from per-bot notifiers)
        self._notifier = TelegramNotifier()
        self._start_time: Optional[float] = None
        self._started_at: Optional[str] = None
        self._last_heartbeat: Optional[float] = None
        self._errors: Dict[str, Optional[str]] = {}
        self._loop_counts: Dict[str, int] = {}
        self._last_loop_times: Dict[str, Optional[float]] = {}
        self._sync_task: Optional[asyncio.Task] = None
        self._stop_notification_sent: bool = False

    @property
    def symbols(self) -> List[str]:
        return list(self._bots.keys())

    def get_bot(self, symbol: str) -> Optional[SMCBot]:
        return self._bots.get(symbol)

    def get_all_bots(self) -> Dict[str, SMCBot]:
        return dict(self._bots)

    @staticmethod
    def _actual_position_count(bot: SMCBot) -> int:
        """Return the real open-position count for both live and paper modes."""
        live_count = max(0, int(bot.get_live_position_count()))
        paper_count = 1 if getattr(bot, "_paper_position", None) is not None else 0
        return live_count + paper_count

    def _create_bot(self, symbol: str) -> SMCBot:
        """Create an independent SMCBot for a symbol."""
        return SMCBot(
            symbol=symbol,
            timeframe=settings.TIMEFRAME,
            market_data_provider=self._market_data_provider,
            entry_lock=self._entry_lock,
            open_trade_gate=self._open_trade_gate,
        )

    async def start(self) -> None:
        """Start independent bot tasks for all configured symbols."""
        if self.running:
            return

        symbols = settings.symbols_list
        logger.info("Starting multi-symbol engine for: %s", symbols)

        self._start_time = time.monotonic()
        self._started_at = _now_utc3_str()
        self._stop_notification_sent = False

        started_symbols: List[str] = []
        for symbol in symbols:
            bot = self._create_bot(symbol)

            # Validate before registering the bot as active. A failed symbol
            # must not appear healthy/running in engine diagnostics.
            try:
                bot.validate_startup()
            except Exception as exc:
                self._errors[symbol] = str(exc)
                logger.error("Validation failed for %s: %s", symbol, exc)
                continue

            self._bots[symbol] = bot
            self._errors[symbol] = None
            self._loop_counts[symbol] = 0
            self._last_loop_times[symbol] = None
            started_symbols.append(symbol)

            task = asyncio.create_task(
                self._run_bot_safely(symbol, bot),
                name=f"smc-engine-{symbol}",
            )
            self._tasks[symbol] = task

        # Start periodic sync task for gate/risk counter reconciliation
        self._sync_task = asyncio.create_task(
            self._periodic_sync_loop(),
            name="multi-symbol-sync",
        )

        self.running = bool(self._tasks)
        logger.info(
            "Multi-symbol engine started with %d symbols",
            len(self._tasks),
        )

        if not self.running:
            self._sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sync_task
            self._sync_task = None
            return

        # Send startup Telegram notification only for symbols that actually started.
        await self._notifier.notify_engine_started(
            symbols=started_symbols,
            exchange=settings.EXCHANGE,
            timeframe=settings.TIMEFRAME,
            bot_count=len(self._tasks),
            risk_per_trade=settings.RISK_PER_TRADE_PCT,
            max_open_trades=settings.MAX_OPEN_TRADES,
            started_at=self._started_at or "",
        )

    async def _run_bot_safely(self, symbol: str, bot: SMCBot) -> None:
        """Run a single bot, catching errors to protect other symbols."""
        try:
            await bot.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._errors[symbol] = str(exc)
            logger.exception(
                "Bot for %s crashed (other symbols continue): %s",
                symbol,
                exc,
            )

    async def _periodic_sync_loop(self) -> None:
        """Periodically recalculate gate and risk counters from actual positions.

        Every 15 seconds, verify that SharedOpenTradeGate and per-symbol
        risk_manager.open_trades match the real position counts. This
        corrects any drift from missed close events or restart recovery.
        """
        while True:
            try:
                await asyncio.sleep(15)
                if not self.running:
                    break
                self.sync_risk_counters()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Periodic sync error: %s", exc)

    async def stop(self, reason: str = "Остановка через API") -> None:
        """Stop all bot tasks gracefully."""
        if not self.running:
            return

        # Send shutdown notification exactly once
        if not self._stop_notification_sent:
            self._stop_notification_sent = True
            # Count open positions before stopping
            open_positions = sum(
                self._actual_position_count(bot) for bot in self._bots.values()
            )
            try:
                await self._notifier.notify_engine_stopped(
                    open_positions=open_positions,
                    reason=reason,
                )
            except Exception:
                logger.exception("Failed to send stop notification")

        self.running = False
        self._stop_event.set()

        # Stop sync task
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        # Stop all bots
        for symbol, bot in self._bots.items():
            try:
                await bot.stop()
            except Exception as exc:
                logger.exception("Failed to stop bot for %s: %s", symbol, exc)

        # Cancel all tasks
        for symbol, task in self._tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.exception(
                        "Failed to await task for %s: %s",
                        symbol,
                        exc,
                    )

        self._tasks.clear()
        self._bots.clear()
        self._open_trade_gate = SharedOpenTradeGate(settings.MAX_OPEN_TRADES)
        self._errors.clear()
        self._loop_counts.clear()
        self._last_loop_times.clear()
        self._stop_event.clear()
        self._start_time = None
        self._started_at = None
        self._last_heartbeat = None

        # Stop engine-level notifier
        try:
            await self._notifier.stop()
        except Exception:
            pass

        logger.info("Multi-symbol engine stopped")

    async def stop_with_notification(self, reason: str = "Завершение процесса") -> None:
        """Stop engine and send notification if not already sent.

        Used by lifespan shutdown to ensure exactly one notification.
        """
        await self.stop(reason=reason)

    def get_status(self) -> Dict[str, dict]:
        """Get status for all symbols."""
        from decimal import Decimal
        result = {}
        for symbol, bot in self._bots.items():
            raw = bot.last_signal
            sig = None
            if raw is not None:
                sig = {k: float(v) if isinstance(v, Decimal) else v for k, v in raw.items()}
            result[symbol] = {
                "symbol": symbol,
                "running": bot.running,
                "last_signal": sig,
                "has_position": self._actual_position_count(bot) > 0,
            }
        return result

    def get_paper_position(self, symbol: str) -> Optional[dict]:
        """Get paper position for a specific symbol."""
        bot = self._bots.get(symbol)
        if bot is None:
            return None
        return bot._paper_position

    def get_last_signal(self, symbol: str) -> Optional[dict]:
        """Get last signal for a specific symbol."""
        from decimal import Decimal
        bot = self._bots.get(symbol)
        if bot is None:
            return None
        raw = bot.last_signal
        if raw is None:
            return None
        return {k: float(v) if isinstance(v, Decimal) else v for k, v in raw.items()}

    def _format_uptime(self) -> Optional[str]:
        """Format uptime from start time as HH:MM:SS."""
        if self._start_time is None:
            return None
        elapsed = time.monotonic() - self._start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _format_timestamp(self, monotonic_time: Optional[float]) -> Optional[str]:
        """Convert a monotonic timestamp to HH:MM:SS elapsed since engine start."""
        if monotonic_time is None:
            return None
        if self._start_time is None:
            return None
        try:
            offset = monotonic_time - self._start_time
            if offset < 0:
                return None
            hours = int(offset // 3600)
            minutes = int((offset % 3600) // 60)
            seconds = int(offset % 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except Exception:
            return None

    def get_health_status(self, symbol: str) -> str:
        """Return HEALTHY/ERROR/STOPPED/STARTING for a symbol."""
        if not self.running:
            return "STOPPED"
        if self._errors.get(symbol):
            return "ERROR"
        task = self._tasks.get(symbol)
        if task is None:
            return "STOPPED"
        if task.done():
            exc = task.exception() if not task.cancelled() else None
            return "ERROR" if exc is not None else "STOPPED"
        return "HEALTHY"

    def get_all_health(self) -> Dict[str, str]:
        """Return health status for all symbols."""
        return {symbol: self.get_health_status(symbol) for symbol in self._bots}

    def get_symbol_diagnostics(self, symbol: str) -> dict:
        """Return detailed diagnostics for a single symbol."""
        bot = self._bots.get(symbol)
        task = self._tasks.get(symbol)
        if bot is None:
            return {"symbol": symbol, "exists": False}

        task_state = "UNKNOWN"
        task_id = None
        task_exception = None
        if task is not None:
            task_id = task.get_name()
            if task.cancelled():
                task_state = "CANCELLED"
            elif task.done():
                task_state = "DONE"
                try:
                    task_exception = str(task.exception())
                except Exception:
                    task_exception = "unknown"
            else:
                task_state = "RUNNING"

        # Sync loop diagnostics from SMCBot
        self._loop_counts[symbol] = bot._loop_count
        self._last_loop_times[symbol] = bot._last_loop_time
        if bot._last_error is not None:
            self._errors[symbol] = bot._last_error
        elif self._errors.get(symbol) is None and task_state == "RUNNING":
            self._errors[symbol] = None

        return {
            "symbol": symbol,
            "exists": True,
            "health": self.get_health_status(symbol),
            "bot_running": bot.running,
            "bot_object_id": id(bot),
            "task_state": task_state,
            "task_name": task_id,
            "loop_count": bot._loop_count,
            "last_loop_time": self._format_timestamp(bot._last_loop_time),
            "last_error": self._errors.get(symbol),
        }

    def _refresh_heartbeat(self) -> None:
        """Derive heartbeat from the most recent bot loop iteration."""
        latest: Optional[float] = None
        for bot in self._bots.values():
            t = bot._last_loop_time
            if t is not None and (latest is None or t > latest):
                latest = t
        if latest is not None:
            self._last_heartbeat = latest

    def recalculate_gate(self) -> None:
        """Recalculate gate count from actual open positions across all bots."""
        actual = 0
        per_symbol: Dict[str, int] = {}
        for symbol, bot in self._bots.items():
            count = self._actual_position_count(bot)
            per_symbol[symbol] = count
            actual += count
        self._open_trade_gate._count = actual
        self._open_trade_gate._registered = per_symbol
        logger.info(
            "Gate recalculated: count=%d symbols=%s",
            actual, per_symbol,
        )

    def sync_risk_counters(self) -> None:
        """Synchronize risk_manager.open_trades with actual position counts.

        After restart or position close events, the per-symbol risk counter
        can drift from reality. This method corrects it.
        """
        for symbol, bot in self._bots.items():
            actual_count = self._actual_position_count(bot)
            rm = bot.risk_manager
            if rm.open_trades != actual_count:
                logger.warning(
                    "Risk counter drift: symbol=%s open_trades=%d actual=%d; correcting",
                    symbol, rm.open_trades, actual_count,
                )
                rm.open_trades = actual_count
        self.recalculate_gate()

    def get_engine_diagnostics(self) -> dict:
        """Return engine-level diagnostics."""
        self._refresh_heartbeat()
        healthy = 0
        errored = 0
        for symbol in self._bots:
            h = self.get_health_status(symbol)
            if h == "HEALTHY":
                healthy += 1
            elif h in ("ERROR", "STOPPED"):
                errored += 1

        return {
            "type": "MultiSymbolEngine",
            "status": "RUNNING" if self.running else "STOPPED",
            "bot_count": len(self._bots),
            "task_count": len(self._tasks),
            "healthy_count": healthy,
            "error_count": errored,
            "started_at": self._started_at,
            "uptime": self._format_uptime(),
            "last_heartbeat": self._format_timestamp(self._last_heartbeat),
            "gate_count": self._open_trade_gate.count,
            "gate_limit": self._open_trade_gate.limit,
            "gate_registered": self._open_trade_gate.registered,
        }
