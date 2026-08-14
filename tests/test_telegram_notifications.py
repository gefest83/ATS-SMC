"""Tests for Telegram notification system with Russian messages."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.monitoring.telegram import (
    TelegramNotifier,
    _fmt_price,
    _translate_reason,
)


# ── Helper Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def notifier():
    """Create a TelegramNotifier with bot mocked out."""
    with patch.object(TelegramNotifier, "__init__", lambda self: None):
        n = TelegramNotifier()
        n.bot = MagicMock()
        n._initialized = True
        n._lifecycle_lock = asyncio.Lock()
        n._sent_events = set()
        n._dedup_window = 3600
        n._event_timestamps = {}
        n._send = AsyncMock()
        return n


@pytest.fixture
def notifier_no_bot():
    """Create a TelegramNotifier with no bot (simulation mode)."""
    with patch.object(TelegramNotifier, "__init__", lambda self: None):
        n = TelegramNotifier()
        n.bot = None
        n._initialized = False
        n._lifecycle_lock = asyncio.Lock()
        n._sent_events = set()
        n._dedup_window = 3600
        n._event_timestamps = {}
        n._send = AsyncMock()
        return n


# ── Helper Functions Tests ───────────────────────────────────────────────


class TestHelperFunctions:
    def test_fmt_price_large(self):
        assert _fmt_price(64139.99) == "64139.99"

    def test_fmt_price_medium(self):
        assert _fmt_price(1.5) == "1.5000"

    def test_fmt_price_small(self):
        assert _fmt_price(0.001234) == "0.001234"

    def test_fmt_price_none(self):
        assert _fmt_price(None) == "Н/Д"

    def test_fmt_price_zero(self):
        assert _fmt_price(0) == "0.00"

    def test_fmt_price_string(self):
        assert _fmt_price("abc") == "abc"

    def test_translate_reason_sl(self):
        assert _translate_reason("stop_loss") == "Stop Loss"

    def test_translate_reason_tp(self):
        assert _translate_reason("take_profit") == "Take Profit"

    def test_translate_reason_stale(self):
        assert _translate_reason("stale_not_on_exchange") == "stale_not_on_exchange"

    def test_translate_reason_manual(self):
        assert _translate_reason("manual") == "Ручное закрытие"

    def test_translate_reason_unknown(self):
        assert _translate_reason("something_else") == "something_else"


# ── Deduplication Tests ─────────────────────────────────────────────────


class TestDeduplication:
    def test_is_duplicate_first_call(self, notifier):
        assert notifier._is_duplicate("test_key") is False

    def test_is_duplicate_second_call(self, notifier):
        notifier._is_duplicate("test_key")
        assert notifier._is_duplicate("test_key") is True

    def test_different_keys_not_duplicate(self, notifier):
        notifier._is_duplicate("key1")
        assert notifier._is_duplicate("key2") is False

    def test_clear_dedup_allows_resend(self, notifier):
        notifier._is_duplicate("test_key")
        notifier._clear_dedup("test_key")
        assert notifier._is_duplicate("test_key") is False


# ── Startup Notification Tests ──────────────────────────────────────────


class TestStartupNotification:
    @pytest.mark.asyncio
    async def test_startup_sends_message(self, notifier):
        await notifier.notify_engine_started(
            symbols=["BTC/USDT", "ETH/USDT"],
            exchange="binance",
            timeframe="15m",
            bot_count=2,
            risk_per_trade=0.01,
            max_open_trades=3,
            started_at="12:34:56",
        )
        notifier._send.assert_called_once()
        msg = notifier._send.call_args[0][0]
        assert "ATS-SMC ЗАПУЩЕН" in msg
        assert "Режим:" in msg
        assert "BTC/USDT, ETH/USDT" in msg
        assert "15m" in msg
        assert "12:34:56" in msg

    @pytest.mark.asyncio
    async def test_startup_no_duplicate(self, notifier):
        await notifier.notify_engine_started(
            symbols=["BTC/USDT"],
            exchange="binance",
            timeframe="15m",
            bot_count=1,
            risk_per_trade=0.01,
            max_open_trades=3,
            started_at="12:00:00",
        )
        notifier._send.reset_mock()
        await notifier.notify_engine_started(
            symbols=["BTC/USDT"],
            exchange="binance",
            timeframe="15m",
            bot_count=1,
            risk_per_trade=0.01,
            max_open_trades=3,
            started_at="12:00:00",
        )
        notifier._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_startup_different_time_not_duplicate(self, notifier):
        await notifier.notify_engine_started(
            symbols=["BTC/USDT"],
            exchange="binance",
            timeframe="15m",
            bot_count=1,
            risk_per_trade=0.01,
            max_open_trades=3,
            started_at="12:00:00",
        )
        notifier._send.reset_mock()
        await notifier.notify_engine_started(
            symbols=["BTC/USDT"],
            exchange="binance",
            timeframe="15m",
            bot_count=1,
            risk_per_trade=0.01,
            max_open_trades=3,
            started_at="12:01:00",
        )
        notifier._send.assert_called_once()


# ── Shutdown Notification Tests ─────────────────────────────────────────


class TestShutdownNotification:
    @pytest.mark.asyncio
    async def test_shutdown_sends_message(self, notifier):
        await notifier.notify_engine_stopped(
            open_positions=2,
            reason="Остановка через API",
        )
        notifier._send.assert_called_once()
        msg = notifier._send.call_args[0][0]
        assert "ATS-SMC ОСТАНОВЛЕН" in msg
        assert "Открытых позиций: 2" in msg
        assert "Остановка через API" in msg

    @pytest.mark.asyncio
    async def test_shutdown_always_sends(self, notifier):
        """Each shutdown should send a notification (different events)."""
        await notifier.notify_engine_stopped(open_positions=0)
        notifier._send.reset_mock()
        await notifier.notify_engine_stopped(open_positions=0)
        # Shutdown uses time-based key, so consecutive calls send independently
        notifier._send.assert_called_once()


# ── Signal Notification Tests ───────────────────────────────────────────


class TestSignalNotification:
    @pytest.mark.asyncio
    async def test_signal_sends_message(self, notifier):
        await notifier.notify_signal(
            symbol="BTC/USDT",
            side="BUY",
            timeframe="15m",
            signal_price=64139.99,
            sl=64000.0,
            tp1=64200.0,
            tp2=64300.0,
            tp3=64400.0,
            risk_pct=0.01,
        )
        notifier._send.assert_called_once()
        msg = notifier._send.call_args[0][0]
        assert "ТОРГОВЫЙ СИГНАЛ" in msg
        assert "BTC/USDT" in msg
        assert "BUY" in msg
        assert "15m" in msg
        assert "64139.99" in msg

    @pytest.mark.asyncio
    async def test_signal_sell(self, notifier):
        await notifier.notify_signal(
            symbol="ETH/USDT",
            side="SELL",
            timeframe="5m",
            signal_price=3500.0,
            sl=3550.0,
            tp1=3450.0,
            tp2=3400.0,
            tp3=3350.0,
            risk_pct=0.02,
        )
        msg = notifier._send.call_args[0][0]
        assert "SELL" in msg
        assert "ETH/USDT" in msg


# ── Order Opened Notification Tests ─────────────────────────────────────


class TestOrderOpenedNotification:
    @pytest.mark.asyncio
    async def test_order_opened_sends_message(self, notifier):
        await notifier.notify_order_opened(
            symbol="BTC/USDT",
            side="SELL",
            quantity=1.156970,
            entry_price=64139.99,
            sl=64217.87,
            tp1=64083.89,
            tp2=64030.29,
            tp3=63976.70,
            risk_pct=0.01,
            position_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        )
        notifier._send.assert_called_once()
        msg = notifier._send.call_args[0][0]
        assert "ОРДЕР ОТКРЫТ" in msg
        assert "BTC/USDT" in msg
        assert "SELL" in msg
        assert "1.156970" in msg
        assert "a1b2c3d4" in msg

    @pytest.mark.asyncio
    async def test_order_opened_dedup_by_position_id(self, notifier):
        await notifier.notify_order_opened(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            sl=49000.0,
            tp1=51000.0,
            tp2=52000.0,
            tp3=53000.0,
            risk_pct=0.01,
            position_id="pos-123",
        )
        notifier._send.reset_mock()
        await notifier.notify_order_opened(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            sl=49000.0,
            tp1=51000.0,
            tp2=52000.0,
            tp3=53000.0,
            risk_pct=0.01,
            position_id="pos-123",
        )
        notifier._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_order_opened_different_positions(self, notifier):
        await notifier.notify_order_opened(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            sl=49000.0,
            tp1=51000.0,
            tp2=52000.0,
            tp3=53000.0,
            risk_pct=0.01,
            position_id="pos-aaa",
        )
        notifier._send.reset_mock()
        await notifier.notify_order_opened(
            symbol="ETH/USDT",
            side="SELL",
            quantity=1.0,
            entry_price=3500.0,
            sl=3600.0,
            tp1=3400.0,
            tp2=3300.0,
            tp3=3200.0,
            risk_pct=0.01,
            position_id="pos-bbb",
        )
        notifier._send.assert_called_once()


# ── Signal Blocked Notification Tests ───────────────────────────────────


class TestSignalBlockedNotification:
    @pytest.mark.asyncio
    async def test_blocked_position_active(self, notifier):
        await notifier.notify_signal_blocked(
            symbol="BTC/USDT",
            side="BUY",
            reason="уже существует открытая позиция",
        )
        notifier._send.assert_called_once()
        msg = notifier._send.call_args[0][0]
        assert "СИГНАЛ ЗАБЛОКИРОВАН" in msg
        assert "BTC/USDT" in msg
        assert "уже существует открытая позиция" in msg

    @pytest.mark.asyncio
    async def test_blocked_max_trades(self, notifier):
        await notifier.notify_signal_blocked(
            symbol="ETH/USDT",
            side="SELL",
            reason="достигнут лимит открытых сделок",
        )
        msg = notifier._send.call_args[0][0]
        assert "достигнут лимит открытых сделок" in msg

    @pytest.mark.asyncio
    async def test_blocked_different_reasons_not_deduped(self, notifier):
        await notifier.notify_signal_blocked(
            symbol="BTC/USDT",
            side="BUY",
            reason="reason_a",
        )
        notifier._send.reset_mock()
        await notifier.notify_signal_blocked(
            symbol="BTC/USDT",
            side="BUY",
            reason="reason_b",
        )
        notifier._send.assert_called_once()


# ── Position Closed Notification Tests ──────────────────────────────────


class TestPositionClosedNotification:
    @pytest.mark.asyncio
    async def test_closed_profitable(self, notifier):
        await notifier.notify_position_closed(
            symbol="BTC/USDT",
            side="SELL",
            quantity=1.0,
            entry_price=64139.99,
            exit_price=64020.00,
            close_reason="take_profit",
            realized_pnl=Decimal("119.99"),
            position_id="pos-001",
            entry_time=1000000.0,
            close_time=1001440.0,
        )
        notifier._send.assert_called_once()
        msg = notifier._send.call_args[0][0]
        assert "СДЕЛКА ЗАКРЫТА" in msg
        assert "BTC/USDT" in msg
        assert "SELL" in msg
        assert "Take Profit" in msg
        assert "119.99" in msg
        assert "24 мин" in msg

    @pytest.mark.asyncio
    async def test_closed_loss(self, notifier):
        await notifier.notify_position_closed(
            symbol="ETH/USDT",
            side="BUY",
            quantity=2.0,
            entry_price=3500.0,
            exit_price=3450.0,
            close_reason="stop_loss",
            realized_pnl=Decimal("-100.00"),
            position_id="pos-002",
        )
        msg = notifier._send.call_args[0][0]
        assert "-100.00" in msg
        assert "Stop Loss" in msg

    @pytest.mark.asyncio
    async def test_closed_unknown_pnl(self, notifier):
        await notifier.notify_position_closed(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            exit_price=None,
            close_reason="stale_not_on_exchange",
            realized_pnl=None,
            position_id="pos-003",
        )
        msg = notifier._send.call_args[0][0]
        assert "Н/Д" in msg
        assert "stale_not_on_exchange" in msg

    @pytest.mark.asyncio
    async def test_closed_dedup_by_position_id(self, notifier):
        await notifier.notify_position_closed(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            exit_price=51000.0,
            close_reason="take_profit",
            realized_pnl=Decimal("100"),
            position_id="pos-unique",
        )
        notifier._send.reset_mock()
        await notifier.notify_position_closed(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            exit_price=51000.0,
            close_reason="take_profit",
            realized_pnl=Decimal("100"),
            position_id="pos-unique",
        )
        notifier._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_closed_clears_open_dedup(self, notifier):
        """After position closed, same position_id should be able to open again."""
        await notifier.notify_order_opened(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            sl=49000.0,
            tp1=51000.0,
            tp2=52000.0,
            tp3=53000.0,
            risk_pct=0.01,
            position_id="pos-recycle",
        )
        notifier._send.reset_mock()
        await notifier.notify_position_closed(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            exit_price=51000.0,
            close_reason="take_profit",
            realized_pnl=Decimal("100"),
            position_id="pos-recycle",
        )
        notifier._send.assert_called_once()
        # Now open again with same ID should work
        notifier._send.reset_mock()
        await notifier.notify_order_opened(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            sl=49000.0,
            tp1=51000.0,
            tp2=52000.0,
            tp3=53000.0,
            risk_pct=0.01,
            position_id="pos-recycle",
        )
        notifier._send.assert_called_once()


# ── Simulation Mode Tests ───────────────────────────────────────────────


class TestSimulationMode:
    @pytest.mark.asyncio
    async def test_no_bot_still_calls_send(self, notifier_no_bot):
        """In simulation mode, _send should still be called for logging."""
        await notifier_no_bot.notify_signal(
            symbol="BTC/USDT",
            side="BUY",
            timeframe="15m",
            signal_price=50000.0,
            sl=49000.0,
            tp1=51000.0,
            tp2=52000.0,
            tp3=53000.0,
            risk_pct=0.01,
        )
        # _send is mocked, so it should have been called
        notifier_no_bot._send.assert_called_once()


# ── Multi-Symbol Tests ──────────────────────────────────────────────────


class TestMultiSymbol:
    @pytest.mark.asyncio
    async def test_concurrent_notifications_different_symbols(self, notifier):
        """Multiple symbols should send independent notifications."""
        await notifier.notify_signal(
            symbol="BTC/USDT", side="BUY", timeframe="15m",
            signal_price=50000, sl=49000, tp1=51000, tp2=52000, tp3=53000,
            risk_pct=0.01,
        )
        await notifier.notify_signal(
            symbol="ETH/USDT", side="SELL", timeframe="15m",
            signal_price=3500, sl=3600, tp1=3400, tp2=3300, tp3=3200,
            risk_pct=0.01,
        )
        await notifier.notify_signal(
            symbol="SOL/USDT", side="BUY", timeframe="15m",
            signal_price=150, sl=145, tp1=155, tp2=160, tp3=165,
            risk_pct=0.01,
        )
        assert notifier._send.call_count == 3

    @pytest.mark.asyncio
    async def test_same_symbol_different_events(self, notifier):
        """Same symbol can have different event types."""
        await notifier.notify_signal(
            symbol="BTC/USDT", side="BUY", timeframe="15m",
            signal_price=50000, sl=49000, tp1=51000, tp2=52000, tp3=53000,
            risk_pct=0.01,
        )
        await notifier.notify_order_opened(
            symbol="BTC/USDT", side="BUY", quantity=0.1,
            entry_price=50000, sl=49000, tp1=51000, tp2=52000, tp3=53000,
            risk_pct=0.01, position_id="pos-1",
        )
        assert notifier._send.call_count == 2

    @pytest.mark.asyncio
    async def test_full_lifecycle_one_position(self, notifier):
        """Test complete lifecycle: signal -> open -> close."""
        await notifier.notify_signal(
            symbol="BTC/USDT", side="BUY", timeframe="15m",
            signal_price=50000, sl=49000, tp1=51000, tp2=52000, tp3=53000,
            risk_pct=0.01,
        )
        await notifier.notify_order_opened(
            symbol="BTC/USDT", side="BUY", quantity=0.1,
            entry_price=50000, sl=49000, tp1=51000, tp2=52000, tp3=53000,
            risk_pct=0.01, position_id="pos-lifecycle",
        )
        await notifier.notify_position_closed(
            symbol="BTC/USDT", side="BUY", quantity=0.1,
            entry_price=50000, exit_price=51000,
            close_reason="take_profit",
            realized_pnl=Decimal("100"),
            position_id="pos-lifecycle",
        )
        assert notifier._send.call_count == 3


# ── Edge Cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_signal_blocked_same_signal_twice(self, notifier):
        """Same blocked signal should only send once."""
        await notifier.notify_signal_blocked(
            symbol="BTC/USDT",
            side="BUY",
            reason="уже существует открытая позиция",
        )
        notifier._send.reset_mock()
        await notifier.notify_signal_blocked(
            symbol="BTC/USDT",
            side="BUY",
            reason="уже существует открытая позиция",
        )
        notifier._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_position_closed_short_duration(self, notifier):
        """Test short duration formatting."""
        await notifier.notify_position_closed(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            exit_price=50100.0,
            close_reason="take_profit",
            realized_pnl=Decimal("10"),
            position_id="pos-short",
            entry_time=1000000.0,
            close_time=1000030.0,
        )
        msg = notifier._send.call_args[0][0]
        assert "30 сек" in msg

    @pytest.mark.asyncio
    async def test_position_closed_long_duration(self, notifier):
        """Test long duration formatting."""
        await notifier.notify_position_closed(
            symbol="BTC/USDT",
            side="BUY",
            quantity=0.1,
            entry_price=50000.0,
            exit_price=50100.0,
            close_reason="take_profit",
            realized_pnl=Decimal("10"),
            position_id="pos-long",
            entry_time=1000000.0,
            close_time=1007200.0,
        )
        msg = notifier._send.call_args[0][0]
        assert "2 ч 0 мин" in msg
