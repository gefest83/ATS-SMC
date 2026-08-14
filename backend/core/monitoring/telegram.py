"""Telegram notifications with an explicit async lifecycle.

Provides structured notification methods for all trading events with:
- Russian-language user-facing messages
- Duplicate protection via event deduplication
- Proper formatting for each notification type
"""
from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Optional, Set

from backend.config import settings

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self):
        self.bot = None
        self._initialized = False
        self._lifecycle_lock = asyncio.Lock()
        # Deduplication: track sent event keys to prevent duplicate notifications
        self._sent_events: Set[str] = set()
        self._dedup_window = 3600  # 1 hour window for dedup
        self._event_timestamps: dict = {}
        if settings.TELEGRAM_BOT_TOKEN:
            from telegram import Bot
            self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.bot and not self._initialized:
                await self.bot.initialize()
                self._initialized = True

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self.bot and self._initialized:
                try:
                    await self.bot.shutdown()
                finally:
                    self._initialized = False

    def _is_duplicate(self, event_key: str) -> bool:
        """Check if this event was already sent within the dedup window."""
        now = time.monotonic()
        # Clean old entries
        expired = [k for k, t in self._event_timestamps.items() if now - t > self._dedup_window]
        for k in expired:
            self._event_timestamps.pop(k, None)
            self._sent_events.discard(k)
        if event_key in self._sent_events:
            return True
        self._sent_events.add(event_key)
        self._event_timestamps[event_key] = now
        return False

    def _clear_dedup(self, event_key: str) -> None:
        """Remove an event from dedup tracking (e.g., after position closed)."""
        self._sent_events.discard(event_key)
        self._event_timestamps.pop(event_key, None)

    async def send_alert(self, message: str) -> None:
        """Legacy method for backward compatibility."""
        if not (self.bot and settings.TELEGRAM_CHAT_ID):
            logger.info("[TELEGRAM SIMULATION] %s", message)
            return
        try:
            await self.start()
            await self.bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=message)
        except Exception as exc:
            logger.error("Telegram error: %s", exc)

    async def _send(self, message: str) -> None:
        """Internal send method."""
        if not (self.bot and settings.TELEGRAM_CHAT_ID):
            logger.info("[TELEGRAM SIMULATION] %s", message)
            return
        try:
            await self.start()
            await self.bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=message)
        except Exception as exc:
            logger.error("Telegram send error: %s", exc)

    # ── 1. STARTUP NOTIFICATION ──────────────────────────────────────────

    async def notify_engine_started(
        self,
        symbols: list,
        exchange: str,
        timeframe: str,
        bot_count: int,
        risk_per_trade: float,
        max_open_trades: int,
        started_at: str,
    ) -> None:
        """Notify that the engine has started successfully."""
        event_key = f"engine_started:{','.join(sorted(symbols))}:{started_at}"
        if self._is_duplicate(event_key):
            return
        mode = settings.TRADING_MODE.upper()
        symbols_str = ", ".join(symbols)
        msg = (
            f"🤖 ATS-SMC ЗАПУЩЕН\n"
            f"\n"
            f"Режим: {mode}\n"
            f"Биржа: {exchange}\n"
            f"Торговых пар: {bot_count}\n"
            f"Пары: {symbols_str}\n"
            f"Таймфрейм: {timeframe}\n"
            f"Риск на сделку: {risk_per_trade:.0%}\n"
            f"Максимум открытых сделок: {max_open_trades}\n"
            f"\n"
            f"Статус: РАБОТАЕТ\n"
            f"\n"
            f"Время запуска: {started_at}"
        )
        await self._send(msg)

    # ── 2. SHUTDOWN NOTIFICATION ─────────────────────────────────────────

    async def notify_engine_stopped(
        self,
        open_positions: int,
        reason: str = "Ручная остановка",
    ) -> None:
        """Notify that the engine has stopped."""
        event_key = f"engine_stopped:{time.monotonic()}"
        if self._is_duplicate(event_key):
            return
        mode = settings.TRADING_MODE.upper()
        msg = (
            f"🛑 ATS-SMC ОСТАНОВЛЕН\n"
            f"\n"
            f"Режим: {mode}\n"
            f"Биржа: {settings.EXCHANGE}\n"
            f"\n"
            f"Статус: ОСТАНОВЛЕН\n"
            f"\n"
            f"Открытых позиций: {open_positions}\n"
            f"Причина: {reason}"
        )
        await self._send(msg)

    # ── 3. SIGNAL NOTIFICATION ───────────────────────────────────────────

    async def notify_signal(
        self,
        symbol: str,
        side: str,
        timeframe: str,
        signal_price: float,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        risk_pct: float,
        score: int = 0,
    ) -> None:
        """Notify that a trading signal was received."""
        event_key = f"signal:{symbol}:{side}:{time.monotonic()}"
        if self._is_duplicate(event_key):
            return
        msg = (
            f"📡 ТОРГОВЫЙ СИГНАЛ\n"
            f"\n"
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"Таймфрейм: {timeframe}\n"
            f"Score: {score}\n"
            f"\n"
            f"Entry: {_fmt_price(signal_price)}\n"
            f"\n"
            f"Stop Loss: {_fmt_price(sl)}\n"
            f"TP1: {_fmt_price(tp1)}\n"
            f"TP2: {_fmt_price(tp2)}\n"
            f"TP3: {_fmt_price(tp3)}\n"
            f"\n"
            f"Риск: {risk_pct:.0%}"
        )
        await self._send(msg)

    # ── 4. ORDER OPENED NOTIFICATION ─────────────────────────────────────

    async def notify_order_opened(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        risk_pct: float,
        position_id: str,
    ) -> None:
        """Notify that an order was successfully opened."""
        short_id = position_id[:8] if position_id else "?"
        event_key = f"order_opened:{position_id}"
        if self._is_duplicate(event_key):
            return
        mode = settings.TRADING_MODE.upper()
        msg = (
            f"🟢 ОРДЕР ОТКРЫТ\n"
            f"\n"
            f"Режим: {mode}\n"
            f"\n"
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"Количество: {quantity:.6f}\n"
            f"\n"
            f"Цена входа: {_fmt_price(entry_price)}\n"
            f"\n"
            f"Stop Loss: {_fmt_price(sl)}\n"
            f"TP1: {_fmt_price(tp1)}\n"
            f"TP2: {_fmt_price(tp2)}\n"
            f"TP3: {_fmt_price(tp3)}\n"
            f"\n"
            f"Риск: {risk_pct:.0%}\n"
            f"ID позиции: {short_id}"
        )
        await self._send(msg)

    # ── 5. SIGNAL BLOCKED NOTIFICATION ───────────────────────────────────

    async def notify_signal_blocked(
        self,
        symbol: str,
        side: str,
        reason: str,
    ) -> None:
        """Notify that a signal was blocked."""
        event_key = f"signal_blocked:{symbol}:{side}:{reason}"
        if self._is_duplicate(event_key):
            return
        msg = (
            f"⚠️ СИГНАЛ ЗАБЛОКИРОВАН\n"
            f"\n"
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"\n"
            f"Причина: {reason}"
        )
        await self._send(msg)

    # ── 6. POSITION CLOSED NOTIFICATION ──────────────────────────────────

    async def notify_position_closed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: Optional[float],
        close_reason: str,
        realized_pnl: Optional[Decimal],
        position_id: str,
        entry_time: Optional[float] = None,
        close_time: Optional[float] = None,
    ) -> None:
        """Notify that a position was closed."""
        short_id = position_id[:8] if position_id else "?"
        event_key = f"position_closed:{position_id}"
        if self._is_duplicate(event_key):
            return
        # Clear the open dedup so same symbol can open again
        self._clear_dedup(f"order_opened:{position_id}")

        mode = settings.TRADING_MODE.upper()

        # Calculate duration
        duration_str = ""
        if entry_time and close_time:
            elapsed = int(close_time - entry_time)
            if elapsed < 60:
                duration_str = f"{elapsed} сек"
            elif elapsed < 3600:
                duration_str = f"{elapsed // 60} мин"
            else:
                hours = elapsed // 3600
                mins = (elapsed % 3600) // 60
                duration_str = f"{hours} ч {mins} мин"

        # Format PnL
        if realized_pnl is not None:
            pnl_val = float(realized_pnl)
            pnl_str = f"{'+' if pnl_val >= 0 else ''}{pnl_val:.2f} USDT"
            # Calculate percentage
            if entry_price > 0 and quantity > 0:
                cost = entry_price * quantity
                if cost > 0:
                    pnl_pct = (pnl_val / cost) * 100
                    result_str = f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%"
                else:
                    result_str = "Н/Д"
            else:
                result_str = "Н/Д"
        else:
            pnl_str = "Н/Д"
            result_str = "Н/Д"

        exit_str = _fmt_price(exit_price) if exit_price else "Н/Д"

        msg = (
            f"🔴 СДЕЛКА ЗАКРЫТА\n"
            f"\n"
            f"Режим: {mode}\n"
            f"\n"
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"\n"
            f"Количество: {quantity:.6f}\n"
            f"\n"
            f"Цена входа: {_fmt_price(entry_price)}\n"
            f"Цена выхода: {exit_str}\n"
            f"\n"
            f"Причина закрытия: {_translate_reason(close_reason)}\n"
            f"\n"
            f"Прибыль/убыток: {pnl_str}\n"
            f"Результат: {result_str}"
        )
        if duration_str:
            msg += f"\n\nДлительность сделки: {duration_str}"
        msg += f"\n\nID позиции: {short_id}"
        await self._send(msg)


# ── HELPER FUNCTIONS ─────────────────────────────────────────────────────


def _fmt_price(price) -> str:
    """Format price for display."""
    if price is None:
        return "Н/Д"
    try:
        val = float(price)
        if val == 0:
            return "0.00"
        if val >= 1000:
            return f"{val:.2f}"
        if val >= 1:
            return f"{val:.4f}"
        return f"{val:.6f}"
    except (TypeError, ValueError):
        return str(price)


def _translate_reason(reason: str) -> str:
    """Translate close reasons to Russian."""
    reasons = {
        "stop_loss": "Stop Loss",
        "take_profit": "Take Profit",
        "tp1": "TP1",
        "tp2": "TP2",
        "tp3": "TP3",
        "stale_not_on_exchange": "stale_not_on_exchange",
        "spot_balance_zero": "spot_balance_zero",
        "exchange_trade_reconciliation": "exchange_trade_reconciliation",
        "quantity_depleted": "quantity_depleted",
        "manual": "Ручное закрытие",
    }
    return reasons.get(reason, reason)
