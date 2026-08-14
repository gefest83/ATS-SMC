"""Regression tests for SL/TP direction validation and recovery safety."""
import asyncio
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch

from backend.core.order_manager import OrderManager
from backend.core.position_manager import Position, PositionManager
from backend.core.exchange.base import OrderRequest, OrderResponse, PositionData


class FakeTicker:
    def __init__(self, price):
        self.price = Decimal(str(price))


class FakeExchange:
    def __init__(self, current_price=Decimal("50000"), positions=None, orders=None):
        self._current_price = current_price
        self._positions = positions or []
        self._open_orders = orders or []
        self._raw_balance = {"free": {"BTC": "1.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "1.0"}}
        self.exchange = self
        self._orders_created = []

    def get_exchange_name(self):
        return "binance"

    def fetch_ticker(self, symbol):
        return FakeTicker(self._current_price)

    def fetch_positions(self, symbol=None):
        return self._positions

    def fetch_balance(self):
        return self._raw_balance

    def find_order_by_client_id(self, symbol, client_id):
        return None

    def fetch_open_orders(self, symbol):
        return self._open_orders

    def create_order(self, order):
        self._orders_created.append(order)
        return OrderResponse(
            order_id="fake-order-1",
            status="closed",
            filled_quantity=order.quantity,
            symbol=order.symbol,
            side=order.side,
            order_type=order.type,
            quantity=order.quantity,
        )

    def cancel_order(self, symbol, order_id):
        return True

    @property
    def _sl_orders(self):
        return [o for o in self._orders_created if o.type == "stop_loss"]

    @property
    def _tp_orders(self):
        return [o for o in self._orders_created if o.type == "take_profit"]


def _make_position(position_id="pos-1", side="buy", sl_price=Decimal("49000"),
                   tp_prices=None, quantity=Decimal("1.0"), entry_price=Decimal("50000")):
    return Position(
        position_id=position_id,
        exchange="binance",
        symbol="BTC/USDT",
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_prices=tp_prices or [Decimal("52000")],
    )


# ──────────────────────────────────────────────────────────────
# Unit tests for validation helpers
# ──────────────────────────────────────────────────────────────

def test_sl_valid_long_below_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_sl_valid_for_position("buy", Decimal("49000"), Decimal("50000")) is True


def test_sl_invalid_long_above_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_sl_valid_for_position("buy", Decimal("51000"), Decimal("50000")) is False


def test_sl_invalid_long_equal_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_sl_valid_for_position("buy", Decimal("50000"), Decimal("50000")) is False


def test_sl_valid_short_above_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_sl_valid_for_position("sell", Decimal("51000"), Decimal("50000")) is True


def test_sl_invalid_short_below_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_sl_valid_for_position("sell", Decimal("49000"), Decimal("50000")) is False


def test_tp_valid_long_above_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_tp_valid_for_position("buy", Decimal("52000"), Decimal("50000")) is True


def test_tp_invalid_long_below_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_tp_valid_for_position("buy", Decimal("48000"), Decimal("50000")) is False


def test_tp_valid_short_below_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_tp_valid_for_position("sell", Decimal("48000"), Decimal("50000")) is True


def test_tp_invalid_short_above_price():
    om = OrderManager.__new__(OrderManager)
    assert om._is_tp_valid_for_position("sell", Decimal("52000"), Decimal("50000")) is False


# ──────────────────────────────────────────────────────────────
# Recovery: Spot LONG with valid SL below price → SL placed
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spot_recovery_valid_sl_below_price():
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    assert len(exchange._sl_orders) == 1
    assert exchange._sl_orders[0].stopPrice == Decimal("49000")


# ──────────────────────────────────────────────────────────────
# Recovery: Spot LONG with SL above/equal to price → SL skipped
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spot_recovery_sl_above_price_skipped():
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("51000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    assert len(exchange._sl_orders) == 0
    assert pos.sl_order_id is None


@pytest.mark.asyncio
async def test_spot_recovery_sl_equal_price_skipped():
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("50000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert len(exchange._sl_orders) == 0


# ──────────────────────────────────────────────────────────────
# Recovery: Futures LONG with valid SL below price
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_futures_recovery_valid_sl_below_price():
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    assert len(exchange._sl_orders) == 1


# ──────────────────────────────────────────────────────────────
# Recovery: Futures LONG with SL above price → skipped
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_futures_recovery_sl_above_price_skipped():
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("51000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert len(exchange._sl_orders) == 0


# ──────────────────────────────────────────────────────────────
# Recovery: Futures SHORT with valid SL above price
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_futures_short_recovery_valid_sl_above_price():
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="sell",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(side="sell", sl_price=Decimal("51000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    assert len(exchange._sl_orders) == 1


@pytest.mark.asyncio
async def test_futures_short_recovery_sl_below_price_skipped():
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="sell",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(side="sell", sl_price=Decimal("49000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert len(exchange._sl_orders) == 0


# ──────────────────────────────────────────────────────────────
# Stale position: no infinite retry loop
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_spot_position_closed_not_retried():
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "0.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.0"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id not in pm.positions
    assert len(exchange._orders_created) == 0


@pytest.mark.asyncio
async def test_stale_futures_position_closed_not_retried():
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id not in pm.positions


# ──────────────────────────────────────────────────────────────
# Free-base balance capping still works for Spot
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spot_free_base_capping_preserved():
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "0.3"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.3"}}
    pm = PositionManager(db_session_factory=None)

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        free_base = await om._get_free_base_balance("BTC/USDT")
        assert free_base == Decimal("0.3")

        position_qty = Decimal("1.0")
        protected_qty = min(position_qty, free_base)
        assert protected_qty == Decimal("0.3")


# ──────────────────────────────────────────────────────────────
# Futures does NOT use spot free-base cap
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_futures_no_spot_free_base_cap():
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        free_base = await om._get_free_base_balance("BTC/USDT")
        assert free_base == Decimal("0")

        position_qty = Decimal("1.0")
        sl_qty = min(position_qty, free_base) if om._is_spot and free_base > 0 else position_qty
        assert sl_qty == Decimal("1.0")


# ──────────────────────────────────────────────────────────────
# _resize_stop_loss: skips when SL would trigger immediately
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resize_skip_invalid_sl():
    exchange = FakeExchange(current_price=Decimal("48000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), quantity=Decimal("0.5"))
    pos.sl_order_id = "old-sl-id"
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        await om._resize_stop_loss(pos)

    assert len(exchange._sl_orders) == 0


@pytest.mark.asyncio
async def test_resize_proceeds_valid_sl():
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), quantity=Decimal("0.5"))
    pos.sl_order_id = "old-sl-id"
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        await om._resize_stop_loss(pos)

    assert len(exchange._sl_orders) == 1


# ──────────────────────────────────────────────────────────────
# Multiple recovery cycles: no infinite retry on bad SL
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_infinite_retry_on_stale_sl():
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("51000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        for _ in range(3):
            await om._recover_protective_orders()

    assert len(exchange._sl_orders) == 0
    assert pos.sl_order_id is None
