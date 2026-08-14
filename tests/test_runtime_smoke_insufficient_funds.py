"""Runtime smoke test: BUY -> SL/TP without InsufficientFunds."""
import asyncio
import pytest
from decimal import Decimal
from unittest.mock import patch

from tests.test_spot_staged_tp_architecture import SpotFakeExchange, _make_position
from backend.core.order_manager import OrderManager
from backend.core.position_manager import PositionManager


@pytest.mark.asyncio
async def test_spot_buy_1_btc_staged_protection():
    """BUY 1.0 BTC -> TP1 + SL (staged). Total must never exceed free_base."""
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

        tp = exchange.get_open_orders_by_type("take_profit")
        sl = exchange.get_open_orders_by_type("stop_loss")
        assert len(tp) == 1
        assert len(sl) == 1
        total = tp[0].quantity + sl[0].quantity
        assert total == Decimal("1.0")

        # TP1 fill -> advance to TP2
        exchange.simulate_fill(tp[0].order_id)
        await om._order_monitor_iteration()

        tp2 = exchange.get_open_orders_by_type("take_profit")
        sl2 = exchange.get_open_orders_by_type("stop_loss")
        assert len(tp2) == 1
        assert len(sl2) == 1
        total2 = tp2[0].quantity + sl2[0].quantity
        assert total2 <= Decimal("1.0")

        # TP2 fill -> advance to TP3
        exchange.simulate_fill(tp2[0].order_id)
        await om._order_monitor_iteration()

        tp3 = exchange.get_open_orders_by_type("take_profit")
        assert len(tp3) == 1

        # TP3 fill -> position closed
        exchange.simulate_fill(tp3[0].order_id)
        await om._order_monitor_iteration()

        final_pos = await pm.get_position(pos.position_id)
        assert final_pos is None or final_pos.status == "CLOSED"


@pytest.mark.asyncio
async def test_spot_buy_exact_1_16735_btc():
    """Exact Binance Testnet scenario: 1.16735 BTC, 3 TPs."""
    exchange = SpotFakeExchange(free_btc=Decimal("1.16735"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.16735"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos
    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300

        await om._recover_protective_orders()

        tp = exchange.get_open_orders_by_type("take_profit")
        sl = exchange.get_open_orders_by_type("stop_loss")
        total = tp[0].quantity + sl[0].quantity
        assert total <= Decimal("1.16735")
        assert total == Decimal("1.16735")


@pytest.mark.asyncio
async def test_spot_partial_free_btc():
    """BUY 1.0 BTC with only 0.5 free_btc."""
    exchange = SpotFakeExchange(free_btc=Decimal("0.5"))
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

        tp = exchange.get_open_orders_by_type("take_profit")
        sl = exchange.get_open_orders_by_type("stop_loss")
        total = (tp[0].quantity if tp else Decimal("0")) + (sl[0].quantity if sl else Decimal("0"))
        assert total <= Decimal("0.5")


@pytest.mark.asyncio
async def test_insufficientfunds_impossible_staged():
    """Staged architecture must never raise InsufficientFunds."""

    class StrictExchange(SpotFakeExchange):
        def create_order(self, order):
            try:
                return super().create_order(order)
            except Exception as e:
                raise AssertionError(
                    f"InsufficientFunds for {order.type} qty={order.quantity}: {e}"
                )

    exchange = StrictExchange(free_btc=Decimal("1.16735"))
    pm = PositionManager(db_session_factory=None)
    pos = _make_position(
        quantity=Decimal("1.16735"),
        tp_prices=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
    )
    pm.positions[pos.position_id] = pos
    om = OrderManager(exchange, pm)
    om._is_spot = True

    with patch("backend.core.order_manager.settings") as mock_settings:
        mock_settings.EXCHANGE_MARKET_TYPE = "spot"
        mock_settings.STUCK_ORDER_TIMEOUT_SECONDS = 300

        await om._recover_protective_orders()

        tp = exchange.get_open_orders_by_type("take_profit")
        sl = exchange.get_open_orders_by_type("stop_loss")
        assert len(tp) == 1
        assert len(sl) == 1


@pytest.mark.asyncio
async def test_futures_still_works():
    """Futures: SL + all 3 TPs at full position qty (reduceOnly)."""
    from backend.core.exchange.base import PositionData

    exchange = SpotFakeExchange(free_btc=Decimal("1.0"))
    exchange._positions = [
        PositionData(
            symbol="BTC/USDT", exchange="binance", side="buy",
            quantity=Decimal("1.0"), entry_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"), mark_price=Decimal("50000"), timestamp=0,
        )
    ]

    def futures_create(order):
        exchange._order_counter += 1
        oid = f"fut-{exchange._order_counter}"
        status = "closed" if order.type == "market" else "open"
        filled = order.quantity if order.type == "market" else Decimal("0")
        avg_price = exchange._current_price if order.type == "market" else None
        initial = {
            "order_id": oid, "client_order_id": order.client_order_id,
            "status": status, "filled_quantity": filled,
            "avg_price": avg_price, "price": None,
            "symbol": order.symbol, "side": order.side,
            "order_type": order.type, "quantity": order.quantity,
            "created_at": 1.0,
        }
        exchange._order_initial[oid] = initial
        exchange._order_state[oid] = {"status": status, "filled_quantity": filled, "fill_delta": Decimal("0")}
        resp = exchange._build_order_response(oid, initial)
        exchange._orders_created.append(resp)
        return resp

    exchange.create_order = futures_create

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
        assert len(tp) == 3
        assert len(sl) == 1
        assert sl[0].quantity == Decimal("1.0")
