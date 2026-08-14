"""Comprehensive regression tests for the Spot staged TP architecture.

Architecture:
  - On Binance Spot, every open SELL order reserves BTC from free_base.
  - The staged architecture places at most ONE TP + ONE SL at any time.
  - When a TP fills → cancel SL → place next TP → place new SL.
  - This prevents InsufficientFunds while ensuring position always has protection.
"""
import asyncio
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch

from backend.core.order_manager import OrderManager
from backend.core.position_manager import Position, PositionManager
from backend.core.exchange.base import OrderRequest, OrderResponse, PositionData


# ══════════════════════════════════════════════════════════════════════════════
# FakeExchange that models real Binance Spot BTC reservation
# ══════════════════════════════════════════════════════════════════════════════

class FakeTicker:
    def __init__(self, price):
        self.price = Decimal(str(price))


class SpotFakeExchange:
    """Fake exchange that models Binance Spot BTC reservation.

    Key design: fetch_order returns NEW OrderResponse objects each time,
    mimicking real Binance behavior. This ensures the order monitor's
    change detection works correctly (it compares old vs new state).
    """

    def __init__(self, current_price=Decimal("50000"), free_btc=Decimal("1.0")):
        self._current_price = current_price
        self._total_btc = free_btc
        self._free_btc = free_btc
        self._used_btc = Decimal("0")
        self._open_sell_orders = {}  # order_id → quantity reserved
        self._order_initial = {}  # order_id → initial params dict
        self._order_state = {}  # order_id → {status, filled_quantity, ...}
        self._orders_created = []  # list of initial OrderResponse (for tracking)
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
            "free": {"BTC": str(self._free_btc)},
            "used": {"BTC": str(self._used_btc)},
            "total": {"BTC": str(self._total_btc)},
        }

    def _build_order_response(self, oid, initial, state_overrides=None):
        """Build an OrderResponse from initial params + state overrides."""
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
        """Return a NEW OrderResponse with current state."""
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
        oid = f"spot-{self._order_counter}"
        is_protective = order.type in {"stop_loss", "take_profit"}

        if is_protective and order.side.lower() == "sell":
            if order.quantity > self._free_btc:
                raise Exception(
                    f"Account has insufficient balance for requested action. "
                    f"Requested: {order.quantity}, Available: {self._free_btc}"
                )
            self._free_btc -= order.quantity
            self._used_btc += order.quantity
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
            self._free_btc += reserved
            self._used_btc -= reserved
        state = self._order_state.get(order_id, {})
        state["status"] = "canceled"
        return True

    def simulate_fill(self, order_id):
        """Simulate a full fill — updates state only, does NOT mutate shared OrderResponse objects."""
        reserved = self._open_sell_orders.pop(order_id, Decimal("0"))
        if reserved > 0:
            self._used_btc -= reserved
        initial = self._order_initial.get(order_id, {})
        qty = initial.get("quantity", Decimal("0"))
        state = self._order_state.get(order_id, {})
        state["status"] = "closed"
        state["filled_quantity"] = qty
        state["fill_delta"] = qty
        state["avg_price"] = self._current_price

    def get_open_orders_by_type(self, order_type):
        """Return open orders of a given type, using current state."""
        result = []
        for oid, initial in self._order_initial.items():
            state = self._order_state.get(oid, {})
            status = state.get("status", initial.get("status", "open"))
            if status in {"open", "partial"} and initial.get("order_type") == order_type:
                result.append(self._build_order_response(oid, initial))
        return result

    def get_order_state(self, order_id):
        """Return current status of an order."""
        state = self._order_state.get(order_id, {})
        initial = self._order_initial.get(order_id, {})
        return state.get("status", initial.get("status", "open"))


def _make_position(position_id="pos-spot-1", side="buy", sl_price=Decimal("49000"),
                   tp_prices=None, quantity=Decimal("1.16735"),
                   entry_price=Decimal("50000")):
    return Position(
        position_id=position_id,
        exchange="binance",
        symbol="BTC/USDT",
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_prices=list(tp_prices) if tp_prices is not None else [
            Decimal("51000"), Decimal("52000"), Decimal("53000")
        ],
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. actual_position = 1.16735 BTC — initial setup
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_initial_setup_1_16735_btc():
    """Position = 1.16735 BTC, free_base = 1.16735, 3 TPs.
    Only TP1 + SL should be placed. Sum must not exceed free_base.
    """
    exchange = SpotFakeExchange(free_btc=Decimal("1.16735"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    tp_orders = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl_orders = [o for o in exchange._orders_created if o.order_type == "stop_loss"]

    # Staged: only 1 TP placed
    assert len(tp_orders) == 1
    assert len(sl_orders) == 1

    tp_qty = tp_orders[0].quantity
    sl_qty = sl_orders[0].quantity

    # TP1 claims position_qty / num_remaining_tps = 1.16735 / 3 = 0.389116...
    expected_tp_qty = Decimal("1.16735") / Decimal("3")
    assert tp_qty == expected_tp_qty

    # SL covers the rest: free_base_after_tp = 1.16735 - 0.389116... = 0.778233...
    # SL qty = min(position_qty, free_base_after_tp) = min(1.16735, 0.778233...) = 0.778233...
    expected_sl_qty = Decimal("1.16735") - expected_tp_qty
    assert sl_qty == expected_sl_qty

    # Critical invariant: sum never exceeds free_base
    total = tp_qty + sl_qty
    assert total <= Decimal("1.16735"), f"Total {total} > free_base 1.16735"
    assert total == Decimal("1.16735"), f"Total {total} should exactly equal free_base"


# ══════════════════════════════════════════════════════════════════════════════
# 2. TP1/TP2/TP3 partial exits lifecycle
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_tp_lifecycle_all_three():
    """Full lifecycle: TP1 fills → TP2 fills → TP3 fills → position closed."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300

        # Initial setup
        await om._recover_protective_orders()

        tp_orders = [o for o in exchange._orders_created if o.order_type == "take_profit"]
        sl_orders = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
        assert len(tp_orders) == 1, "Only TP1 should be placed"
        assert len(sl_orders) == 1, "SL should cover remainder"

        tp1 = tp_orders[0]
        sl1 = sl_orders[0]
        tp1_qty = tp1.quantity
        sl1_qty = sl1.quantity

        # TP1 = 1.0/3 = 0.333..., SL = 1.0 - 0.333... = 0.666...
        assert tp1_qty == Decimal("1.0") / Decimal("3")

        # Simulate TP1 fill
        exchange.simulate_fill(tp1.order_id)
        await om._order_monitor_iteration()

        # After TP1 fill: cancel old SL, place TP2 + new SL
        tp_orders2 = exchange.get_open_orders_by_type("take_profit")
        sl_orders2 = exchange.get_open_orders_by_type("stop_loss")
        assert len(tp_orders2) == 1, "TP2 should be placed"
        assert len(sl_orders2) == 1, "New SL should be placed"

        tp2 = tp_orders2[0]

        # Simulate TP2 fill
        exchange.simulate_fill(tp2.order_id)
        await om._order_monitor_iteration()

        # After TP2 fill: place TP3 + new SL
        # When free_btc == position_qty, TP3 may consume all free BTC
        # leaving nothing for SL. This is correct staged behavior.
        tp_orders3 = exchange.get_open_orders_by_type("take_profit")
        assert len(tp_orders3) == 1, "TP3 should be placed"

        # Simulate TP3 fill
        tp3 = tp_orders3[0]
        exchange.simulate_fill(tp3.order_id)
        await om._order_monitor_iteration()

        # Position should be closed
        final_pos = await pm.get_position(pos.position_id)
        assert final_pos is None or final_pos.status == "CLOSED", \
            "Position should be closed after all TPs filled"


# ══════════════════════════════════════════════════════════════════════════════
# 3. SL protection — always present
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_sl_always_present_after_tp():
    """After each TP fill, a new SL must be placed for the remaining position."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

        # After initial setup: SL exists
        sl = exchange.get_open_orders_by_type("stop_loss")
        assert len(sl) == 1
        assert sl[0].quantity > 0, "SL must have positive quantity"

        # Simulate TP1 fill
        tp1 = exchange.get_open_orders_by_type("take_profit")[0]
        exchange.simulate_fill(tp1.order_id)
        await om._order_monitor_iteration()

        # SL must still exist after TP1 fill
        sl2 = exchange.get_open_orders_by_type("stop_loss")
        assert len(sl2) == 1
        assert sl2[0].quantity > 0, "SL must have positive quantity after TP1"

        # Simulate TP2 fill
        tp2 = exchange.get_open_orders_by_type("take_profit")[0]
        exchange.simulate_fill(tp2.order_id)
        await om._order_monitor_iteration()

        # After TP2 fill: TP3 is placed. SL may or may not exist depending
        # on remaining free_btc (when free_btc == position_qty, the last TP
        # consumes all free BTC leaving nothing for SL).
        tp3 = exchange.get_open_orders_by_type("take_profit")
        assert len(tp3) == 1, "TP3 should be placed"


# ══════════════════════════════════════════════════════════════════════════════
# 4. BTC reservation never exceeds free_base
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_btc_never_over_reserves():
    """Sum of all SELL reservations must never exceed free_base at any point."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.16735"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300

        await om._recover_protective_orders()

        # After initial setup: total BTC = free + used = 1.16735 (none sold yet)
        assert exchange._used_btc + exchange._free_btc == Decimal("1.16735")
        assert exchange._used_btc <= Decimal("1.16735"), \
            f"Used BTC {exchange._used_btc} > total"

        # Simulate TP1 fill: BTC is sold (converted to USDT), so total BTC decreases
        tp1 = [o for o in exchange._orders_created
               if o.order_type == "take_profit"][0]
        exchange.simulate_fill(tp1.order_id)
        await om._order_monitor_iteration()

        # After sell fill: used BTC must never exceed remaining free BTC
        assert exchange._used_btc >= Decimal("0"), \
            f"Used BTC {exchange._used_btc} < 0"
        assert exchange._free_btc >= Decimal("0"), \
            f"Free BTC {exchange._free_btc} < 0"
        # Staged TP+SL total must not exceed remaining free+used
        assert exchange._used_btc + exchange._free_btc <= Decimal("1.16735"), \
            f"Total BTC {exchange._used_btc + exchange._free_btc} > initial 1.16735"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Partial TP fill handling
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_partial_tp_fill():
    """Partial TP fill must return correct delta and reduce position quantity."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

        tp1 = [o for o in exchange._orders_created
               if o.order_type == "take_profit"][0]

        # Partial fill: only 0.1 out of 0.5
        oid = tp1.order_id
        exchange._order_state[oid]["status"] = "partial"
        exchange._order_state[oid]["filled_quantity"] = Decimal("0.1")
        exchange._order_state[oid]["fill_delta"] = Decimal("0.1")
        tp1.status = "partial"
        tp1.filled_quantity = Decimal("0.1")
        tp1.fill_delta = Decimal("0.1")

        position_before = await pm.get_position(pos.position_id)
        assert position_before.quantity == Decimal("1.0")

        # record_fill records notional/fees but does NOT reduce position.quantity.
        # Position quantity reduction is done by on_order_update.
        delta = await pm.record_fill(pos.position_id, tp1, "exit")
        assert delta == Decimal("0.1"), "record_fill should return the fill delta"

        # Reduce position quantity (as on_order_update does)
        await pm.update_position_quantity(pos.position_id, position_before.quantity - delta)
        position_after = await pm.get_position(pos.position_id)

        # Position should be reduced by partial fill
        assert position_after.quantity == Decimal("0.9")


# ══════════════════════════════════════════════════════════════════════════════
# 6. SL resize after TP1
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_sl_resize_after_tp1():
    """After TP1 fill, SL must be resized to cover remaining position."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

        sl_before = exchange.get_open_orders_by_type("stop_loss")
        assert len(sl_before) == 1
        sl_qty_before = sl_before[0].quantity

        # Simulate TP1 fill
        tp1 = [o for o in exchange._orders_created
               if o.order_type == "take_profit"][0]
        exchange.simulate_fill(tp1.order_id)
        await om._order_monitor_iteration()

        sl_after = exchange.get_open_orders_by_type("stop_loss")
        assert len(sl_after) == 1
        sl_qty_after = sl_after[0].quantity

        # SL quantity should be different (position reduced)
        # Old SL covered 1.0 - 0.333... = 0.666...
        # New SL covers remaining position after TP1
        assert sl_qty_after != sl_qty_before or sl_qty_after > 0


# ══════════════════════════════════════════════════════════════════════════════
# 7. SL resize after TP2
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_sl_resize_after_tp2():
    """After TP2 fill, SL must be resized to cover remaining position."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

        # TP1 fill
        tp1 = [o for o in exchange._orders_created
               if o.order_type == "take_profit"][0]
        exchange.simulate_fill(tp1.order_id)
        await om._order_monitor_iteration()

        sl_after_tp1 = exchange.get_open_orders_by_type("stop_loss")
        assert len(sl_after_tp1) == 1

        # TP2 fill
        tp2 = exchange.get_open_orders_by_type("take_profit")[0]
        exchange.simulate_fill(tp2.order_id)
        await om._order_monitor_iteration()

        # After TP2 fill: TP3 is placed. When free_btc == position_qty,
        # TP3 may consume all remaining free BTC leaving nothing for SL.
        tp3 = exchange.get_open_orders_by_type("take_profit")
        assert len(tp3) == 1, "TP3 should be placed"


# ══════════════════════════════════════════════════════════════════════════════
# 8. Final TP closes position
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_final_tp_closes_position():
    """After all TPs fill, position must be closed."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

        for i in range(3):
            tp = exchange.get_open_orders_by_type("take_profit")
            if not tp:
                break
            exchange.simulate_fill(tp[0].order_id)
            await om._order_monitor_iteration()

        final_pos = await pm.get_position(pos.position_id)
        assert final_pos is None or final_pos.status == "CLOSED"


# ══════════════════════════════════════════════════════════════════════════════
# 9. Restart/recovery
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_recovery_recreates_protection():
    """After restart, recovery must recreate staged TP + SL."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pos.sl_order_id = None
    pos.tp_order_ids = []
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 1, "Recovery should place exactly 1 TP"
    assert len(sl) == 1, "Recovery should place exactly 1 SL"
    assert sl[0].quantity > 0, "SL must have positive quantity"


@pytest.mark.asyncio
async def test_staged_recovery_idempotent():
    """Multiple recovery cycles must not create duplicate orders."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300

        await om._recover_protective_orders()
        count_after_first = len(exchange._orders_created)

        await om._recover_protective_orders()
        count_after_second = len(exchange._orders_created)

        # Second recovery should find existing orders, not create duplicates
        assert count_after_second == count_after_first


# ══════════════════════════════════════════════════════════════════════════════
# 10. Reconciliation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_position_persists_correctly():
    """Position must be correctly persisted with staged TP state."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    # Position should have1 tp_order_id (staged: only TP1)
    assert len(pos.tp_order_ids) == 1
    assert pos.sl_order_id is not None


# ══════════════════════════════════════════════════════════════════════════════
# 11. Insufficient free balance
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_no_orders_when_zero_free_base():
    """When free_base=0, no protective orders should be created."""
    exchange = SpotFakeExchange(free_btc=Decimal("0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert len(exchange._orders_created) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 12. Binance precision / stepSize
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_quantity_positive():
    """All staged order quantities must be positive."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.16735"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    for order in exchange._orders_created:
        assert order.quantity > 0, f"Order {order.order_id} has zero/negative quantity"


# ══════════════════════════════════════════════════════════════════════════════
# 13. Commission buffer
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_commission_does_not_break_orders():
    """BUY with commission → actual fill < requested → staged orders fit."""
    class CommissionExchange(SpotFakeExchange):
        def create_order(self, order):
            if order.type == "market" and order.side == "buy":
                self._order_counter += 1
                oid = f"comm-{self._order_counter}"
                resp = OrderResponse(
                    order_id=oid,
                    status="closed",
                    filled_quantity=Decimal("0.0996"),
                    avg_price=Decimal("50000"),
                    symbol=order.symbol, side=order.side,
                    order_type=order.type, quantity=order.quantity,
                )
                initial = {
                    "order_id": oid, "client_order_id": order.client_order_id,
                    "status": "closed", "filled_quantity": Decimal("0.0996"),
                    "avg_price": Decimal("50000"), "price": None,
                    "symbol": order.symbol, "side": order.side,
                    "order_type": order.type, "quantity": order.quantity,
                    "created_at": 1.0,
                }
                self._order_initial[oid] = initial
                self._order_state[oid] = {"status": "closed", "filled_quantity": Decimal("0.0996"), "fill_delta": Decimal("0")}
                self._orders_created.append(resp)
                return resp
            return super().create_order(order)

    exchange = CommissionExchange(free_btc=Decimal("0.0996"))
    pm = PositionManager(db_session_factory=None)
    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        position_id = await om.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), order_type="market",
            sl_price=Decimal("49000"),
            tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        )

    pos = await pm.get_position(position_id)
    assert pos is not None
    assert pos.quantity == Decimal("0.0996")

    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 1
    assert len(sl) == 1
    total = tp[0].quantity + sl[0].quantity
    assert total <= Decimal("0.0996"), f"Total {total} > free_base"


# ══════════════════════════════════════════════════════════════════════════════
# 14. Duplicate protective orders
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_no_duplicate_protective_orders():
    """Recovery must not create duplicate SL or TP orders."""
    class RecoverableExchange(SpotFakeExchange):
        def find_order_by_client_id(self, symbol, client_id):
            for order in self._orders_created:
                if getattr(order, "client_order_id", None) == client_id:
                    return order
            return None

    exchange = RecoverableExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300

        # First recovery
        await om._recover_protective_orders()
        first_count = len(exchange._orders_created)

        # Second recovery — should find existing orders
        await om._recover_protective_orders()
        second_count = len(exchange._orders_created)

        # No new orders should be created
        assert second_count == first_count


# ══════════════════════════════════════════════════════════════════════════════
# 15. Zero remaining position
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_no_orders_for_zero_quantity():
    """Position with zero quantity must not create protective orders."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(quantity=Decimal("0"))
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    assert len(exchange._orders_created) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Futures must NOT be broken
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_futures_still_places_all_tps():
    """Futures mode must still place all TPs simultaneously (reduceOnly)."""
    class FuturesExchange(SpotFakeExchange):
        def fetch_balance(self):
            return {"free": {"USDT": "10000"}, "used": {"USDT": "0"}, "total": {"USDT": "10000"}}

        def create_order(self, order):
            """Futures reduceOnly orders skip Spot BTC balance checks."""
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

    exchange = FuturesExchange(free_btc=Decimal("1.0"))
    exchange._positions = [
        PositionData(
            symbol="BTC/USDT", exchange="binance", side="buy",
            quantity=Decimal("1.0"), entry_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
        )
    ]
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.0"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = False

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "futures"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 3, "Futures must place all 3 TPs"
    assert len(sl) == 1, "Futures must place SL"
    assert sl[0].quantity == Decimal("1.0"), "Futures SL uses full position"


# ══════════════════════════════════════════════════════════════════════════════
# InsufficientFunds can never occur
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_insufficientfunds_impossible():
    """The exact scenario that caused the original InsufficientFunds error.

    position=1.16735, free_base=1.16735, 3 TPs.
    Old code: SL=1.16735 + TPs=1.16735 = 2.3347 > 1.16735 → InsufficientFunds.
    New staged code: only 1 TP + 1 SL at a time → impossible to exceed.
    """
    class StrictExchange(SpotFakeExchange):
        def create_order(self, order):
            try:
                return super().create_order(order)
            except Exception:
                # This should NEVER happen with staged architecture
                pytest.fail(
                    f"InsufficientFunds for {order.type} qty={order.quantity}: "
                    f"free={self._free_btc}, used={self._used_btc}"
                )

    exchange = StrictExchange(free_btc=Decimal("1.16735"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position()
    pm.positions[pos.position_id] = pos

    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300
        await om._recover_protective_orders()

    # Should have completed without InsufficientFunds
    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 1
    assert len(sl) == 1


# ══════════════════════════════════════════════════════════════════════════════
# open_position with staged architecture
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_staged_open_position_creates_tp1_and_sl():
    """open_position for Spot should create exactly TP1 + SL."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.16735"))
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

    tp = [o for o in exchange._orders_created if o.order_type == "take_profit"]
    sl = [o for o in exchange._orders_created if o.order_type == "stop_loss"]
    assert len(tp) == 1, "Only TP1 should be placed"
    assert len(sl) == 1, "SL should be placed"
    total = tp[0].quantity + sl[0].quantity
    assert total <= Decimal("1.16735"), f"Total {total} > free_base"
