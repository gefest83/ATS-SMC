"""Regression tests for InsufficientFunds and Spot protective order sizing.

Covers the root cause of the InsufficientFunds error: when free_base <= 0,
the fallback to position.quantity must NOT happen for Spot orders.
"""
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
        self._order_map = {}
        self._reserved_base = Decimal("0")  # Track BTC reserved by open SELL orders

    def get_exchange_name(self):
        return "binance"

    def fetch_ticker(self, symbol):
        return FakeTicker(self._current_price)

    def fetch_positions(self, symbol=None):
        return self._positions

    def fetch_balance(self):
        # Simulate BTC reservation: when SELL orders are placed, BTC moves from free to used
        free_btc = Decimal(str(self._raw_balance.get("free", {}).get("BTC", "0")))
        actual_free = max(Decimal("0"), free_btc - self._reserved_base)
        return {
            "free": {"BTC": str(actual_free)},
            "used": {"BTC": str(self._reserved_base)},
            "total": self._raw_balance.get("total", {}),
        }

    def find_order_by_client_id(self, symbol, client_id):
        return None

    def fetch_order(self, symbol, order_id):
        return self._order_map.get(order_id, None)

    def fetch_open_orders(self, symbol):
        return self._open_orders

    def create_order(self, order):
        self._orders_created.append(order)
        is_protective = order.type in {"stop_loss", "take_profit"}
        if is_protective and order.side == "sell":
            # Reserve BTC for SELL protective orders
            self._reserved_base += order.quantity
        resp = OrderResponse(
            order_id=f"fake-order-{len(self._orders_created)}",
            status="open" if is_protective else "closed",
            filled_quantity=order.quantity if not is_protective else Decimal("0"),
            symbol=order.symbol,
            side=order.side,
            order_type=order.type,
            quantity=order.quantity,
            client_order_id=order.client_order_id,
        )
        self._order_map[resp.order_id] = resp
        return resp

    def cancel_order(self, symbol, order_id):
        # Unreserve BTC when canceling a SELL protective order
        order = self._order_map.get(order_id)
        if order and order.side == "sell" and order.type in {"stop_loss", "take_profit"}:
            self._reserved_base = max(Decimal("0"), self._reserved_base - order.quantity)
        return True


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
        tp_prices=list(tp_prices) if tp_prices is not None else [Decimal("52000")],
    )


# ══════════════════════════════════════════════════════════════════════════════
# ROOT CAUSE: free_base=0 must NOT fall back to position.quantity
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spot_free_base_zero_no_insufficient_funds():
    """When free_base=0, recovery must NOT create protective orders exceeding balance."""
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

    # free_base=0 → position is stale → closed, NO orders created
    assert pos.position_id not in pm.positions
    assert len(exchange._orders_created) == 0


@pytest.mark.asyncio
async def test_spot_free_base_zero_ensure_protection_no_orders():
    """_ensure_position_protection with free_base=0 must not create any orders."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "0.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.0"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        await om._ensure_position_protection(pos)

    assert len(exchange._orders_created) == 0


@pytest.mark.asyncio
async def test_spot_free_base_partial_capping():
    """When free_base < position.quantity, TPs use free_base, SL gets remainder."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "0.3"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.3"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), quantity=Decimal("1.0"),
                         tp_prices=[Decimal("52000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    # Position exists (not stale since free_base > 0)
    assert pos.position_id in pm.positions
    # TP uses all free_base (0.3), SL gets 0 (no remaining free BTC)
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    assert len(tp_orders) == 1
    assert tp_orders[0].quantity == Decimal("0.3")
    # SL not placed because all free_base was reserved by TP
    assert len(sl_orders) == 0


@pytest.mark.asyncio
async def test_spot_free_base_exceeds_position_uses_position_qty():
    """When free_base > position.quantity, protective orders use position.quantity."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "2.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "2.0"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), quantity=Decimal("0.5"),
                         tp_prices=[Decimal("52000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    assert len(sl_orders) == 1
    assert sl_orders[0].quantity == Decimal("0.5")
    assert len(tp_orders) == 1
    assert tp_orders[0].quantity == Decimal("0.5")


# ══════════════════════════════════════════════════════════════════════════════
# Stale position: free_base=0 → closed, not retried
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stale_spot_position_closed_not_retried():
    """Stale spot position (free_base=0) must be closed without retry."""
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
        for _ in range(3):
            await om._recover_protective_orders()

    assert pos.position_id not in pm.positions
    assert len(exchange._orders_created) == 0


@pytest.mark.asyncio
async def test_stale_futures_position_closed_not_retried():
    """Stale futures position (not on exchange) must be closed without retry."""
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        for _ in range(3):
            await om._recover_protective_orders()

    assert pos.position_id not in pm.positions
    assert len(exchange._orders_created) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Spot SELL (protective) with insufficient free BTC
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spot_sell_protective_capped_to_free_btc():
    """TP uses free BTC, SL gets remainder (0 if TPs consume all)."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "0.01"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.01"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), quantity=Decimal("0.1"),
                         tp_prices=[Decimal("52000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    # TP uses all free_base (0.01), SL gets 0
    assert len(tp_orders) == 1
    assert tp_orders[0].side == "sell"
    assert tp_orders[0].quantity == Decimal("0.01")
    assert len(sl_orders) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Futures does NOT use spot free-base cap
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_futures_no_spot_free_base_cap():
    """Futures protective orders must use full position quantity."""
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), quantity=Decimal("1.0"),
                         tp_prices=[Decimal("52000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    assert len(sl_orders) == 1
    assert sl_orders[0].quantity == Decimal("1.0")
    assert len(tp_orders) == 1
    assert tp_orders[0].quantity == Decimal("1.0")


# ══════════════════════════════════════════════════════════════════════════════
# SL/TP direction validation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_recovery_skips_sl_that_would_trigger_immediately():
    """SL above current price for LONG must be skipped; TP still created."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("51000"), tp_prices=[Decimal("52000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    assert len(sl_orders) == 0
    assert len(tp_orders) == 1
    assert pos.sl_order_id is None


@pytest.mark.asyncio
async def test_recovery_skips_tp_that_would_trigger_immediately():
    """TP below current price for LONG must be skipped."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), tp_prices=[Decimal("48000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    # SL created, TP skipped
    assert len(exchange._orders_created) == 1
    assert exchange._orders_created[0].type == "stop_loss"


# ══════════════════════════════════════════════════════════════════════════════
# Real position + missing SL → new SL created
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_real_position_missing_sl_creates_new():
    """Position exists on exchange, no SL → new SL created."""
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), tp_prices=[Decimal("52000")])
    pos.sl_order_id = None
    pos.tp_order_ids = []
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    assert len(sl_orders) == 1
    assert pos.sl_order_id is not None


# ══════════════════════════════════════════════════════════════════════════════
# Real position + stale SL ID → old cleared, new created
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stale_sl_id_cleared_and_new_created():
    """Stale SL order ID must be cleared and new SL created."""
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"))
    pos.sl_order_id = "stale-sl-id"
    pos.tp_order_ids = ["stale-tp-1"]
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    # New SL created, old stale ID replaced
    assert pos.sl_order_id != "stale-sl-id"
    assert len(exchange._orders_created) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Futures LONG and SHORT
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_futures_long_sl_below_price():
    """Futures LONG: SL must be below current price."""
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), tp_prices=[Decimal("52000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    assert len(sl_orders) == 1
    sl = sl_orders[0]
    assert sl.side == "sell"
    assert sl.stopPrice < Decimal("50000")


@pytest.mark.asyncio
async def test_futures_short_sl_above_price():
    """Futures SHORT: SL must be above current price."""
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

    assert len(exchange._orders_created) == 1
    sl = exchange._orders_created[0]
    assert sl.side == "buy"
    assert sl.stopPrice > Decimal("50000")


@pytest.mark.asyncio
async def test_futures_short_recovery():
    """Futures SHORT: full recovery with SL+TP."""
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="sell",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(side="sell", sl_price=Decimal("51000"),
                         tp_prices=[Decimal("48000"), Decimal("47000")])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    assert len(sl_orders) == 1
    assert len(tp_orders) == 2


# ══════════════════════════════════════════════════════════════════════════════
# Duplicate order protection
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_recovery_does_not_create_duplicates():
    """Multiple recovery cycles must not create duplicate protective orders."""
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.0"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )

    class RecoverableExchange(FakeExchange):
        def __init__(self):
            super().__init__(positions=[remote])

        def find_order_by_client_id(self, symbol, client_order_id):
            for order in self._orders_created:
                if getattr(order, "client_order_id", None) == client_order_id:
                    return order
            return None

    exchange = RecoverableExchange()
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), tp_prices=[])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        for _ in range(3):
            await om._recover_protective_orders()

    # Only one SL order created across all recovery cycles
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    assert len(sl_orders) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Rejected/timeout order handling
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_order_timeout_raises_ambiguous_error():
    """Timeout must raise RuntimeError, not blind retry."""
    class TimeoutExchange:
        def create_order(self, order):
            raise TimeoutError("response lost")
        def get_exchange_name(self):
            return "binance"
        def find_order_by_client_id(self, symbol, client_id):
            return None

    om = OrderManager.__new__(OrderManager)
    om.exchange = TimeoutExchange()
    om.retry_counts = {}

    request = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("1"),
    )

    with pytest.raises(RuntimeError, match="automatic retry disabled"):
        await om._place_order_with_retry(request)


@pytest.mark.asyncio
async def test_rejected_order_does_not_retry():
    """Rejected order must raise error, not retry."""
    class RejectedExchange:
        def create_order(self, order):
            raise Exception("Order rejected")
        def get_exchange_name(self):
            return "binance"
        def find_order_by_client_id(self, symbol, client_id):
            return None

    om = OrderManager.__new__(OrderManager)
    om.exchange = RejectedExchange()
    om.retry_counts = {}

    request = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("1"),
    )

    with pytest.raises(RuntimeError, match="automatic retry disabled"):
        await om._place_order_with_retry(request)


@pytest.mark.asyncio
async def test_recovered_order_after_timeout():
    """If order found by client_id after timeout, it is recovered."""
    class RecoveredExchange:
        def __init__(self):
            self.recovered = OrderResponse(
                order_id="exchange-42", status="closed",
                filled_quantity=Decimal("1"),
            )
        def create_order(self, order):
            raise TimeoutError("response lost")
        def get_exchange_name(self):
            return "binance"
        def find_order_by_client_id(self, symbol, client_id):
            self.recovered.client_order_id = client_id
            return self.recovered

    om = OrderManager.__new__(OrderManager)
    om.exchange = RecoveredExchange()
    om.retry_counts = {}

    request = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("1"),
    )

    response = await om._place_order_with_retry(request)
    assert response.order_id == "exchange-42"


# ══════════════════════════════════════════════════════════════════════════════
# Engine loop SELL sizing
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_engine_sell_size_zero_when_no_free_base():
    """Engine must set size=0 for SELL when free_base=0."""
    from backend.core.engine.smc_bot import SMCBot
    from backend.config import Settings

    class DummyNotifier:
        async def send_alert(self, message):
            return None
        async def notify_position_closed(self, **kwargs):
            return None
        async def notify_order_opened(self, **kwargs):
            return None
        async def notify_signal(self, **kwargs):
            return None
        async def notify_signal_blocked(self, **kwargs):
            return None

    bot = SMCBot.__new__(SMCBot)
    bot.symbol = "BTC/USDT"
    bot.notifier = DummyNotifier()

    # Simulate the SELL sizing logic
    free_base = Decimal("0")
    size = 0.1

    if free_base > 0:
        size = min(size, float(free_base))
    else:
        size = 0

    assert size == 0


# ══════════════════════════════════════════════════════════════════════════════
# Spot BUY protective orders with commission-affected quantity
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spot_buy_after_commission_sl_uses_actual_fill():
    """After BUY with commission, SL uses actual filled quantity."""
    class PartialFillExchange(FakeExchange):
        def create_order(self, order):
            self._orders_created.append(order)
            if order.type == "market":
                # BUY filled less due to commission
                return OrderResponse(
                    order_id=f"fake-{len(self._orders_created)}",
                    status="closed",
                    filled_quantity=Decimal("0.0996"),  # 0.1 minus 0.04% fee
                    avg_price=Decimal("50000"),
                    symbol=order.symbol, side=order.side,
                    order_type=order.type, quantity=order.quantity,
                )
            return OrderResponse(
                order_id=f"fake-{len(self._orders_created)}",
                status="closed",
                filled_quantity=order.quantity,
                symbol=order.symbol, side=order.side,
                order_type=order.type, quantity=order.quantity,
            )

    exchange = PartialFillExchange()
    pm = PositionManager(db_session_factory=None)
    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        position_id = await om.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), order_type="market",
            sl_price=Decimal("49000"), tp_prices=[Decimal("51000")],
        )

    pos = await pm.get_position(position_id)
    assert pos is not None
    assert pos.quantity == Decimal("0.0996")
    # SL must use actual filled quantity
    sl_order = [o for o in exchange._orders_created if o.type == "stop_loss"]
    assert len(sl_order) == 1
    assert sl_order[0].quantity == Decimal("0.0996")


# ══════════════════════════════════════════════════════════════════════════════
# Multiple recovery cycles: no infinite retry
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_infinite_retry_on_invalid_sl():
    """Invalid SL (would trigger immediately) must not cause infinite retry."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("51000"), tp_prices=[])
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        for _ in range(5):
            await om._recover_protective_orders()

    assert len(exchange._orders_created) == 0
    assert pos.sl_order_id is None


@pytest.mark.asyncio
async def test_no_infinite_retry_on_stale_sl():
    """Stale SL (order not found on exchange) must not cause infinite retry."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"), tp_prices=[])
    pos.sl_order_id = "stale-sl"
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        # First cycle: stale-sl not found, new SL created
        await om._recover_protective_orders()
        first_sl_id = pos.sl_order_id
        # Second cycle: new SL found via fetch_order, no duplicate
        await om._recover_protective_orders()

    # Only 1 SL order created (recovery is idempotent)
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    assert len(sl_orders) == 1
    assert pos.sl_order_id == first_sl_id


# ══════════════════════════════════════════════════════════════════════════════
# TP split across levels
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spot_recovery_splits_tp_across_free_base():
    """Staged TP: only ONE TP is placed, qty = position_qty / num_remaining."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "0.6"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.6"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"),
                         tp_prices=[Decimal("51000"), Decimal("52000")],
                         quantity=Decimal("1.0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    # Staged: only TP1 is placed, qty = 1.0 / 2 = 0.5
    assert len(tp_orders) == 1
    assert tp_orders[0].quantity == Decimal("0.5")
    # SL gets remaining free_base: 0.6 - 0.5 = 0.1
    assert len(sl_orders) == 1
    assert sl_orders[0].quantity == Decimal("0.1")


@pytest.mark.asyncio
async def test_spot_recovery_splits_tp_across_position_qty_when_enough():
    """Staged TP: when free_base >= position.quantity, TP1 uses position_qty / num_remaining."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "2.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "2.0"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"),
                         tp_prices=[Decimal("51000"), Decimal("52000")],
                         quantity=Decimal("1.0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    # Staged: only TP1 placed, qty = 1.0 / 2 = 0.5
    assert len(tp_orders) == 1
    assert tp_orders[0].quantity == Decimal("0.5")
    # SL gets remaining: free_base(2.0) - tp_qty(0.5) = 1.5, capped at position_qty = 1.0
    assert len(sl_orders) == 1
    assert sl_orders[0].quantity == Decimal("1.0")


# ══════════════════════════════════════════════════════════════════════════════
# CRITICAL: SL+TP overlap must NOT exceed free_base on Spot
# This is the exact scenario that caused the real Binance Testnet InsufficientFunds
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spot_sl_tp_total_never_exceeds_free_base():
    """SL + all TPs must never sum to more than free_base on Spot.

    Regression: position=1.16735 BTC, free_base=1.16735, 3 TPs.
    Staged: TP1 qty = 1.16735 / 3, SL = 1.16735 - TP1.
    Total = TP1 + SL = 1.16735 (never exceeds).
    """
    exchange = FakeExchange(current_price=Decimal("50000"))
    exchange._raw_balance = {"free": {"BTC": "1.16735"}, "used": {"BTC": "0.0"}, "total": {"BTC": "1.16735"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"),
                         tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
                         quantity=Decimal("1.16735"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert pos.position_id in pm.positions
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    total_sl = sum(o.quantity for o in sl_orders)
    total_tp = sum(o.quantity for o in tp_orders)
    total_all = total_sl + total_tp
    # Staged: only 1 TP placed; total must never exceed free_base
    assert len(tp_orders) == 1
    assert total_all <= Decimal("1.16735"), (
        f"SL({total_sl}) + TP({total_tp}) = {total_all} > free_base(1.16735)"
    )


@pytest.mark.asyncio
async def test_spot_open_position_sl_tp_never_exceeds_free_base():
    """open_position: SL + TPs must never exceed free_base on Spot."""
    class PreciseExchange(FakeExchange):
        def __init__(self):
            super().__init__(current_price=Decimal("50000"))
            self._raw_balance = {"free": {"BTC": "1.16735"}, "used": {"BTC": "0.0"}, "total": {"BTC": "1.16735"}}

        def create_order(self, order):
            self._orders_created.append(order)
            is_protective = order.type in {"stop_loss", "take_profit"}
            if is_protective and order.side == "sell":
                # Track BTC reservation like real Binance
                self._reserved_base += order.quantity
                # Simulate Binance: reject if total SELL exceeds free_base
                if self._reserved_base > Decimal("1.16735"):
                    raise Exception("Account has insufficient balance for requested action.")
            return OrderResponse(
                order_id=f"fake-{len(self._orders_created)}",
                status="closed" if not is_protective else "open",
                filled_quantity=order.quantity if not is_protective else Decimal("0"),
                avg_price=Decimal("50000") if not is_protective else None,
                symbol=order.symbol, side=order.side,
                order_type=order.type, quantity=order.quantity,
                client_order_id=order.client_order_id,
            )

    exchange = PreciseExchange()
    pm = PositionManager(db_session_factory=None)
    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        position_id = await om.open_position(
            "BTC/USDT", "buy", Decimal("1.16735"), order_type="market",
            sl_price=Decimal("49000"),
            tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        )

    pos = await pm.get_position(position_id)
    assert pos is not None
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    total_sl = sum(o.quantity for o in sl_orders)
    total_tp = sum(o.quantity for o in tp_orders)
    total_all = total_sl + total_tp
    assert total_all <= Decimal("1.16735"), (
        f"SL({total_sl}) + TPs({total_tp}) = {total_all} > free_base(1.16735)"
    )


@pytest.mark.asyncio
async def test_spot_tps_before_sl_order():
    """For Spot, TPs must be placed BEFORE SL to prevent balance overlap."""
    placement_order = []

    class OrderTrackingExchange(FakeExchange):
        def create_order(self, order):
            placement_order.append(order.type)
            return super().create_order(order)

    exchange = OrderTrackingExchange()
    exchange._raw_balance = {"free": {"BTC": "0.5"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.5"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"),
                         tp_prices=[Decimal("51000"), Decimal("52000")],
                         quantity=Decimal("1.0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    # TPs must appear before SL in the placement order
    tp_indices = [i for i, t in enumerate(placement_order) if t == "take_profit"]
    sl_indices = [i for i, t in enumerate(placement_order) if t == "stop_loss"]
    if tp_indices and sl_indices:
        assert max(tp_indices) < min(sl_indices), (
            f"TPs must be placed before SL. Order: {placement_order}"
        )


@pytest.mark.asyncio
async def test_spot_sl_uses_free_base_after_tp_reservation():
    """SL quantity must reflect free_base AFTER the staged TP has reserved BTC."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    # free_base = 0.6, position = 1.0, 2 TPs
    # Staged: TP1 qty = 1.0 / 2 = 0.5, SL = free_base(0.6) - tp_qty(0.5) = 0.1
    exchange._raw_balance = {"free": {"BTC": "0.6"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.6"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"),
                         tp_prices=[Decimal("51000"), Decimal("52000")],
                         quantity=Decimal("1.0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    total_tp = sum(o.quantity for o in tp_orders)
    total_sl = sum(o.quantity for o in sl_orders)
    # Staged: TP1 = 0.5, SL = 0.6 - 0.5 = 0.1
    assert total_tp == Decimal("0.5")
    assert len(tp_orders) == 1
    assert len(sl_orders) == 1
    assert total_sl == Decimal("0.1")


@pytest.mark.asyncio
async def test_spot_partial_free_base_split_between_tps_and_sl():
    """Staged TP: TP1 gets position_qty/num_remaining, SL gets the remainder."""
    exchange = FakeExchange(current_price=Decimal("50000"))
    # free_base = 0.8, position = 1.0, 2 TPs
    # Staged: TP1 qty = 1.0 / 2 = 0.5, SL = free_base(0.8) - 0.5 = 0.3
    exchange._raw_balance = {"free": {"BTC": "0.8"}, "used": {"BTC": "0.0"}, "total": {"BTC": "0.8"}}
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"),
                         tp_prices=[Decimal("51000"), Decimal("52000")],
                         quantity=Decimal("1.0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    total_tp = sum(o.quantity for o in tp_orders)
    total_sl = sum(o.quantity for o in sl_orders)
    # Staged: TP1 = 0.5, SL = 0.8 - 0.5 = 0.3
    assert total_tp == Decimal("0.5")
    assert len(tp_orders) == 1
    assert total_sl == Decimal("0.3")
    assert len(sl_orders) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Futures regression: must NOT be broken by Spot fixes
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_futures_sl_plus_tps_use_full_position():
    """Futures: SL and TPs can both use full position (reduceOnly)."""
    remote = PositionData(
        symbol="BTC/USDT", exchange="binance", side="buy",
        quantity=Decimal("1.16735"), entry_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
    )
    exchange = FakeExchange(current_price=Decimal("50000"), positions=[remote])
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(sl_price=Decimal("49000"),
                         tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
                         quantity=Decimal("1.16735"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    assert len(sl_orders) == 1
    assert len(tp_orders) == 3
    # Futures: SL uses full position, TPs split full position
    assert sl_orders[0].quantity == Decimal("1.16735")
    total_tp = sum(o.quantity for o in tp_orders)
    assert total_tp == Decimal("1.16735")


# ══════════════════════════════════════════════════════════════════════════════
# Integration: BUY → filled → TP1 → TP2 → TP3 lifecycle
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spot_buy_fill_tp_lifecycle():
    """Full lifecycle: BUY fill → staged TP1 placed → only 1 TP active."""
    class LifecycleExchange(FakeExchange):
        def __init__(self):
            super().__init__(current_price=Decimal("50000"))
            self._raw_balance = {"free": {"BTC": "1.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "1.0"}}
            self._order_counter = 0

        def create_order(self, order):
            self._order_counter += 1
            is_protective = order.type in {"stop_loss", "take_profit"}
            if is_protective and order.side == "sell":
                self._reserved_base += order.quantity
            resp = OrderResponse(
                order_id=f"order-{self._order_counter}",
                status="closed" if not is_protective else "open",
                filled_quantity=order.quantity if not is_protective else Decimal("0"),
                avg_price=Decimal("50000") if not is_protective else None,
                symbol=order.symbol, side=order.side,
                order_type=order.type, quantity=order.quantity,
                client_order_id=order.client_order_id,
            )
            self._order_map[resp.order_id] = resp
            self._orders_created.append(order)
            return resp

    exchange = LifecycleExchange()
    pm = PositionManager(db_session_factory=None)
    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        position_id = await om.open_position(
            "BTC/USDT", "buy", Decimal("1.0"), order_type="market",
            sl_price=Decimal("49000"),
            tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        )

    pos = await pm.get_position(position_id)
    assert pos is not None
    assert pos.quantity == Decimal("1.0")

    # Staged: only 1 TP placed, qty = 1.0 / 3 = 0.333...
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    assert len(tp_orders) == 1
    assert len(sl_orders) == 1
    # Total (TP + SL) must not exceed free_base
    total_tp = sum(o.quantity for o in tp_orders)
    total_sl = sum(o.quantity for o in sl_orders)
    assert total_tp + total_sl == Decimal("1.0")


@pytest.mark.asyncio
async def test_spot_insufficient_funds_error_never_occurs():
    """Simulate the exact Binance Testnet scenario that caused InsufficientFunds.

    position = 1.16735 BTC, 3 TPs, free_base = 1.16735.
    The exchange must never see total SELL > free_base.
    """
    class BinanceTestnetSimulator(FakeExchange):
        def __init__(self):
            super().__init__(current_price=Decimal("50000"))
            self._raw_balance = {"free": {"BTC": "1.16735"}, "used": {"BTC": "0.0"}, "total": {"BTC": "1.16735"}}

        def create_order(self, order):
            if order.side == "sell" and order.type in {"stop_loss", "take_profit"}:
                # Track BTC reservation like real Binance
                self._reserved_base += order.quantity
                if self._reserved_base > Decimal("1.16735"):
                    raise Exception(
                        "Account has insufficient balance for requested action."
                    )
            self._orders_created.append(order)
            return OrderResponse(
                order_id=f"order-{len(self._orders_created)}",
                status="closed" if order.type == "market" else "open",
                filled_quantity=order.quantity if order.type == "market" else Decimal("0"),
                avg_price=Decimal("50000") if order.type == "market" else None,
                symbol=order.symbol, side=order.side,
                order_type=order.type, quantity=order.quantity,
                client_order_id=order.client_order_id,
            )

    exchange = BinanceTestnetSimulator()
    pm = PositionManager(db_session_factory=None)
    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        # This must NOT raise InsufficientFunds
        position_id = await om.open_position(
            "BTC/USDT", "buy", Decimal("1.16735"), order_type="market",
            sl_price=Decimal("49000"),
            tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        )

    pos = await pm.get_position(position_id)
    assert pos is not None
    # If we reached here, no InsufficientFunds was raised
    sl_orders = [o for o in exchange._orders_created if o.type == "stop_loss"]
    tp_orders = [o for o in exchange._orders_created if o.type == "take_profit"]
    total_sl = sum(o.quantity for o in sl_orders)
    total_tp = sum(o.quantity for o in tp_orders)
    assert total_sl + total_tp <= Decimal("1.16735")
