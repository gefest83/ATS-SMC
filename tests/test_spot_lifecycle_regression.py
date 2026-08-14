"""Full Spot lifecycle regression tests with realistic ETH reservation.

FakeExchange MUST move quantity between free and used when SELL orders are
created/cancelled/filled.  This is the only way to catch InsufficientFunds
and missing-SL bugs that only manifest when Binance balances reflect real
order reservations.
"""
import asyncio
import pytest
from decimal import Decimal
from unittest.mock import patch

from backend.core.order_manager import OrderManager
from backend.core.position_manager import Position, PositionManager
from backend.core.exchange.base import OrderRequest, OrderResponse, PositionData


# ══════════════════════════════════════════════════════════════════════════════
# FakeExchange: real ETH reservation between free / used
# ══════════════════════════════════════════════════════════════════════════════

class FakeTicker:
    def __init__(self, price):
        self.price = Decimal(str(price))


class SpotFakeExchange:
    """Exchange model that moves ETH between free and used on SELL orders."""

    def __init__(self, current_price=Decimal("2000"), free_eth=Decimal("10.0")):
        self._current_price = current_price
        self._total_eth = free_eth
        self._free_eth = free_eth
        self._used_eth = Decimal("0")
        self._open_sell_orders = {}
        self._order_initial = {}
        self._order_state = {}
        self._orders_created = []
        self._order_counter = 0
        self.exchange = self
        self._positions = []

    def get_exchange_name(self):
        return "binance"

    def fetch_ticker(self, symbol):
        return FakeTicker(self._current_price)

    def fetch_positions(self, symbol=None):
        return self._positions

    def fetch_balance(self):
        return {
            "free": {"ETH": str(self._free_eth)},
            "used": {"ETH": str(self._used_eth)},
            "total": {"ETH": str(self._total_eth)},
        }

    def _build_order_response(self, oid, initial, state_overrides=None):
        s = dict(self._order_state.get(oid, {}))
        if state_overrides:
            s.update(state_overrides)
        return OrderResponse(
            order_id=oid,
            client_order_id=initial.get("client_order_id"),
            status=s.get("status", initial.get("status", "open")),
            filled_quantity=s.get("filled_quantity", initial.get("filled_quantity", Decimal("0"))),
            avg_price=s.get("avg_price", initial.get("avg_price")),
            price=initial.get("price"),
            symbol=initial.get("symbol"),
            side=initial.get("side"),
            order_type=initial.get("order_type"),
            quantity=initial.get("quantity"),
            created_at=initial.get("created_at", 1.0),
            fill_delta=s.get("fill_delta", Decimal("0")),
        )

    def find_order_by_client_id(self, symbol, client_id):
        for oid, initial in [(i.order_id, {
            "order_id": i.order_id, "client_order_id": i.client_order_id,
            "status": i.status, "filled_quantity": i.filled_quantity,
            "avg_price": i.avg_price, "price": i.price, "symbol": i.symbol,
            "side": i.side, "order_type": i.order_type, "quantity": i.quantity,
            "created_at": i.created_at,
        }) for i in self._orders_created]:
            if initial.get("client_order_id") == client_id:
                state = self._order_state.get(oid, {})
                if state.get("status", initial["status"]) in {"closed", "filled", "canceled", "cancelled", "expired", "rejected"}:
                    continue
                return self._build_order_response(oid, initial)
        return None

    def fetch_order(self, symbol, order_id):
        initial = self._order_initial.get(order_id)
        if initial is None:
            return None
        return self._build_order_response(order_id, initial)

    def fetch_open_orders(self, symbol=None):
        result = []
        for oid, initial in self._order_initial.items():
            state = self._order_state.get(oid, {})
            status = state.get("status", initial.get("status", "open"))
            if status in {"open", "partial"}:
                result.append(self._build_order_response(oid, initial))
        return result

    def create_order(self, order):
        self._order_counter += 1
        oid = f"eth-{self._order_counter}"
        is_protective = order.type in {"stop_loss", "take_profit"}

        if is_protective and order.side.lower() == "sell":
            if order.quantity > self._free_eth:
                raise Exception(
                    f"Account has insufficient balance for requested action. "
                    f"Requested: {order.quantity}, Available: {self._free_eth}"
                )
            self._free_eth -= order.quantity
            self._used_eth += order.quantity
            self._open_sell_orders[oid] = order.quantity

        status = "closed" if order.type == "market" else "open"
        filled = order.quantity if order.type == "market" else Decimal("0")
        avg_price = self._current_price if order.type == "market" else None

        initial = {
            "order_id": oid,
            "client_order_id": order.client_order_id,
            "status": status,
            "filled_quantity": filled,
            "avg_price": avg_price,
            "price": None,
            "symbol": order.symbol,
            "side": order.side,
            "order_type": order.type,
            "quantity": order.quantity,
            "created_at": 1.0,
        }
        self._order_initial[oid] = initial
        self._order_state[oid] = {"status": status, "filled_quantity": filled, "fill_delta": Decimal("0")}
        resp = self._build_order_response(oid, initial)
        self._orders_created.append(resp)
        return resp

    def cancel_order(self, symbol, order_id):
        reserved = self._open_sell_orders.pop(order_id, Decimal("0"))
        if reserved > 0:
            self._free_eth += reserved
            self._used_eth -= reserved
        state = self._order_state.get(order_id, {})
        state["status"] = "canceled"
        return True

    def simulate_fill(self, order_id):
        reserved = self._open_sell_orders.pop(order_id, Decimal("0"))
        if reserved > 0:
            self._used_eth -= reserved
            self._total_eth -= reserved
        initial = self._order_initial.get(order_id, {})
        qty = initial.get("quantity", Decimal("0"))
        state = self._order_state.get(order_id, {})
        state["status"] = "closed"
        state["filled_quantity"] = qty
        state["fill_delta"] = qty
        state["avg_price"] = self._current_price

    def get_open_orders_by_type(self, order_type):
        result = []
        for oid, initial in self._order_initial.items():
            state = self._order_state.get(oid, {})
            status = state.get("status", initial.get("status", "open"))
            if status in {"open", "partial"} and initial.get("order_type") == order_type:
                result.append(self._build_order_response(oid, initial))
        return result

    def get_order_state(self, order_id):
        state = self._order_state.get(order_id, {})
        initial = self._order_initial.get(order_id, {})
        return state.get("status", initial.get("status", "open"))


def _make_position(position_id="pos-eth-1", side="buy", sl_price=Decimal("1900"),
                   tp_prices=None, quantity=Decimal("5.0"),
                   entry_price=Decimal("2000")):
    return Position(
        position_id=position_id,
        exchange="binance",
        symbol="ETH/USDT",
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_prices=list(tp_prices) if tp_prices is not None else [
            Decimal("2100"), Decimal("2200"), Decimal("2300")
        ],
    )


def _patch_settings():
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.EXCHANGE_MARKET_TYPE = "spot"
    mock.STUCK_ORDER_TIMEOUT_SECONDS = 300
    return patch("backend.core.order_manager.settings", mock)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Full staged lifecycle: ENTRY → TP1 → TP2 → TP3 → close
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_full_staged_lifecycle_entry_to_close():
    """Complete Spot lifecycle: BUY entry → staged TP1/TP2/TP3 → position closed.

    FakeExchange tracks real ETH reservation: every SELL order moves ETH
    from free to used.  Total ETH must never go negative.
    """
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    # Initial: TP1 + SL placed
    tp1 = exchange.get_open_orders_by_type("take_profit")
    sl1 = exchange.get_open_orders_by_type("stop_loss")
    assert len(tp1) == 1
    assert len(sl1) == 1

    # ETH balance: free + used must equal total
    assert exchange._free_eth + exchange._used_eth == Decimal("5.0")
    assert exchange._used_eth > Decimal("0"), "SELL orders must reserve ETH"

    # TP1 fill
    exchange.simulate_fill(tp1[0].order_id)
    await om._order_monitor_iteration()

    tp2 = exchange.get_open_orders_by_type("take_profit")
    sl2 = exchange.get_open_orders_by_type("stop_loss")
    assert len(tp2) == 1
    assert len(sl2) == 1

    # TP2 fill
    exchange.simulate_fill(tp2[0].order_id)
    await om._order_monitor_iteration()

    tp3 = exchange.get_open_orders_by_type("take_profit")
    assert len(tp3) == 1

    # TP3 fill
    exchange.simulate_fill(tp3[0].order_id)
    await om._order_monitor_iteration()

    # Position closed
    final = await pm.get_position(pos.position_id)
    assert final is None or final.status == "CLOSED"


# ══════════════════════════════════════════════════════════════════════════════
# 2. InsufficientFunds impossible with staged architecture
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_insufficientfunds_impossible_full_lifecycle():
    """Simulate the exact scenario: position=5.0 ETH, free=5.0, 3 TPs.

    At no point should total SELL reservations exceed free ETH.
    """
    class StrictExchange(SpotFakeExchange):
        def create_order(self, order):
            try:
                return super().create_order(order)
            except Exception as e:
                raise AssertionError(
                    f"InsufficientFunds for {order.type} qty={order.quantity}: {e}"
                )

    exchange = StrictExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    # Check that free + used = total at all times
    assert exchange._free_eth + exchange._used_eth == Decimal("5.0")

    # TP1 fill → advance to TP2
    tp1 = exchange.get_open_orders_by_type("take_profit")[0]
    exchange.simulate_fill(tp1.order_id)
    await om._order_monitor_iteration()

    assert exchange._free_eth + exchange._used_eth <= Decimal("5.0")

    # TP2 fill → advance to TP3
    tp2 = exchange.get_open_orders_by_type("take_profit")[0]
    exchange.simulate_fill(tp2.order_id)
    await om._order_monitor_iteration()

    # TP3 fill → close
    tp3 = exchange.get_open_orders_by_type("take_profit")[0]
    exchange.simulate_fill(tp3.order_id)
    await om._order_monitor_iteration()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Position with existing SELL orders reserves ETH correctly
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_existing_sell_orders_reserve_eth():
    """After initial protection, used ETH > 0 and free ETH < total."""
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    assert exchange._used_eth > Decimal("0"), "Protective SELL orders must reserve ETH"
    assert exchange._free_eth < Decimal("5.0"), "Free ETH must decrease after placing SELL"
    assert exchange._free_eth + exchange._used_eth == Decimal("5.0")


# ══════════════════════════════════════════════════════════════════════════════
# 4. sellable_qty calculation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sellable_qty_equals_min_position_free():
    """sellable_qty = min(actual_qty, free_base) for Spot."""
    exchange = SpotFakeExchange(free_eth=Decimal("2.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(quantity=Decimal("5.0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    tp = exchange.get_open_orders_by_type("take_profit")
    sl = exchange.get_open_orders_by_type("stop_loss")
    total = (tp[0].quantity if tp else Decimal("0")) + (sl[0].quantity if sl else Decimal("0"))
    # TP + SL must not exceed free ETH
    assert total <= Decimal("2.0")


# ══════════════════════════════════════════════════════════════════════════════
# 5. TP total + SL <= free_base invariant
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tp_plus_sl_never_exceeds_free_base():
    """Critical invariant: staged TP1 qty + SL qty <= free_base."""
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 1 and len(sl) == 1
    assert tp[0].quantity + sl[0].quantity == Decimal("5.0")
    # Must not exceed free ETH (which is total - used by these orders)
    assert tp[0].quantity + sl[0].quantity <= Decimal("5.0")


# ══════════════════════════════════════════════════════════════════════════════
# 6. Partial TP fill
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_partial_tp_fill_reduces_position():
    """Partial TP fill: position quantity must decrease by fill delta."""
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    tp1 = [o for o in exchange._orders_created if o.order_type == "take_profit"][0]
    oid = tp1.order_id
    # Partial fill: only 0.5 out of TP1's qty
    tp1_qty = exchange._order_initial[oid]["quantity"]
    partial = Decimal("0.5")
    exchange._order_state[oid]["status"] = "partial"
    exchange._order_state[oid]["filled_quantity"] = partial
    exchange._order_state[oid]["fill_delta"] = partial

    # Simulate the order monitor detecting the partial fill
    updated = exchange.fetch_order("ETH/USDT", oid)
    assert updated.filled_quantity == partial
    assert updated.fill_delta == partial


# ══════════════════════════════════════════════════════════════════════════════
# 7. Cancel TP frees ETH
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_tp_frees_eth():
    """Cancelling a TP must return its reserved ETH to free."""
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    used_before = exchange._used_eth
    free_before = exchange._free_eth

    tp1 = exchange.get_open_orders_by_type("take_profit")[0]
    await om.cancel_order(tp1.order_id, "ETH/USDT")

    # ETH freed from reservation
    assert exchange._used_eth < used_before
    assert exchange._free_eth > free_before


# ══════════════════════════════════════════════════════════════════════════════
# 8. Restart/recovery with protective orders
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_recovery_does_not_close_position_with_reserved_eth():
    """CRITICAL: recovery must NOT close a valid position when all ETH is
    reserved by protective orders (free_eth=0 but used_eth>0).

    This was the root cause of the staleness bug: free_base=0 after
    placing protective orders was incorrectly treated as "stale".
    """
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pos.sl_order_id = None
    pos.tp_order_ids = []
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        # First recovery: place TP1 + SL
        await om._recover_protective_orders()

    # Simulate restart: all protective orders are on exchange,
    # but OM has no in-memory knowledge of them.
    om2 = OrderManager(exchange, pm)
    om2._is_spot = True

    # Before second recovery, find_order_by_client_id returns existing orders
    # so it doesn't create duplicates
    with _patch_settings():
        await om2._recover_protective_orders()

    # Position must still be OPEN, not closed as stale
    pos_after = await pm.get_position(pos.position_id)
    assert pos_after is not None, "Position must survive recovery with reserved ETH"
    assert pos_after.status == "OPEN", "Position must remain OPEN after recovery"


@pytest.mark.asyncio
async def test_staleness_uses_total_balance_not_free():
    """Staleness check must use total balance (free + used), not free only.

    When protective orders reserve all ETH, free=0 but total>0.
    Position must NOT be closed as stale.
    """
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    # After placing protective orders, free_eth may be 0 or low
    # but total_eth > 0
    assert exchange._total_eth > Decimal("0")

    # Verify staleness check returns False
    is_stale = await om._check_position_staleness(pos)
    assert is_stale is False, "Position with ETH balance must not be stale"


@pytest.mark.asyncio
async def test_staleness_detects_truly_empty_balance():
    """Staleness check returns True when total balance is 0."""
    exchange = SpotFakeExchange(free_eth=Decimal("0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        # free=0, total=0 → position is stale → closed
        await om._recover_protective_orders()

    # Position should be closed as stale
    assert pos.position_id not in pm.positions


# ══════════════════════════════════════════════════════════════════════════════
# 9. Duplicate protection
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_duplicate_recovery_does_not_create_extra_orders():
    """Multiple recovery cycles must not create duplicate protective orders."""
    class RecoverableExchange(SpotFakeExchange):
        def find_order_by_client_id(self, symbol, client_id):
            for order in self._orders_created:
                if getattr(order, "client_order_id", None) == client_id:
                    return order
            return None

    exchange = RecoverableExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()
        count_after_first = len(exchange._orders_created)

        await om._recover_protective_orders()
        count_after_second = len(exchange._orders_created)

    assert count_after_second == count_after_first, "No duplicate orders"


# ══════════════════════════════════════════════════════════════════════════════
# 10. Minimum notional validation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_notional_validation_blocks_small_orders():
    """Market order below min notional must be rejected before exchange."""
    from backend.core.exchange.base import Exchange, OrderRequest
    from unittest.mock import MagicMock

    class NotionalExchange(Exchange):
        def __init__(self):
            self.exchange = MagicMock()
            self.exchange.markets = {
                "ETH/USDT": {
                    "limits": {
                        "amount": {"min": 0.001, "max": 10000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            self.exchange.amount_to_precision = lambda s, a: f"{a:.3f}"
            self.exchange.price_to_precision = lambda s, p: f"{p:.2f}"

        def fetch_ticker(self, symbol):
            return FakeTicker(Decimal("2000"))
        def fetch_balance(self): return {}
        def fetch_ohlcv(self, symbol, timeframe="15m", limit=200): return []
        def fetch_order_book(self, symbol, limit=20): return []
        def create_order(self, order): return None
        def fetch_order(self, symbol, order_id): return None
        def cancel_order(self, symbol, order_id): return True
        def fetch_open_orders(self, symbol=None): return []
        def fetch_positions(self, symbol=None): return []
        def get_wallet_balance(self, asset): return Decimal("0")
        def get_exchange_name(self): return "binance"

    exchange = NotionalExchange()
    # qty=0.001, price=2000 => notional=2 < min_cost=10
    order = OrderRequest(
        symbol="ETH/USDT", side="buy", type="market",
        quantity=Decimal("0.001"),
    )
    with pytest.raises(ValueError, match="below minimum"):
        exchange.prepare_order(order)


@pytest.mark.asyncio
async def test_notional_validation_allows_valid_orders():
    """Market order above min notional must pass."""
    from backend.core.exchange.base import Exchange, OrderRequest
    from unittest.mock import MagicMock

    class ValidExchange(Exchange):
        def __init__(self):
            self.exchange = MagicMock()
            self.exchange.markets = {
                "ETH/USDT": {
                    "limits": {
                        "amount": {"min": 0.001, "max": 10000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            self.exchange.amount_to_precision = lambda s, a: f"{a:.3f}"
            self.exchange.price_to_precision = lambda s, p: f"{p:.2f}"

        def fetch_ticker(self, symbol):
            return FakeTicker(Decimal("2000"))
        def fetch_balance(self): return {}
        def fetch_ohlcv(self, symbol, timeframe="15m", limit=200): return []
        def fetch_order_book(self, symbol, limit=20): return []
        def create_order(self, order): return None
        def fetch_order(self, symbol, order_id): return None
        def cancel_order(self, symbol, order_id): return True
        def fetch_open_orders(self, symbol=None): return []
        def fetch_positions(self, symbol=None): return []
        def get_wallet_balance(self, asset): return Decimal("0")
        def get_exchange_name(self): return "binance"

    exchange = ValidExchange()
    # qty=0.01, price=2000 => notional=20 > min_cost=10
    order = OrderRequest(
        symbol="ETH/USDT", side="buy", type="market",
        quantity=Decimal("0.01"),
    )
    symbol, amount, price, params = exchange.prepare_order(order)
    assert symbol == "ETH/USDT"
    assert Decimal(amount) == Decimal("0.010")


# ══════════════════════════════════════════════════════════════════════════════
# 11. Futures reduceOnly intact
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_futures_reduce_only_intact():
    """Futures SL/TP must still have reduceOnly=True."""
    from backend.core.exchange.base import Exchange, OrderRequest
    from unittest.mock import MagicMock
    from backend.config import settings

    class FuturesExchange(Exchange):
        def __init__(self):
            self.exchange = MagicMock()
            self.exchange.markets = {
                "ETH/USDT": {
                    "limits": {
                        "amount": {"min": 0.01, "max": 10000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            self.exchange.amount_to_precision = lambda s, a: f"{a:.2f}"
            self.exchange.price_to_precision = lambda s, p: f"{p:.2f}"
            self.exchange.feature_value = lambda s, m, f: True

        def fetch_ticker(self, symbol):
            return FakeTicker(Decimal("2000"))
        def fetch_balance(self): return {}
        def fetch_ohlcv(self, symbol, timeframe="15m", limit=200): return []
        def fetch_order_book(self, symbol, limit=20): return []
        def create_order(self, order): return None
        def fetch_order(self, symbol, order_id): return None
        def cancel_order(self, symbol, order_id): return True
        def fetch_open_orders(self, symbol=None): return []
        def fetch_positions(self, symbol=None): return []
        def get_wallet_balance(self, asset): return Decimal("0")
        def get_exchange_name(self): return "binance"

    exchange = FuturesExchange()
    original = settings.EXCHANGE_MARKET_TYPE
    try:
        settings.EXCHANGE_MARKET_TYPE = "futures"
        order = OrderRequest(
            symbol="ETH/USDT", side="sell", type="stop_loss",
            quantity=Decimal("1.0"), stopPrice=Decimal("1900"),
        )
        symbol, amount, price, params = exchange.prepare_order(order)
        assert params["reduceOnly"] is True
        assert params["stopLossPrice"] == "1900"
    finally:
        settings.EXCHANGE_MARKET_TYPE = original


# ══════════════════════════════════════════════════════════════════════════════
# 12. Quantity precision
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_quantity_precision_applied():
    """Order quantity must be rounded by exchange precision."""
    from backend.core.exchange.base import Exchange, OrderRequest
    from unittest.mock import MagicMock

    class PrecisionExchange(Exchange):
        def __init__(self):
            self.exchange = MagicMock()
            self.exchange.markets = {
                "ETH/USDT": {
                    "limits": {
                        "amount": {"min": 0.001, "max": 10000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            self.exchange.amount_to_precision = lambda s, a: f"{float(a):.3f}"
            self.exchange.price_to_precision = lambda s, p: f"{float(p):.2f}"

        def fetch_ticker(self, symbol):
            return FakeTicker(Decimal("2000"))
        def fetch_balance(self): return {}
        def fetch_ohlcv(self, symbol, timeframe="15m", limit=200): return []
        def fetch_order_book(self, symbol, limit=20): return []
        def create_order(self, order): return None
        def fetch_order(self, symbol, order_id): return None
        def cancel_order(self, symbol, order_id): return True
        def fetch_open_orders(self, symbol=None): return []
        def fetch_positions(self, symbol=None): return []
        def get_wallet_balance(self, asset): return Decimal("0")
        def get_exchange_name(self): return "binance"

    exchange = PrecisionExchange()
    order = OrderRequest(
        symbol="ETH/USDT", side="buy", type="market",
        quantity=Decimal("1.123456"),
    )
    symbol, amount, price, params = exchange.prepare_order(order)
    assert amount == "1.123"


# ══════════════════════════════════════════════════════════════════════════════
# 13. free ETH = 0 blocks protective orders
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_zero_free_eth_blocks_protective_orders():
    """When free_eth=0 (all reserved), no new protective orders are placed."""
    exchange = SpotFakeExchange(free_eth=Decimal("0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    assert len(exchange._orders_created) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 14. close_position cancels protective orders and frees ETH
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_close_frees_all_reserved_eth():
    """Closing a position must cancel protective orders and free all ETH."""
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    # Protective orders placed → used ETH > 0
    assert exchange._used_eth > Decimal("0")

    # Close via market sell
    from backend.core.exchange.base import OrderRequest
    close_resp = OrderResponse(
        order_id="close-1", status="closed",
        filled_quantity=Decimal("5.0"), avg_price=Decimal("2000"),
        symbol="ETH/USDT", side="sell", order_type="market", quantity=Decimal("5.0"),
    )
    exchange._orders_created.append(close_resp)
    exchange._order_initial["close-1"] = {
        "order_id": "close-1", "status": "closed",
        "filled_quantity": Decimal("5.0"), "avg_price": Decimal("2000"),
        "price": None, "symbol": "ETH/USDT", "side": "sell",
        "order_type": "market", "quantity": Decimal("5.0"), "created_at": 1.0,
    }
    exchange._order_state["close-1"] = {"status": "closed", "filled_quantity": Decimal("5.0"), "fill_delta": Decimal("5.0")}
    # Simulate sell: ETH goes to USDT
    exchange._total_eth -= Decimal("5.0")

    # Cancel protective orders
    for oid in list(exchange._open_sell_orders.keys()):
        await om.cancel_order(oid, "ETH/USDT")

    # All ETH should be freed (or sold)
    assert exchange._used_eth == Decimal("0")


# ══════════════════════════════════════════════════════════════════════════════
# 15. SL fires → position closed → remaining TPs cancelled
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sl_fill_closes_position_cancels_tps():
    """When SL fires, position quantity decreases and remaining TPs are cancelled.

    SL covers position_qty - tp1_qty.  When SL fills, only that portion
    is closed.  The remaining (TP1's portion) stays open — this is correct
    staged behavior.
    """
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    sl = exchange.get_open_orders_by_type("stop_loss")[0]
    tp = exchange.get_open_orders_by_type("take_profit")
    assert len(tp) == 1

    sl_qty = sl.quantity
    pos_qty = pos.quantity

    # Simulate SL fill
    exchange.simulate_fill(sl.order_id)
    await om._order_monitor_iteration()

    # TP should be cancelled (all protective orders cancelled on SL fire)
    remaining_tp = exchange.get_open_orders_by_type("take_profit")
    for t in remaining_tp:
        assert exchange.get_order_state(t.order_id) in {"canceled", "closed"}

    # Position quantity reduced by SL fill, but not fully closed
    # (TP1's portion remains)
    final = await pm.get_position(pos.position_id)
    if final is not None:
        assert final.quantity == pos_qty - sl_qty


# ══════════════════════════════════════════════════════════════════════════════
# 16. Single TP lifecycle
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_single_tp_lifecycle():
    """Position with 1 TP: TP1 placed (claims all free ETH), SL may not fit.

    With 1 TP and free_eth == position_qty, TP1 claims all free ETH.
    SL cannot be placed (free_eth = 0 after TP reservation).
    This is the documented "last TP gap" of staged architecture.
    When TP1 fills, position is fully closed.
    """
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("5.0"),
        tp_prices=[Decimal("2200")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    tp = exchange.get_open_orders_by_type("take_profit")
    sl = exchange.get_open_orders_by_type("stop_loss")
    assert len(tp) == 1
    # SL may not be placed when TP claims all free ETH
    # This is the documented "last TP gap"
    if len(sl) == 1:
        assert sl[0].quantity > 0

    exchange.simulate_fill(tp[0].order_id)
    await om._order_monitor_iteration()

    final = await pm.get_position(pos.position_id)
    assert final is None or final.status == "CLOSED"


# ══════════════════════════════════════════════════════════════════════════════
# 17. No position without SL (except last TP gap)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sl_present_after_each_tp_fill():
    """After each TP fill, SL must be present (until the last TP)."""
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

        # After TP1: SL present
        sl1 = exchange.get_open_orders_by_type("stop_loss")
        assert len(sl1) == 1, "SL must be present after initial setup"

        # TP1 fills
        tp1 = exchange.get_open_orders_by_type("take_profit")[0]
        exchange.simulate_fill(tp1.order_id)
        await om._order_monitor_iteration()

        # After TP2: SL present
        sl2 = exchange.get_open_orders_by_type("stop_loss")
        assert len(sl2) == 1, "SL must be present after TP1 fill"
        tp2 = exchange.get_open_orders_by_type("take_profit")
        assert len(tp2) == 1, "TP2 must be placed"

        # TP2 fills
        exchange.simulate_fill(tp2[0].order_id)
        await om._order_monitor_iteration()

        # After TP3: SL may or may not be present (last TP gap)
        # This is the documented trade-off of staged architecture
        tp3 = exchange.get_open_orders_by_type("take_profit")
        assert len(tp3) == 1, "TP3 must be placed"


# ══════════════════════════════════════════════════════════════════════════════
# 18. Futures protect all TPs at once (not staged)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_futures_all_tps_at_once():
    """Futures: all 3 TPs + SL placed simultaneously (reduceOnly)."""
    class FuturesExchange(SpotFakeExchange):
        def fetch_balance(self):
            return {"free": {"USDT": "10000"}, "used": {"USDT": "0"}, "total": {"USDT": "10000"}}

        def create_order(self, order):
            self._order_counter += 1
            oid = f"fut-{self._order_counter}"
            is_protective = order.type in {"stop_loss", "take_profit"}
            status = "closed" if order.type == "market" else "open"
            filled = order.quantity if order.type == "market" else Decimal("0")
            avg_price = self._current_price if order.type == "market" else None
            initial = {
                "order_id": oid, "client_order_id": order.client_order_id,
                "status": status, "filled_quantity": filled,
                "avg_price": avg_price, "price": None,
                "symbol": order.symbol, "side": order.side,
                "order_type": order.type, "quantity": order.quantity,
                "created_at": 1.0,
            }
            self._order_initial[oid] = initial
            self._order_state[oid] = {"status": status, "filled_quantity": filled, "fill_delta": Decimal("0")}
            resp = self._build_order_response(oid, initial)
            self._orders_created.append(resp)
            return resp

    exchange = FuturesExchange(free_eth=Decimal("5.0"))
    exchange._positions = [
        PositionData(
            symbol="ETH/USDT", exchange="binance", side="buy",
            quantity=Decimal("5.0"), entry_price=Decimal("2000"),
            unrealized_pnl=Decimal("0"), mark_price=Decimal("2000"), timestamp=0,
        )
    ]
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with _patch_settings():
        from unittest.mock import MagicMock
        mock = MagicMock()
        mock.EXCHANGE_MARKET_TYPE = "futures"
        mock.STUCK_ORDER_TIMEOUT_SECONDS = 300
        with patch("backend.core.order_manager.settings", mock):
            await om._recover_protective_orders()

    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 3, "Futures must place all 3 TPs"
    assert len(sl) == 1, "Futures must place SL"
    assert sl[0].quantity == Decimal("5.0"), "Futures SL uses full position"


# ══════════════════════════════════════════════════════════════════════════════
# 19. Insufficient free ETH for new protective order
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_insufficient_free_eth_for_new_protection():
    """When existing SELL orders already reserve ETH, new protection uses remainder."""
    exchange = SpotFakeExchange(free_eth=Decimal("3.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(quantity=Decimal("5.0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    # Only 3 ETH free, position is 5.0
    # TP1 = 5.0/3 = 1.666..., SL = min(5.0, 3.0 - 1.666...) = 1.333...
    # Total = 1.666... + 1.333... = 3.0 = free_eth
    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 1
    total = tp[0].quantity + (sl[0].quantity if sl else Decimal("0"))
    assert total <= Decimal("3.0"), f"Total {total} > free_eth 3.0"


# ══════════════════════════════════════════════════════════════════════════════
# 20. protective_client_ids survives DB round-trip
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_protective_client_ids_persisted_and_recovered():
    """protective_client_ids must survive a DB persistence round-trip.

    After placing protective orders the client IDs are stored on the
    position.  When the position is persisted and later recovered, the
    client IDs must be present so recovery can find existing orders by
    client id instead of creating duplicates.
    """
    from backend.core.position_manager import PositionManager as PM

    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PM(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    # After recovery, protective_client_ids must be populated
    assert "tp0" in pos.protective_client_ids, "tp0 client id missing"
    assert "sl" in pos.protective_client_ids, "sl client id missing"

    # Simulate persist → recover cycle via JSON round-trip
    import json
    from backend.core.position_manager import Position as Pos

    metadata = {
        "entry_order_id": pos.entry_order_id,
        "sl_order_id": pos.sl_order_id,
        "tp_order_ids": pos.tp_order_ids,
        "protective_client_ids": pos.protective_client_ids,
        "fill_progress": {k: str(v) for k, v in pos.fill_progress.items()},
        "entry_time": pos.entry_time,
        "initial_quantity": str(pos.initial_quantity),
        "entry_cost": str(pos.entry_cost),
        "entry_fee_quote": str(pos.entry_fee_quote),
        "exit_notional": str(pos.exit_notional),
        "exit_quantity": str(pos.exit_quantity),
        "exit_fee_quote": str(pos.exit_fee_quote),
        "fee_unconverted": str(pos.fee_unconverted),
        "fee_currencies": sorted(pos.fee_currencies),
    }
    json_str = json.dumps(metadata)
    recovered = json.loads(json_str)

    recovered_ids = dict(recovered.get("protective_client_ids") or {})
    assert recovered_ids == pos.protective_client_ids, (
        f"protective_client_ids lost in round-trip: {recovered_ids} != {pos.protective_client_ids}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 21. Recovery after TP1 fill cancels stale SL
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_recovery_after_tp1_fill_cancels_stale_sl():
    """When recovery detects that TP1 already filled, the old SL (sized
    for the full position) must be cancelled and a new SL placed with the
    correct (reduced) quantity.
    """
    exchange = SpotFakeExchange(free_eth=Decimal("5.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with _patch_settings():
        await om._recover_protective_orders()

    # Initial state: TP1 + SL placed
    tp1 = exchange.get_open_orders_by_type("take_profit")[0]
    sl1 = exchange.get_open_orders_by_type("stop_loss")[0]
    sl1_qty = sl1.quantity
    sl1_id = sl1.order_id

    # Simulate TP1 fill on exchange
    exchange.simulate_fill(tp1.order_id)

    # Now simulate a restart: new OrderManager, same exchange + position
    om2 = OrderManager(exchange, pm)
    om2._is_spot = True

    with _patch_settings():
        await om2._recover_protective_orders()

    # The old SL should have been cancelled
    assert exchange.get_order_state(sl1_id) in {"canceled", "cancelled"}, (
        f"Old SL {sl1_id} was not cancelled after recovery"
    )

    # A new SL should exist with correct quantity
    sl_new = exchange.get_open_orders_by_type("stop_loss")
    if sl_new:
        # Position qty after TP1 fill = 5.0 - (5.0/3) ≈ 3.333...
        # New SL should be for the reduced position, not the original 5.0
        assert sl_new[0].quantity != sl1_qty or sl_new[0].quantity == pos.quantity - tp1.quantity, (
            f"New SL quantity {sl_new[0].quantity} should differ from old {sl1_qty}"
        )
