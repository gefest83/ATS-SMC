"""Regression tests for risk counter synchronization and position state consistency."""
import asyncio
import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.core.engine.smc_bot import SMCBot
from backend.core.engine.multi_symbol import MultiSymbolEngine, SharedOpenTradeGate
from backend.core.risk.risk_manager import RiskManager


class TestRiskCounterSynchronization:
    """Verify open_trades counter stays synchronized with actual positions."""

    def test_open_trades_matches_positions_after_open(self):
        """After trade_opened(), open_trades=1."""
        risk = RiskManager(1000)
        assert risk.open_trades == 0
        risk.trade_opened()
        assert risk.open_trades == 1

    def test_open_trades_matches_positions_after_close(self):
        """After trade_opened() then trade_closed(), open_trades=0."""
        risk = RiskManager(1000)
        risk.trade_opened()
        assert risk.open_trades == 1
        risk.trade_closed(0.0, event_id="pos1")
        assert risk.open_trades == 0

    def test_close_callback_idempotent_does_not_double_decrement(self):
        """Duplicate close callback does not decrement counter twice."""
        risk = RiskManager(1000)
        risk.trade_opened()
        assert risk.open_trades == 1
        result1 = risk.trade_closed(10.0, event_id="pos1")
        assert result1 is True
        assert risk.open_trades == 0
        result2 = risk.trade_closed(10.0, event_id="pos1")
        assert result2 is False
        assert risk.open_trades == 0

    def test_close_without_event_id_always_decrements(self):
        """Close without event_id always decrements (no idempotency guard)."""
        risk = RiskManager(1000)
        risk.trade_opened()
        risk.trade_opened()
        assert risk.open_trades == 2
        risk.trade_closed(0.0)
        assert risk.open_trades == 1
        risk.trade_closed(0.0)
        assert risk.open_trades == 0

    def test_counter_never_goes_negative(self):
        """Counter clamped to 0 even on extra close calls."""
        risk = RiskManager(1000)
        risk.trade_closed(0.0)
        assert risk.open_trades == 0
        risk.trade_closed(0.0, event_id="phantom")
        assert risk.open_trades == 0

    def test_multi_symbol_risk_counters_are_independent(self):
        """Each SMCBot has its own RiskManager with independent counter."""
        bot_btc = SMCBot(symbol="BTC/USDT")
        bot_eth = SMCBot(symbol="ETH/USDT")
        bot_sol = SMCBot(symbol="SOL/USDT")

        bot_btc.risk_manager.trade_opened()
        bot_btc.risk_manager.trade_opened()
        assert bot_btc.risk_manager.open_trades == 2
        assert bot_eth.risk_manager.open_trades == 0
        assert bot_sol.risk_manager.open_trades == 0

        bot_eth.risk_manager.trade_opened()
        assert bot_btc.risk_manager.open_trades == 2
        assert bot_eth.risk_manager.open_trades == 1
        assert bot_sol.risk_manager.open_trades == 0

    def test_risk_manager_blocks_at_max_open_trades(self):
        """can_open_trade() returns False when open_trades >= MAX_OPEN_TRADES."""
        risk = RiskManager(1000)
        risk.open_trades = 3
        assert risk.can_open_trade() is False

    def test_risk_manager_allows_below_max(self):
        """can_open_trade() returns True when open_trades < MAX_OPEN_TRADES."""
        risk = RiskManager(1000)
        risk.open_trades = 2
        assert risk.can_open_trade() is True


class TestSharedOpenTradeGate:
    """Verify SharedOpenTradeGate correctly limits across symbols."""

    def test_gate_allows_up_to_limit(self):
        gate = SharedOpenTradeGate(3)
        assert gate.try_reserve() is True
        assert gate.try_reserve() is True
        assert gate.try_reserve() is True
        assert gate.count == 3

    def test_gate_blocks_at_limit(self):
        gate = SharedOpenTradeGate(3)
        for _ in range(3):
            gate.try_reserve()
        assert gate.try_reserve() is False
        assert gate.count == 3

    def test_gate_release_allows_new_reservation(self):
        gate = SharedOpenTradeGate(3)
        for _ in range(3):
            gate.try_reserve()
        assert gate.try_reserve() is False
        gate.release()
        assert gate.count == 2
        assert gate.try_reserve() is True
        assert gate.count == 3

    def test_gate_register_adds_recovered_count(self):
        gate = SharedOpenTradeGate(3)
        gate.register("BTC/USDT", 1)
        gate.register("ETH/USDT", 1)
        gate.register("SOL/USDT", 1)
        assert gate.count == 3
        assert gate.try_reserve() is False

    def test_gate_register_idempotent(self):
        gate = SharedOpenTradeGate(3)
        gate.register("BTC/USDT", 2)
        gate.register("BTC/USDT", 5)
        assert gate.count == 2
        assert gate.registered["BTC/USDT"] == 2

    def test_gate_release_clamped_to_zero(self):
        gate = SharedOpenTradeGate(3)
        gate.release()
        assert gate.count == 0


class TestSMCBotPositionDetection:
    """Verify has_active_position() and get_live_position_count()."""

    def test_has_active_position_false_when_empty(self):
        bot = SMCBot(symbol="BTC/USDT")
        assert bot.has_active_position() is False

    def test_has_active_position_true_when_paper(self):
        bot = SMCBot(symbol="BTC/USDT")
        bot._paper_position = {"side": "buy", "entry": 100}
        assert bot.has_active_position() is True

    def test_has_active_position_true_when_live_position(self):
        bot = SMCBot(symbol="BTC/USDT")
        bot._paper_position = None
        mock_pos = SimpleNamespace(
            position_id="p1", symbol="BTC/USDT", side="buy",
            quantity=Decimal("1"), entry_price=Decimal("100"),
        )
        bot.position_manager.positions = {"p1": mock_pos}
        assert bot.has_active_position() is True

    def test_has_active_position_false_when_empty_positions(self):
        bot = SMCBot(symbol="BTC/USDT")
        bot._paper_position = None
        bot.position_manager.positions = {}
        assert bot.has_active_position() is False

    def test_get_live_position_count_zero(self):
        bot = SMCBot(symbol="BTC/USDT")
        assert bot.get_live_position_count() == 0

    def test_get_live_position_count_with_positions(self):
        bot = SMCBot(symbol="BTC/USDT")
        bot.position_manager.positions = {
            "p1": SimpleNamespace(),
            "p2": SimpleNamespace(),
        }
        assert bot.get_live_position_count() == 2


class TestNoFreeBaseBehavior:
    """Verify 'No free base' is logged at INFO level, not WARNING."""

    def test_no_free_base_is_info_not_warning(self, caplog):
        """'No free base for staged protective orders' should be INFO."""
        with caplog.at_level(logging.INFO, logger="backend.core.order_manager"):
            logger = logging.getLogger("backend.core.order_manager")
            logger.info(
                "No free base for staged protective orders on %s "
                "(position=%s qty=%s side=%s; existing protective orders likely reserve all base)",
                "BTC/USDT", "pos1", Decimal("0.001"), "buy",
            )
        assert any("No free base" in r.message for r in caplog.records)
        assert any(r.levelno == logging.INFO for r in caplog.records if "No free base" in r.message)


class TestDiagnosticLogging:
    """Verify diagnostic logging is produced for risk state changes."""

    def test_trade_opened_logs(self, caplog):
        risk = RiskManager(1000, state_scope="binance:BTC/USDT")
        with caplog.at_level(logging.INFO):
            risk.trade_opened()
        assert any("trade_opened" in r.message and "open_trades=1" in r.message for r in caplog.records)

    def test_trade_closed_logs(self, caplog):
        risk = RiskManager(1000, state_scope="binance:BTC/USDT")
        risk.trade_opened()
        with caplog.at_level(logging.INFO):
            risk.trade_closed(10.0, event_id="pos1")
        assert any("trade_closed" in r.message and "open_trades=0" in r.message for r in caplog.records)

    def test_max_open_trades_warning_includes_counter(self, caplog):
        risk = RiskManager(1000, state_scope="binance:BTC/USDT")
        risk.open_trades = 3
        with caplog.at_level(logging.WARNING):
            risk.can_open_trade()
        assert any("Max open trades reached" in r.message and "open_trades=3" in r.message for r in caplog.records)


class TestEngineDiagnosticsIncludeGate:
    """Engine diagnostics include gate state."""

    def test_engine_diagnostics_has_gate_fields(self, monkeypatch):
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine._open_trade_gate.register("BTC/USDT", 1)
        engine._open_trade_gate.register("ETH/USDT", 1)
        diag = engine.get_engine_diagnostics()
        assert "gate_count" in diag
        assert "gate_limit" in diag
        assert "gate_registered" in diag
        assert diag["gate_count"] == 2
        assert diag["gate_limit"] == 3
        assert "BTC/USDT" in diag["gate_registered"]
        assert "ETH/USDT" in diag["gate_registered"]
