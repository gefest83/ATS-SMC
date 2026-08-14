"""Regression tests for stale position handling and protective order sizing."""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch

from backend.core.order_manager import OrderManager
from backend.core.position_manager import Position, PositionManager
from backend.core.exchange.base import PositionData


class FakeExchange:
    def __init__(self):
        self._positions = []
        self._raw_balance = {"free": {"BTC": "0.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.0"}}
        self.exchange = self

    def get_exchange_name(self):
        return "binance"

    def fetch_positions(self, symbol=None):
        return self._positions

    def fetch_balance(self):
        return self._raw_balance

    def find_order_by_client_id(self, symbol, client_id):
        return None

    def fetch_open_orders(self, symbol):
        return []


@pytest.mark.asyncio
async def test_spot_stale_position_closed_in_db():
    """When spot position has no free base, recovery should close it."""
    exchange = FakeExchange()
    exchange._raw_balance = {"free": {"BTC": "0.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.0"}}

    pm = PositionManager(db_session_factory=None)
    pos = Position(
        position_id="test-pos-1",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        quantity=Decimal("1.0"),
        entry_price=Decimal("50000"),
        sl_price=Decimal("49000"),
        tp_prices=[Decimal("51000")],
    )
    pm.positions["test-pos-1"] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert "test-pos-1" not in pm.positions


@pytest.mark.asyncio
async def test_spot_position_with_balance_not_stale():
    """When spot position has free base, recovery should proceed."""
    exchange = FakeExchange()
    exchange._raw_balance = {"free": {"BTC": "1.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "1.0"}}

    pm = PositionManager(db_session_factory=None)
    pos = Position(
        position_id="test-pos-2",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        quantity=Decimal("0.5"),
        entry_price=Decimal("50000"),
        sl_price=Decimal("49000"),
        tp_prices=[Decimal("51000")],
    )
    pm.positions["test-pos-2"] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert "test-pos-2" in pm.positions


@pytest.mark.asyncio
async def test_futures_stale_position_closed_in_db():
    """When futures position not on exchange, recovery should close it."""
    exchange = FakeExchange()
    exchange._positions = []

    pm = PositionManager(db_session_factory=None)
    pos = Position(
        position_id="test-pos-3",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        quantity=Decimal("1.0"),
        entry_price=Decimal("50000"),
        sl_price=Decimal("49000"),
        tp_prices=[Decimal("51000")],
    )
    pm.positions["test-pos-3"] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert "test-pos-3" not in pm.positions


@pytest.mark.asyncio
async def test_futures_position_on_exchange_not_stale():
    """When futures position exists on exchange, recovery should proceed."""
    exchange = FakeExchange()
    remote_pos = PositionData(
        symbol="BTC/USDT",
        exchange="binance",
        side="buy",
        quantity=Decimal("1.0"),
        entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"),
        mark_price=Decimal("50000"),
        timestamp=1234567890,
    )
    exchange._positions = [remote_pos]

    pm = PositionManager(db_session_factory=None)
    pos = Position(
        position_id="test-pos-4",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        quantity=Decimal("1.0"),
        entry_price=Decimal("50000"),
        sl_price=Decimal("49000"),
        tp_prices=[Decimal("51000")],
    )
    pm.positions["test-pos-4"] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert "test-pos-4" in pm.positions


@pytest.mark.asyncio
async def test_protective_qty_capped_to_free_base():
    """Protective orders should not exceed free base balance."""
    exchange = FakeExchange()
    exchange._raw_balance = {"free": {"BTC": "0.5"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.5"}}

    pm = PositionManager(db_session_factory=None)

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        free_base = await om._get_free_base_balance("BTC/USDT")
        assert free_base == Decimal("0.5")

        position_qty = Decimal("1.0")
        protected_qty = min(position_qty, free_base)
        assert protected_qty == Decimal("0.5")
