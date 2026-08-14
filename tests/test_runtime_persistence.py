import pytest
from decimal import Decimal
from types import SimpleNamespace

from backend.core.risk.risk_manager import RiskManager


class FakeSession:
    def __init__(self, store):
        self.store = store
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def get(self, model, key):
        return self.store.get(key)
    def add(self, row):
        self.store[row.scope] = row
    async def commit(self):
        return None


class Factory:
    def __init__(self, store):
        self.store = store
    def __call__(self):
        return FakeSession(self.store)


@pytest.mark.asyncio
async def test_risk_state_survives_restart_same_day():
    store = {}
    factory = Factory(store)
    first = RiskManager(10000, db_session_factory=factory, state_scope="binance:BTC/USDT")
    first.update_equity(9100)
    first.trade_opened()
    assert await first.persist_state('{"remaining":"1"}')

    second = RiskManager(10000, db_session_factory=factory, state_scope="binance:BTC/USDT")
    payload = await second.restore_state()
    assert second.current_equity == pytest.approx(9100)
    assert second.daily_start_equity == pytest.approx(10000)
    assert second.daily_loss == pytest.approx(900)
    assert second.open_trades == 1
    assert payload == '{"remaining":"1"}'


@pytest.mark.asyncio
async def test_missing_runtime_state_does_not_reset_constructor_defaults():
    store = {}
    manager = RiskManager(5000, db_session_factory=Factory(store), state_scope="new")
    assert await manager.restore_state() is None
    assert manager.current_equity == 5000
    assert manager.daily_start_equity == 5000
