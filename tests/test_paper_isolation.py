import asyncio

import pytest


class LocalProvider:
    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, limit=200):
        self.calls.append((symbol, timeframe, limit))
        return [[1, "100", "101", "99", "100", "1"]]


@pytest.mark.asyncio
async def test_paper_bot_uses_explicit_provider_without_exchange(monkeypatch):
    import backend.core.engine.smc_bot as module

    monkeypatch.setattr(module.settings, "TRADING_MODE", "paper")
    provider = LocalProvider()
    bot = module.SMCBot("BTC/USDT", "1m", market_data_provider=provider)

    def fail_create_exchange():
        raise AssertionError("paper mode must not construct an exchange")

    monkeypatch.setattr(
        "backend.core.exchange.factory.create_exchange", fail_create_exchange
    )
    assert bot.fetch_ohlcv(limit=7) == [[1, "100", "101", "99", "100", "1"]]
    assert provider.calls == [("BTC/USDT", "1m", 7)]
    assert bot.exchange is None
    await bot.stop()
    assert bot.exchange is None


@pytest.mark.asyncio
async def test_paper_bot_requires_local_provider(monkeypatch):
    import backend.core.engine.smc_bot as module

    monkeypatch.setattr(module.settings, "TRADING_MODE", "paper")
    bot = module.SMCBot("BTC/USDT", "1m")
    with pytest.raises(RuntimeError, match="explicit local market-data provider"):
        bot.fetch_ohlcv()
    with pytest.raises(RuntimeError, match="explicit local market-data provider"):
        await bot.run(poll_seconds=0)
    await bot.stop()


@pytest.mark.asyncio
async def test_paper_run_does_not_create_exchange(monkeypatch):
    import backend.core.engine.smc_bot as module

    monkeypatch.setattr(module.settings, "TRADING_MODE", "paper")
    provider = LocalProvider()
    bot = module.SMCBot(market_data_provider=provider)

    def fail_create_exchange():
        raise AssertionError("paper mode must not construct an exchange")

    monkeypatch.setattr(
        "backend.core.exchange.factory.create_exchange", fail_create_exchange
    )
    monkeypatch.setattr(bot.position_manager, "start", lambda: asyncio.sleep(0))
    monkeypatch.setattr(bot.order_manager, "start", lambda: asyncio.sleep(0))
    monkeypatch.setattr(bot.risk_manager, "restore_state", lambda: asyncio.sleep(0, result=None))
    monkeypatch.setattr(bot, "_persist_runtime_state", lambda: asyncio.sleep(0))
    monkeypatch.setattr(bot.analyzer, "analyze", lambda _: None)

    task = asyncio.create_task(bot.run(poll_seconds=0))
    await asyncio.sleep(0.02)
    await bot.stop()
    await asyncio.wait_for(task, timeout=1)
    assert bot.exchange is None
    assert provider.calls
