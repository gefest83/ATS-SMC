from decimal import Decimal

from fastapi.testclient import TestClient


class LocalProvider:
    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, limit=200):
        self.calls.append((symbol, timeframe, limit))
        return [
            [
                1_600_000_000_000 + index * 900_000,
                Decimal("100.123456789012345678") + index,
                Decimal("101.123456789012345678") + index,
                Decimal("99.123456789012345678") + index,
                Decimal("100.987654321098765432") + index,
                Decimal("1.000000000000000001"),
            ]
            for index in range(30)
        ]


def test_paper_analysis_uses_local_provider_and_preserves_decimal_strings(monkeypatch):
    import backend.api.endpoints as ep
    from backend.main import app

    provider = LocalProvider()
    monkeypatch.setattr(ep.settings, "TRADING_MODE", "paper")
    ep.set_paper_analysis_provider(provider)
    try:
        with TestClient(app) as client:
            response = client.get("/analysis?limit=7")

        assert response.status_code == 200
        payload = response.json()
        assert payload["current_price"] == "129.987654321098765432"
        assert isinstance(payload["atr"], str)
        assert provider.calls == [("ETH/USDT", "15m", 7)]
    finally:
        ep.clear_paper_analysis_provider()


def test_paper_analysis_without_provider_fails_closed(monkeypatch):
    import backend.api.endpoints as ep
    from backend.main import app

    monkeypatch.setattr(ep.settings, "TRADING_MODE", "paper")
    ep.clear_paper_analysis_provider()

    with TestClient(app) as client:
        response = client.get("/analysis")

    assert response.status_code == 503
    assert "local market-data provider" in response.json()["detail"]


def test_engine_start_without_paper_provider_returns_503(monkeypatch):
    import backend.api.endpoints as ep
    from backend.main import app

    monkeypatch.setattr(ep.settings, "TRADING_MODE", "paper")
    monkeypatch.setattr(ep.settings, "SYMBOLS", "")
    monkeypatch.setattr(ep.settings, "SYMBOL", "BTC/USDT")
    ep.clear_paper_analysis_provider()
    ep._state.update(bot=None, engine_task=None, multi_engine=None)

    with TestClient(app) as client:
        response = client.post("/engine/start")

    assert response.status_code == 503
    assert ep._state["bot"] is None
    assert ep._state["engine_task"] is None


def test_concurrent_engine_start_creates_one_task(monkeypatch):
    import asyncio
    import backend.api.endpoints as ep

    class DummyBot:
        instances = []

        def __init__(self, market_data_provider=None):
            self.running = False
            self.last_signal = None
            self.market_data_provider = market_data_provider
            self.stop_calls = 0
            self.run_calls = 0
            self.symbol = "BTC/USDT"
            self._paper_position = None
            type(self).instances.append(self)

        def validate_startup(self):
            return None

        async def run(self):
            self.run_calls += 1
            self.running = True
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise
            finally:
                self.running = False

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    async def scenario():
        monkeypatch.setattr(ep.settings, "TRADING_MODE", "paper")
        monkeypatch.setattr(ep.settings, "SYMBOLS", "")
        monkeypatch.setattr(ep.settings, "SYMBOL", "BTC/USDT")
        monkeypatch.setattr(
            "backend.core.engine.smc_bot.SMCBot", DummyBot
        )
        ep.set_paper_analysis_provider(object())
        ep._state.update(bot=None, engine_task=None, multi_engine=None)
        try:
            started = await asyncio.gather(
                ep.start_engine_state(), ep.start_engine_state()
            )
            assert sorted(started) == [False, True]
            assert len(DummyBot.instances) == 1
            bot = DummyBot.instances[0]
            task = ep._state["engine_task"]
            assert task is not None
            await asyncio.sleep(0)
            assert bot.run_calls == 1
            await ep.stop_engine_state()
            assert bot.stop_calls == 1
            assert ep._state["engine_task"] is None
            assert ep._state["bot"] is None
        finally:
            ep.clear_paper_analysis_provider()
            if ep._state.get("engine_task") is not None:
                await ep.stop_engine_state()

    asyncio.run(scenario())


def test_concurrent_engine_stop_calls_bot_stop_once(monkeypatch):
    import asyncio
    import backend.api.endpoints as ep

    class DummyBot:
        def __init__(self):
            self.running = True
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1
            await asyncio.sleep(0)
            self.running = False

    async def scenario():
        bot = DummyBot()
        ep._state.update(bot=bot, engine_task=None)
        await asyncio.gather(
            ep.stop_engine_state(), ep.stop_engine_state()
        )
        assert bot.stop_calls == 1
        assert ep._state["bot"] is None
        assert ep._state["engine_task"] is None

    asyncio.run(scenario())
