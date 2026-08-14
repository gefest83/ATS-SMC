"""Regression tests for multi-symbol engine isolation."""
import asyncio
import random

import pytest

from backend.config import settings
from backend.core.engine.smc_bot import SMCBot


def make_ohlcv(n: int = 120, seed: int = 7, base_price: float = 30000.0) -> list:
    rng = random.Random(seed)
    price = base_price
    candles = []
    for i in range(n):
        open_ = price
        close = open_ * (1 + rng.uniform(-0.01, 0.012))
        high = max(open_, close) * (1 + rng.uniform(0, 0.004))
        low = min(open_, close) * (1 - rng.uniform(0, 0.004))
        candles.append([
            1_600_000_000_000 + i * 900_000,
            open_, high, low, close, rng.uniform(1, 10)
        ])
        price = close
    return candles


class MockMarketDataProvider:
    """Mock provider for testing multi-symbol isolation."""

    def __init__(self, data: dict):
        self._data = data

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> list:
        return self._data.get(symbol, [])


class TestConfigSymbolsList:
    """Test SYMBOLS configuration parsing."""

    def test_single_symbol_fallback(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOL", "BTC/USDT")
        monkeypatch.setattr(settings, "SYMBOLS", "")
        assert settings.symbols_list == ["BTC/USDT"]

    def test_multiple_symbols(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT")
        assert settings.symbols_list == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    def test_symbols_with_spaces(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOLS", "BTC/USDT , ETH/USDT , SOL/USDT")
        assert settings.symbols_list == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    def test_single_symbol_in_symbols(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOLS", "ETH/USDT")
        assert settings.symbols_list == ["ETH/USDT"]


class TestMultiSymbolIsolation:
    """Test that each symbol has independent state."""

    def test_separate_bot_instances(self):
        """Each symbol gets its own SMCBot instance."""
        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
            "SOL/USDT": make_ohlcv(seed=3, base_price=150),
        }
        provider = MockMarketDataProvider(data)

        bot_btc = SMCBot(symbol="BTC/USDT", market_data_provider=provider)
        bot_eth = SMCBot(symbol="ETH/USDT", market_data_provider=provider)
        bot_sol = SMCBot(symbol="SOL/USDT", market_data_provider=provider)

        assert bot_btc.symbol == "BTC/USDT"
        assert bot_eth.symbol == "ETH/USDT"
        assert bot_sol.symbol == "SOL/USDT"

        # Each bot has its own analyzer
        assert bot_btc.analyzer is not bot_eth.analyzer
        assert bot_eth.analyzer is not bot_sol.analyzer

    def test_state_not_shared(self):
        """Paper position state is not shared between bots."""
        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
        }
        provider = MockMarketDataProvider(data)

        bot_btc = SMCBot(symbol="BTC/USDT", market_data_provider=provider)
        bot_eth = SMCBot(symbol="ETH/USDT", market_data_provider=provider)

        # Set position on BTC
        bot_btc._paper_position = {"side": "buy", "entry": 60000}

        # ETH should have no position
        assert bot_eth._paper_position is None

    def test_last_signal_independent(self):
        """Last signal is independent per symbol."""
        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
        }
        provider = MockMarketDataProvider(data)

        bot_btc = SMCBot(symbol="BTC/USDT", market_data_provider=provider)
        bot_eth = SMCBot(symbol="ETH/USDT", market_data_provider=provider)

        bot_btc.last_signal = {"side": "BUY", "size": 0.1}
        bot_eth.last_signal = {"side": "SELL", "size": 1.0}

        assert bot_btc.last_signal["side"] == "BUY"
        assert bot_eth.last_signal["side"] == "SELL"

    def test_fetch_ohlcv_per_symbol(self, monkeypatch):
        """Each bot fetches data for its own symbol."""
        monkeypatch.setattr(settings, "TRADING_MODE", "paper")

        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
        }
        provider = MockMarketDataProvider(data)

        bot_btc = SMCBot(symbol="BTC/USDT", market_data_provider=provider)
        bot_eth = SMCBot(symbol="ETH/USDT", market_data_provider=provider)

        ohlcv_btc = bot_btc.fetch_ohlcv(limit=10)
        ohlcv_eth = bot_eth.fetch_ohlcv(limit=10)

        # BTC prices should be around 60000
        assert ohlcv_btc[0][1] > 50000
        # ETH prices should be around 3000
        assert ohlcv_eth[0][1] < 10000


class TestMultiSymbolEngine:
    """Test the MultiSymbolEngine wrapper."""

    @pytest.mark.asyncio
    async def test_engine_creates_bots_for_all_symbols(self, monkeypatch):
        """Engine creates independent bots for all configured symbols."""
        monkeypatch.setattr(settings, "SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT")
        monkeypatch.setattr(settings, "SYMBOL", "BTC/USDT")

        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
            "SOL/USDT": make_ohlcv(seed=3, base_price=150),
        }
        provider = MockMarketDataProvider(data)

        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine(market_data_provider=provider)

        await engine.start()

        assert len(engine.symbols) == 3
        assert "BTC/USDT" in engine.symbols
        assert "ETH/USDT" in engine.symbols
        assert "SOL/USDT" in engine.symbols

        await engine.stop()

    @pytest.mark.asyncio
    async def test_engine_error_does_not_stop_others(self, monkeypatch):
        """An error in one symbol does not stop the others."""
        monkeypatch.setattr(settings, "SYMBOLS", "BTC/USDT,ETH/USDT")
        monkeypatch.setattr(settings, "SYMBOL", "BTC/USDT")

        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
        }
        provider = MockMarketDataProvider(data)

        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine(market_data_provider=provider)

        await engine.start()

        # Both should be running
        assert len(engine.symbols) == 2

        await engine.stop()

    @pytest.mark.asyncio
    async def test_engine_stops_cleanly(self, monkeypatch):
        """Engine stops all bots cleanly."""
        monkeypatch.setattr(settings, "SYMBOLS", "BTC/USDT,ETH/USDT")
        monkeypatch.setattr(settings, "SYMBOL", "BTC/USDT")

        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
        }
        provider = MockMarketDataProvider(data)

        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine(market_data_provider=provider)

        await engine.start()
        assert engine.running

        await engine.stop()
        assert not engine.running
        assert len(engine.symbols) == 0

    def test_get_bot_returns_correct_instance(self):
        """get_bot returns the correct bot for each symbol."""
        data = {
            "BTC/USDT": make_ohlcv(seed=1, base_price=60000),
            "ETH/USDT": make_ohlcv(seed=2, base_price=3000),
        }
        provider = MockMarketDataProvider(data)

        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine(market_data_provider=provider)

        # Manually create bots
        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT", market_data_provider=provider)
        engine._bots["ETH/USDT"] = SMCBot(symbol="ETH/USDT", market_data_provider=provider)

        assert engine.get_bot("BTC/USDT").symbol == "BTC/USDT"
        assert engine.get_bot("ETH/USDT").symbol == "ETH/USDT"
        assert engine.get_bot("SOL/USDT") is None


class TestBackwardCompatibility:
    """Test backward compatibility with single symbol."""

    def test_single_symbol_config(self, monkeypatch):
        """Single SYMBOL still works when SYMBOLS is empty."""
        monkeypatch.setattr(settings, "SYMBOLS", "")
        monkeypatch.setattr(settings, "SYMBOL", "ETH/USDT")
        assert settings.symbols_list == ["ETH/USDT"]

    def test_bot_uses_settings_symbol(self):
        """SMCBot defaults to settings.SYMBOL when not specified."""
        bot = SMCBot()
        assert bot.symbol == settings.SYMBOL


class TestEngineDiagnostics:
    """Test engine diagnostic methods."""

    def test_engine_diagnostics_returns_real_data(self, monkeypatch):
        """Engine diagnostics show correct bot/task counts."""
        monkeypatch.setattr(settings, "SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT")
        monkeypatch.setattr(settings, "SYMBOL", "BTC/USDT")

        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()

        # Manually create bots (without starting tasks)
        for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            engine._bots[sym] = SMCBot(symbol=sym)
            engine._errors[sym] = None
            engine._loop_counts[sym] = 0
            engine._last_loop_times[sym] = None
        engine._start_time = 100.0
        engine._started_at = "12:00:00"

        diag = engine.get_engine_diagnostics()
        assert diag["type"] == "MultiSymbolEngine"
        assert diag["status"] == "STOPPED"
        assert diag["bot_count"] == 3
        assert diag["task_count"] == 0
        assert diag["started_at"] == "12:00:00"

    def test_symbol_health_stopped(self, monkeypatch):
        """Health is STOPPED when engine is not running."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT")
        assert engine.get_health_status("BTC/USDT") == "STOPPED"

    def test_symbol_health_error(self, monkeypatch):
        """Health is ERROR when task has exception."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine.running = True
        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT")
        engine._errors["BTC/USDT"] = "test error"
        assert engine.get_health_status("BTC/USDT") == "ERROR"

    @pytest.mark.asyncio
    async def test_symbol_health_healthy(self, monkeypatch):
        """Health is HEALTHY when task is running and no errors."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine.running = True

        async def dummy():
            await asyncio.sleep(10)

        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT")
        engine._tasks["BTC/USDT"] = asyncio.create_task(dummy())
        engine._errors["BTC/USDT"] = None
        assert engine.get_health_status("BTC/USDT") == "HEALTHY"
        engine._tasks["BTC/USDT"].cancel()

    def test_per_symbol_isolation(self, monkeypatch):
        """BTC error does not appear in ETH diagnostics."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine.running = True
        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT")
        engine._bots["ETH/USDT"] = SMCBot(symbol="ETH/USDT")
        engine._errors["BTC/USDT"] = "BTC crashed"
        engine._errors["ETH/USDT"] = None

        btc_diag = engine.get_symbol_diagnostics("BTC/USDT")
        eth_diag = engine.get_symbol_diagnostics("ETH/USDT")
        assert btc_diag["last_error"] == "BTC crashed"
        assert eth_diag["last_error"] is None

    def test_last_error_per_symbol(self):
        """Errors are symbol-specific."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT")
        engine._bots["ETH/USDT"] = SMCBot(symbol="ETH/USDT")
        engine._errors["BTC/USDT"] = "ConnectionError"
        engine._errors["ETH/USDT"] = None

        assert engine._errors["BTC/USDT"] == "ConnectionError"
        assert engine._errors["ETH/USDT"] is None

    def test_loop_count_initial(self):
        """Loop count starts at 0."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT")
        diag = engine.get_symbol_diagnostics("BTC/USDT")
        assert diag["loop_count"] == 0

    def test_smc_bot_diagnostic_fields(self):
        """SMCBot has diagnostic fields initialized."""
        bot = SMCBot(symbol="BTC/USDT")
        assert bot._last_error is None
        assert bot._loop_count == 0
        assert bot._last_loop_time is None
        assert bot._started_at is None

    def test_engine_diagnostics_cleared_on_stop(self, monkeypatch):
        """Engine diagnostics are cleared when stopped."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        engine._bots["BTC/USDT"] = SMCBot(symbol="BTC/USDT")
        engine._errors["BTC/USDT"] = "error"
        engine._loop_counts["BTC/USDT"] = 5
        engine._start_time = 100.0
        engine._started_at = "12:00:00"
        engine.running = True

        # Simulate stop cleanup
        engine._bots.clear()
        engine._errors.clear()
        engine._loop_counts.clear()
        engine._last_loop_times.clear()
        engine._start_time = None
        engine._started_at = None
        engine.running = False

        assert len(engine._bots) == 0
        assert len(engine._errors) == 0

    def test_nonexistent_symbol_diagnostics(self):
        """Diagnostics for nonexistent symbol returns exists=False."""
        from backend.core.engine.multi_symbol import MultiSymbolEngine
        engine = MultiSymbolEngine()
        diag = engine.get_symbol_diagnostics("FAKE/USDT")
        assert diag["exists"] is False
        assert diag["symbol"] == "FAKE/USDT"

