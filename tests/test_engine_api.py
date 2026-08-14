import asyncio
import sys
import types
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DummyCCXT(types.ModuleType):
    def __getattr__(self, name):
        if name == "OrderNotFound":
            return type("OrderNotFound", (Exception,), {})
        return type(name, (), {})


def _install_ccxt_stubs():
    ccxt = _DummyCCXT("ccxt")
    pro = _DummyCCXT("ccxt.pro")
    ccxt.pro = pro
    sys.modules.setdefault("ccxt", ccxt)
    sys.modules.setdefault("ccxt.pro", pro)


class _FakeTask:
    """Minimal asyncio Task stub for sync test contexts."""

    def __init__(self):
        self._done = True
        self._cancelled = False

    def done(self):
        return self._done

    def cancelled(self):
        return self._cancelled

    def get_name(self):
        return "smc-engine"

    def exception(self):
        return None

    def cancel(self):
        self._cancelled = True

    def __await__(self):
        return iter([])
        yield


def test_engine_start_stop_endpoints(monkeypatch):
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    class DummyBot:
        def __init__(self, symbol="BTC/USDT", **kwargs):
            self.symbol = symbol
            self.running = False
            self.last_signal = None
            self._paper_position = None
            self._last_error = None
            self._loop_count = 0
            self._last_loop_time = None
            self._started_at = None
            self.risk_manager = SimpleNamespace(current_equity=10000.0, open_trades=0)
            self.order_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.position_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.notifier = SimpleNamespace(stop=lambda: asyncio.sleep(0))

        def validate_startup(self):
            return None

        async def run(self):
            self.running = True
            try:
                while self.running:
                    await asyncio.sleep(0.01)
            finally:
                self.running = False

    monkeypatch.setattr("backend.core.engine.smc_bot.SMCBot", DummyBot)
    monkeypatch.setattr(ep.settings, "SYMBOLS", "")
    monkeypatch.setattr(ep.settings, "SYMBOL", "BTC/USDT")
    ep._state.update(bot=None, engine_task=None, multi_engine=None)

    with TestClient(app) as client:
        first = client.post("/engine/start")
        assert first.status_code == 200
        assert first.json()["running"] is True
        second = client.post("/engine/start")
        assert second.status_code == 200
        stopped = client.post("/engine/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False


def test_status_includes_engine_diagnostics(monkeypatch):
    """GET /status includes engine diagnostics object."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    class DummyBot:
        def __init__(self, symbol="BTC/USDT", **kwargs):
            self.symbol = symbol
            self.running = True
            self.last_signal = None
            self._paper_position = None
            self._last_error = None
            self._loop_count = 0
            self._last_loop_time = None
            self._started_at = None
            self.risk_manager = SimpleNamespace(current_equity=10000.0, open_trades=0)
            self.order_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.position_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.notifier = SimpleNamespace(stop=lambda: asyncio.sleep(0))

    monkeypatch.setattr(ep.settings, "SYMBOLS", "")
    monkeypatch.setattr(ep.settings, "SYMBOL", "BTC/USDT")

    with TestClient(app) as client:
        dummy_task = _FakeTask()
        ep._state.update(
            bot=DummyBot(symbol="BTC/USDT"),
            engine_task=dummy_task,
            multi_engine=None,
        )
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "engine" in data
        engine = data["engine"]
        assert "type" in engine
        assert "status" in engine
        assert "bot_count" in engine
        assert "task_count" in engine
        assert "healthy_count" in engine
        assert "error_count" in engine
        ep._state.update(bot=None, engine_task=None, multi_engine=None)


def test_symbol_status_includes_runtime(monkeypatch):
    """GET /status/{symbol} includes runtime diagnostics."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    class DummyBot:
        def __init__(self, symbol="BTC/USDT", **kwargs):
            self.symbol = symbol
            self.running = True
            self.last_signal = None
            self._paper_position = None
            self._last_error = None
            self._loop_count = 0
            self._last_loop_time = None
            self._started_at = None
            self.risk_manager = SimpleNamespace(current_equity=10000.0, open_trades=0)
            self.order_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.position_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.notifier = SimpleNamespace(stop=lambda: asyncio.sleep(0))

    monkeypatch.setattr(ep.settings, "SYMBOLS", "")
    monkeypatch.setattr(ep.settings, "SYMBOL", "BTC/USDT")

    with TestClient(app) as client:
        dummy_task = _FakeTask()
        ep._state.update(
            bot=DummyBot(symbol="BTC/USDT"),
            engine_task=dummy_task,
            multi_engine=None,
        )
        resp = client.get("/status/BTC%2FUSDT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTC/USDT"
        assert "health" in data
        assert "runtime" in data
        runtime = data["runtime"]
        assert "loop_count" in runtime
        assert "last_error" in runtime
        assert "task_state" in runtime
        ep._state.update(bot=None, engine_task=None, multi_engine=None)


def test_single_symbol_fallback(monkeypatch):
    """Single SYMBOL mode works without multi_engine."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    class DummyBot:
        def __init__(self, symbol="ETH/USDT", **kwargs):
            self.symbol = symbol
            self.running = True
            self.last_signal = None
            self._paper_position = None
            self._last_error = None
            self._loop_count = 0
            self._last_loop_time = None
            self._started_at = None
            self.risk_manager = SimpleNamespace(current_equity=10000.0, open_trades=0)
            self.order_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.position_manager = SimpleNamespace(stop=lambda: asyncio.sleep(0))
            self.notifier = SimpleNamespace(stop=lambda: asyncio.sleep(0))

    monkeypatch.setattr(ep.settings, "SYMBOLS", "")
    monkeypatch.setattr(ep.settings, "SYMBOL", "ETH/USDT")

    with TestClient(app) as client:
        dummy_task = _FakeTask()
        ep._state.update(
            bot=DummyBot(symbol="ETH/USDT"),
            engine_task=dummy_task,
            multi_engine=None,
        )
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["multi_symbol"] is False
        assert data["engine"]["type"] == "SMCBot"

        resp2 = client.get("/status/ETH%2FUSDT")
        assert resp2.status_code == 200

        resp3 = client.get("/status/BTC%2FUSDT")
        assert resp3.status_code == 404
        ep._state.update(bot=None, engine_task=None, multi_engine=None)


# ---------------------------------------------------------------------------
# Dashboard OHLCV multi-symbol routing tests
# ---------------------------------------------------------------------------

def _make_ohlcv(symbol: str, n: int = 5) -> list:
    """Return minimal OHLCV candles with prices keyed to symbol."""
    base = {"BTC/USDT": 60000, "ETH/USDT": 3000, "SOL/USDT": 150}.get(symbol, 100)
    return [[1_600_000_000_000 + i * 900_000, base + i, base + i + 10, base - 5, base + 5, 1.0] for i in range(n)]


class _FakeExchange:
    """Minimal exchange stub that returns per-symbol OHLCV data."""

    def __init__(self, data: dict):
        self._data = data
        self._markets = {}

    def fetch_ohlcv(self, symbol, timeframe="15m", limit=200):
        return self._data.get(symbol, [])

    def load_markets(self):
        pass

    def get_exchange_name(self):
        return "test"

    def fetch_ticker(self, symbol):
        from backend.core.exchange.base import MarketData
        return MarketData(symbol=symbol, timestamp=0, price=100)


class _DummyBot:
    """Minimal bot stub for dashboard tests."""

    def __init__(self, symbol: str, exchange=None):
        self.symbol = symbol
        self.running = True
        self.last_signal = None
        self._paper_position = None
        self._last_error = None
        self._loop_count = 0
        self._last_loop_time = None
        self._started_at = None
        self.exchange = exchange
        self.market_data_provider = None
        self._exchange_closed = False

    def fetch_ohlcv(self, limit=200, timeframe=None):
        if self.exchange is not None:
            return self.exchange.fetch_ohlcv(self.symbol, limit=limit)
        return []

    def _ensure_exchange(self):
        return self.exchange

    def validate_startup(self):
        return None


class _FakeMultiEngine:
    """Minimal multi-engine stub for dashboard tests."""

    def __init__(self, bots: dict):
        self._bots = bots
        self.running = True

    @property
    def symbols(self):
        return list(self._bots.keys())

    def get_bot(self, symbol):
        return self._bots.get(symbol)

    def get_engine_diagnostics(self):
        return {"type": "MultiSymbolEngine", "status": "RUNNING", "bot_count": len(self._bots)}

    def get_symbol_diagnostics(self, symbol):
        bot = self._bots.get(symbol)
        if bot is None:
            return {"symbol": symbol, "exists": False, "health": "STOPPED"}
        return {
            "symbol": symbol, "exists": True, "health": "HEALTHY",
            "loop_count": bot._loop_count, "last_loop_time": None, "last_error": None,
        }


def test_dashboard_ohlcv_multi_symbol_btc(monkeypatch):
    """Multi-symbol /dashboard/ohlcv returns BTC candles when symbol=BTC/USDT."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.api.dashboard import router as dash_router
    from backend.main import app

    btc_data = _make_ohlcv("BTC/USDT")
    eth_data = _make_ohlcv("ETH/USDT")
    sol_data = _make_ohlcv("SOL/USDT")

    btc_bot = _DummyBot("BTC/USDT", exchange=_FakeExchange({"BTC/USDT": btc_data}))
    eth_bot = _DummyBot("ETH/USDT", exchange=_FakeExchange({"ETH/USDT": eth_data}))
    sol_bot = _DummyBot("SOL/USDT", exchange=_FakeExchange({"SOL/USDT": sol_data}))

    multi = _FakeMultiEngine({"BTC/USDT": btc_bot, "ETH/USDT": eth_bot, "SOL/USDT": sol_bot})
    ep._state.update(bot=btc_bot, multi_engine=multi, engine_task=None)

    with TestClient(app) as client:
        resp = client.get("/dashboard/ohlcv?symbol=BTC%2FUSDT&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTC/USDT"
        assert len(data["candles"]) == 5
        assert data["candles"][0]["open"] == 60000
    ep._state.update(bot=None, multi_engine=None, engine_task=None)


def test_dashboard_ohlcv_multi_symbol_eth(monkeypatch):
    """Multi-symbol /dashboard/ohlcv returns ETH candles when symbol=ETH/USDT."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    btc_data = _make_ohlcv("BTC/USDT")
    eth_data = _make_ohlcv("ETH/USDT")

    btc_bot = _DummyBot("BTC/USDT", exchange=_FakeExchange({"BTC/USDT": btc_data}))
    eth_bot = _DummyBot("ETH/USDT", exchange=_FakeExchange({"ETH/USDT": eth_data}))

    multi = _FakeMultiEngine({"BTC/USDT": btc_bot, "ETH/USDT": eth_bot})
    ep._state.update(bot=btc_bot, multi_engine=multi, engine_task=None)

    with TestClient(app) as client:
        resp = client.get("/dashboard/ohlcv?symbol=ETH%2FUSDT&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "ETH/USDT"
        assert len(data["candles"]) == 5
        assert data["candles"][0]["open"] == 3000
    ep._state.update(bot=None, multi_engine=None, engine_task=None)


def test_dashboard_ohlcv_multi_symbol_sol(monkeypatch):
    """Multi-symbol /dashboard/ohlcv returns SOL candles when symbol=SOL/USDT."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    btc_data = _make_ohlcv("BTC/USDT")
    sol_data = _make_ohlcv("SOL/USDT")

    btc_bot = _DummyBot("BTC/USDT", exchange=_FakeExchange({"BTC/USDT": btc_data}))
    sol_bot = _DummyBot("SOL/USDT", exchange=_FakeExchange({"SOL/USDT": sol_data}))

    multi = _FakeMultiEngine({"BTC/USDT": btc_bot, "SOL/USDT": sol_bot})
    ep._state.update(bot=btc_bot, multi_engine=multi, engine_task=None)

    with TestClient(app) as client:
        resp = client.get("/dashboard/ohlcv?symbol=SOL%2FUSDT&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "SOL/USDT"
        assert len(data["candles"]) == 5
        assert data["candles"][0]["open"] == 150
    ep._state.update(bot=None, multi_engine=None, engine_task=None)


def test_dashboard_ohlcv_does_not_use_state_bot_for_non_first_symbol(monkeypatch):
    """In multi-symbol mode, /dashboard/ohlcv for ETH does NOT return BTC data."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    btc_data = _make_ohlcv("BTC/USDT")
    eth_data = _make_ohlcv("ETH/USDT")

    btc_bot = _DummyBot("BTC/USDT", exchange=_FakeExchange({"BTC/USDT": btc_data}))
    eth_bot = _DummyBot("ETH/USDT", exchange=_FakeExchange({"ETH/USDT": eth_data}))

    multi = _FakeMultiEngine({"BTC/USDT": btc_bot, "ETH/USDT": eth_bot})
    ep._state.update(bot=btc_bot, multi_engine=multi, engine_task=None)

    with TestClient(app) as client:
        resp = client.get("/dashboard/ohlcv?symbol=ETH%2FUSDT&limit=5")
        data = resp.json()
        first_open = data["candles"][0]["open"]
        assert first_open == 3000, f"Expected ETH price ~3000, got {first_open}"
    ep._state.update(bot=None, multi_engine=None, engine_task=None)


def test_dashboard_ohlcv_single_symbol_fallback(monkeypatch):
    """Single-symbol mode uses _state['bot'] when multi_engine is None."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    eth_data = _make_ohlcv("ETH/USDT")
    eth_bot = _DummyBot("ETH/USDT", exchange=_FakeExchange({"ETH/USDT": eth_data}))

    ep._state.update(bot=eth_bot, multi_engine=None, engine_task=None)

    with TestClient(app) as client:
        resp = client.get("/dashboard/ohlcv?symbol=ETH%2FUSDT&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "ETH/USDT"
        assert data["candles"][0]["open"] == 3000
    ep._state.update(bot=None, multi_engine=None, engine_task=None)


def test_dashboard_ohlcv_no_engine_returns_empty(monkeypatch):
    """When no engine is running, /dashboard/ohlcv returns empty candles."""
    _install_ccxt_stubs()
    import backend.api.endpoints as ep
    from backend.main import app

    ep._state.update(bot=None, multi_engine=None, engine_task=None)

    with TestClient(app) as client:
        resp = client.get("/dashboard/ohlcv?symbol=BTC%2FUSDT&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTC/USDT"
        assert data["candles"] == []
    ep._state.update(bot=None, multi_engine=None, engine_task=None)
