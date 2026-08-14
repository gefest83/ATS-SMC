from decimal import Decimal

import pytest

from backend.core.exchange.base import Exchange, MarketData, OrderRequest, OrderResponse
from backend.core.order_manager import OrderManager
from backend.core.position_manager import PositionManager
from backend.core.risk.risk_manager import RiskManager


class FakeExchange:
    def __init__(self):
        self.counter = 0
        self.orders = {}
        self.exchange = self
        self._raw_balance = {"free": {"BTC": "1000.0"}, "used": {"BTC": "0.0"}, "total": {"BTC": "1000.0"}}

    def get_exchange_name(self):
        return "binance"

    def fetch_balance(self):
        return self._raw_balance

    def create_order(self, order: OrderRequest):
        self.counter += 1
        oid = f"o{self.counter}"
        response = OrderResponse(
            order_id=oid,
            status="closed" if order.type == "market" else "open",
            filled_quantity=order.quantity if order.type == "market" else Decimal("0"),
            avg_price=order.price or Decimal("100"),
            symbol=order.symbol,
            side=order.side,
            order_type=order.type,
            quantity=order.quantity,
        )
        self.orders[oid] = response
        return response

    def cancel_order(self, symbol, order_id):
        self.orders.pop(order_id, None)
        return True

    def fetch_open_orders(self, symbol=None):
        return list(self.orders.values())

    def fetch_ticker(self, symbol):
        return MarketData(symbol=symbol, timestamp=1, price=Decimal("100"), volume=Decimal("1"))


@pytest.mark.asyncio
async def test_order_manager_creates_position_with_sl_tp():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("1"), order_type="market",
        sl_price=Decimal("95"), tp_prices=[Decimal("105")],
    )
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.sl_price == Decimal("95")
    assert len(position.tp_order_ids) == 1


def test_risk_drawdown_updates_and_blocks():
    risk = RiskManager(1000)
    assert risk.can_open_trade()
    risk.update_equity(940)
    assert risk.check_drawdown()
    assert not risk.can_open_trade()


def test_live_mode_requires_explicit_enable(monkeypatch):
    from pydantic import ValidationError
    from backend.config import Settings

    with pytest.raises(ValidationError):
        Settings(TRADING_MODE="live", EXCHANGE_MODE="testnet", LIVE_TRADING_ENABLED=False)

    with pytest.raises(ValidationError):
        Settings(TRADING_MODE="live", EXCHANGE_MODE="testnet", LIVE_TRADING_ENABLED=True)

    settings = Settings(TRADING_MODE="live", EXCHANGE_MODE="live", LIVE_TRADING_ENABLED=True, API_AUTH_ENABLED=True, API_ACCESS_TOKEN="test-token", BINANCE_API_KEY="key", BINANCE_API_SECRET="secret")
    assert settings.TRADING_MODE == "live"


@pytest.mark.asyncio
async def test_three_take_profits_split_quantity():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("3"), order_type="market",
        sl_price=Decimal("95"),
        tp_prices=[Decimal("105"), Decimal("110"), Decimal("115")],
    )
    tp_orders = [
        order for order in exchange.orders.values()
        if order.order_type == "take_profit"
    ]
    assert len(tp_orders) == 1
    assert sum((o.quantity for o in tp_orders), Decimal("0")) == Decimal("1")
    assert await positions.get_position(position_id) is not None


def test_trigger_order_is_normalized_to_unified_ccxt_params():
    order = OrderRequest(
        symbol="BTC/USDT",
        side="sell",
        type="stop_loss",
        quantity=Decimal("0.01"),
        stopPrice=Decimal("60000"),
    )
    order_type, price, params = Exchange.normalize_trigger_order(order)
    assert order_type == "market"
    assert price is None
    assert params["stopLossPrice"] == "60000"

@pytest.mark.asyncio
async def test_order_status_uses_fetch_order_when_available():
    class StatusExchange(FakeExchange):
        def fetch_order(self, symbol, order_id):
            return self.orders[order_id]

    exchange = StatusExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position("BTC/USDT", "buy", Decimal("1"), order_type="market")
    order_id = next(iter(exchange.orders))
    status = await manager._fetch_order_status(order_id, "BTC/USDT")
    assert status.order_id == order_id
    assert status.status == "closed"
    assert await positions.get_position(position_id) is not None


@pytest.mark.asyncio
async def test_take_profit_closes_only_filled_quantity():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("3"), order_type="market",
        sl_price=Decimal("95"), tp_prices=[Decimal("105"), Decimal("110"), Decimal("115")],
    )
    position = await positions.get_position(position_id)
    tp_id = position.tp_order_ids[0]
    await positions.on_order_update(
        tp_id,
        OrderResponse(order_id=tp_id, status="closed", filled_quantity=Decimal("1"),
                      symbol="BTC/USDT", side="sell", order_type="take_profit", quantity=Decimal("1")),
    )
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.quantity == Decimal("2")
    assert tp_id not in position.tp_order_ids


def test_exchange_order_request_validation():
    from backend.core.exchange.base import Exchange
    with pytest.raises(ValueError):
        Exchange.validate_order_request(OrderRequest(
            symbol="BTC/USDT", side="hold", type="market", quantity=Decimal("1")
        ))
    with pytest.raises(ValueError):
        Exchange.validate_order_request(OrderRequest(
            symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("0")
        ))

@pytest.mark.asyncio
async def test_position_close_cancels_remaining_protective_orders():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("3"), order_type="market",
        sl_price=Decimal("95"),
        tp_prices=[Decimal("105"), Decimal("110"), Decimal("115")],
    )
    position = await positions.get_position(position_id)
    assert position is not None
    protective_ids = [position.sl_order_id, *position.tp_order_ids]
    await manager.close_position(position_id, reason="manual")
    assert await positions.get_position(position_id) is None
    assert all(order_id not in exchange.orders for order_id in protective_ids)


@pytest.mark.asyncio
async def test_order_status_fallback_preserves_last_known_state():
    class BrokenStatusExchange(FakeExchange):
        def fetch_order(self, symbol, order_id):
            raise RuntimeError("temporary API failure")

        def fetch_open_orders(self, symbol=None):
            raise RuntimeError("temporary API failure")

    exchange = BrokenStatusExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    current = OrderResponse(
        order_id="o1", status="open", symbol="BTC/USDT", side="buy",
        order_type="limit", quantity=Decimal("1"), created_at=1.0,
    )
    result = await manager._fetch_order_status("o1", "BTC/USDT", current=current)
    assert result.status == "open"
    assert result.order_id == "o1"

@pytest.mark.asyncio
async def test_strategy_to_order_to_position_pipeline():
    from backend.core.strategy.manager import StrategyManager

    class OneShotStrategy:
        name = "integration"
        parameters = {}
        def __init__(self):
            self.sent = False
        def on_market_data(self, market_data):
            if self.sent:
                return None
            self.sent = True
            return {
                "action": "open",
                "symbol": market_data.symbol,
                "side": "buy",
                "price": Decimal("100"),
                "sl_price": Decimal("95"),
                "tp_prices": [Decimal("105"), Decimal("110"), Decimal("115")],
            }
        def log_signal(self, signal):
            return None

    class Registry:
        strategies = {"integration": OneShotStrategy()}

    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    orders = OrderManager(exchange, positions)
    risk = RiskManager(1000)
    manager = StrategyManager(Registry(), orders, risk)
    await manager.feed_market_data(MarketData(
        symbol="BTC/USDT", timestamp=1, price=Decimal("100"), volume=Decimal("1")
    ))
    assert risk.open_trades == 1
    assert len(positions.positions) == 1
    position = next(iter(positions.positions.values()))
    assert position.quantity > 0
    assert len(position.tp_order_ids) == 1


def test_live_settings_require_api_auth():
    from pydantic import ValidationError
    from backend.config import Settings
    with pytest.raises(ValidationError):
        Settings(TRADING_MODE="live", EXCHANGE_MODE="live", LIVE_TRADING_ENABLED=True, API_AUTH_ENABLED=False, API_ACCESS_TOKEN="x")
    settings = Settings(TRADING_MODE="live", EXCHANGE_MODE="live", LIVE_TRADING_ENABLED=True, API_AUTH_ENABLED=True, API_ACCESS_TOKEN="x", BINANCE_API_KEY="key", BINANCE_API_SECRET="secret")
    assert settings.API_ACCESS_TOKEN == "x"


def test_market_analyzer_short_series_has_finite_atr():
    from backend.core.analysis.market_analyzer import MarketAnalyzer
    candles = [[i, 100, 101, 99, 100, 1] for i in range(5)]
    result = MarketAnalyzer("BTC/USDT", "1m").analyze(candles)
    assert result is not None
    assert result["atr"] >= 0.0

@pytest.mark.asyncio
async def test_partial_take_profit_resizes_remaining_stop_loss():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("3"), order_type="market",
        sl_price=Decimal("95"),
        tp_prices=[Decimal("105"), Decimal("110"), Decimal("115")],
    )
    position = await positions.get_position(position_id)
    old_sl_id = position.sl_order_id
    tp_id = position.tp_order_ids[0]
    await positions.on_order_update(
        tp_id,
        OrderResponse(order_id=tp_id, status="closed", filled_quantity=Decimal("1"),
                      symbol="BTC/USDT", side="sell", order_type="take_profit", quantity=Decimal("1")),
    )
    position = await positions.get_position(position_id)
    assert position.quantity == Decimal("2")
    await manager._resize_stop_loss(position)
    position = await positions.get_position(position_id)
    assert position.sl_order_id != old_sl_id
    assert exchange.orders[position.sl_order_id].quantity == Decimal("2")
    assert old_sl_id not in exchange.orders

@pytest.mark.asyncio
async def test_order_monitor_preserves_original_created_at_for_stuck_timeout():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    old = OrderResponse(
        order_id="o-old", status="open", symbol="BTC/USDT", side="buy",
        order_type="limit", quantity=Decimal("1"), created_at=100.0,
    )
    manager.open_orders[old.order_id] = old
    current = await manager._fetch_order_status(old.order_id, old.symbol, current=old)
    if current.created_at > old.created_at:
        current.created_at = old.created_at
    assert current.created_at == 100.0


@pytest.mark.asyncio
async def test_strategy_manager_does_not_increment_risk_when_open_fails():
    from backend.core.strategy.manager import StrategyManager

    class FailingOrderManager:
        async def open_position(self, **kwargs):
            raise RuntimeError("exchange failure")

    class Registry:
        strategies = {}

    manager = StrategyManager(Registry(), FailingOrderManager(), RiskManager(1000))
    signal = {
        "action": "open", "symbol": "BTC/USDT", "side": "buy",
        "price": Decimal("100"), "sl_price": Decimal("95"),
        "tp_prices": [Decimal("110")], "strategy": "test",
    }
    await manager._handle_signal(signal)
    assert manager.risk_manager.open_trades == 0


def test_smart_money_strategy_does_not_own_separate_risk_manager():
    from backend.core.strategy.strategies.smart_money import Strategy
    strategy = Strategy()
    assert not hasattr(strategy, "risk")

@pytest.mark.asyncio
async def test_partial_take_profit_resizes_stop_order():
    class MonitorExchange(FakeExchange):
        def __init__(self):
            super().__init__()
            self.status_updates = {}

        def fetch_order(self, symbol, order_id):
            return self.status_updates.get(order_id, self.orders[order_id])

    exchange = MonitorExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("3"), order_type="market",
        sl_price=Decimal("95"),
        tp_prices=[Decimal("105"), Decimal("110"), Decimal("115")],
    )
    position = await positions.get_position(position_id)
    old_sl = position.sl_order_id
    tp_id = position.tp_order_ids[0]
    exchange.status_updates[tp_id] = OrderResponse(
        order_id=tp_id, status="closed", filled_quantity=Decimal("1"),
        symbol="BTC/USDT", side="sell", order_type="take_profit", quantity=Decimal("1"),
    )
    await manager._order_monitor_iteration()
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.quantity == Decimal("2")
    assert position.sl_order_id != old_sl
    assert old_sl not in exchange.orders
    assert exchange.orders[position.sl_order_id].quantity == Decimal("2")

@pytest.mark.asyncio
async def test_stop_loss_closes_position_and_cancels_remaining_protection():
    class MonitorExchange(FakeExchange):
        def __init__(self):
            super().__init__()
            self.status_updates = {}

        def fetch_order(self, symbol, order_id):
            return self.status_updates.get(order_id, self.orders[order_id])

    exchange = MonitorExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("3"), order_type="market",
        sl_price=Decimal("95"),
        tp_prices=[Decimal("105"), Decimal("110"), Decimal("115")],
    )
    position = await positions.get_position(position_id)
    sl_id = position.sl_order_id
    tp_ids = list(position.tp_order_ids)
    exchange.status_updates[sl_id] = OrderResponse(
        order_id=sl_id, status="closed", filled_quantity=Decimal("3"),
        symbol="BTC/USDT", side="sell", order_type="stop_loss", quantity=Decimal("3"),
    )
    await manager._order_monitor_iteration()
    assert await positions.get_position(position_id) is None
    assert all(oid not in exchange.orders for oid in tp_ids)


@pytest.mark.asyncio
async def test_sell_paper_lifecycle_realizes_profit_and_closes_risk():
    from backend.core.engine.smc_bot import SMCBot

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
    bot.risk_manager = RiskManager(1000)
    bot._paper_position = {
        "side": "sell",
        "entry": Decimal("100"),
        "sl": Decimal("110"),
        "tp1": Decimal("95"),
        "tp2": Decimal("90"),
        "tp3": Decimal("85"),
        "size": Decimal("3"),
        "remaining": Decimal("3"),
        "tp_qty": Decimal("1"),
        "tp_hit": [False, False, False],
        "unrealized_pnl": Decimal("0"),
    }
    bot.risk_manager.trade_opened()

    await bot._manage_paper_position(Decimal("95"))
    assert bot._paper_position is not None
    assert bot._paper_position["remaining"] == Decimal("2")
    assert bot.risk_manager.current_equity == pytest.approx(1005)
    assert bot.risk_manager.open_trades == 1

    await bot._manage_paper_position(Decimal("90"))
    assert bot._paper_position is not None
    assert bot._paper_position["remaining"] == Decimal("1")
    assert bot.risk_manager.current_equity == pytest.approx(1015)

    await bot._manage_paper_position(Decimal("85"))
    assert bot._paper_position is None
    assert bot.risk_manager.current_equity == pytest.approx(1030)
    assert bot.risk_manager.open_trades == 0


@pytest.mark.asyncio
async def test_sell_paper_stop_loss_realizes_loss_once():
    from backend.core.engine.smc_bot import SMCBot

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
    bot.risk_manager = RiskManager(1000)
    bot._paper_position = {
        "side": "sell",
        "entry": Decimal("100"),
        "sl": Decimal("110"),
        "tp1": Decimal("95"),
        "tp2": Decimal("90"),
        "tp3": Decimal("85"),
        "size": Decimal("3"),
        "remaining": Decimal("3"),
        "tp_qty": Decimal("1"),
        "tp_hit": [False, False, False],
        "unrealized_pnl": Decimal("0"),
    }
    bot.risk_manager.trade_opened()

    await bot._manage_paper_position(Decimal("110"))
    assert bot._paper_position is None
    assert bot.risk_manager.current_equity == pytest.approx(970)
    assert bot.risk_manager.open_trades == 0


@pytest.mark.asyncio
async def test_partial_take_profit_keeps_active_order_until_terminal():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("3"), order_type="market",
        sl_price=Decimal("95"), tp_prices=[Decimal("105"), Decimal("110"), Decimal("115")],
    )
    position = await positions.get_position(position_id)
    tp_id = position.tp_order_ids[0]
    await positions.on_order_update(
        tp_id,
        OrderResponse(order_id=tp_id, status="open", filled_quantity=Decimal("0.5"),
                      fill_delta=Decimal("0.5"), symbol="BTC/USDT", side="sell",
                      order_type="take_profit", quantity=Decimal("1")),
    )
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.quantity == Decimal("2.5")
    assert tp_id in position.tp_order_ids

    await positions.on_order_update(
        tp_id,
        OrderResponse(order_id=tp_id, status="closed", filled_quantity=Decimal("1"),
                      fill_delta=Decimal("0.5"), symbol="BTC/USDT", side="sell",
                      order_type="take_profit", quantity=Decimal("1")),
    )
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.quantity == Decimal("2")
    assert tp_id not in position.tp_order_ids

@pytest.mark.asyncio
async def test_realized_pnl_is_net_of_quote_fees_for_long():
    from backend.core.position_manager import PositionManager
    from backend.core.exchange.base import OrderResponse
    pm = PositionManager(exchange_name="binance")
    position_id = await pm.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("2"),
        entry_price=Decimal("100"), strategy="test"
    )
    await pm.record_fill(position_id, OrderResponse(
        order_id="entry", status="closed", symbol="BTC/USDT", side="buy",
        order_type="market", quantity=Decimal("2"), filled_quantity=Decimal("2"),
        fill_delta=Decimal("2"), avg_price=Decimal("100"), fee_cost=Decimal("0.20"), fee_currency="USDT"
    ), "entry")
    await pm.record_fill(position_id, OrderResponse(
        order_id="exit", status="closed", symbol="BTC/USDT", side="sell",
        order_type="market", quantity=Decimal("2"), filled_quantity=Decimal("2"),
        fill_delta=Decimal("2"), avg_price=Decimal("110"), fee_cost=Decimal("0.22"), fee_currency="USDT"
    ), "exit")
    position = await pm.get_position(position_id)
    assert position is not None
    assert pm.realized_pnl(position) == Decimal("19.58")


@pytest.mark.asyncio
async def test_realized_pnl_sell_uses_opposite_price_delta():
    from backend.core.position_manager import PositionManager
    from backend.core.exchange.base import OrderResponse
    pm = PositionManager(exchange_name="binance")
    position_id = await pm.create_position(
        symbol="BTC/USDT", side="sell", quantity=Decimal("1"),
        entry_price=Decimal("100"), strategy="test"
    )
    await pm.record_fill(position_id, OrderResponse(
        order_id="entry", status="closed", symbol="BTC/USDT", side="sell",
        order_type="market", quantity=Decimal("1"), filled_quantity=Decimal("1"),
        fill_delta=Decimal("1"), avg_price=Decimal("100"), fee_cost=Decimal("0.10"), fee_currency="USDT"
    ), "entry")
    await pm.record_fill(position_id, OrderResponse(
        order_id="exit", status="closed", symbol="BTC/USDT", side="buy",
        order_type="market", quantity=Decimal("1"), filled_quantity=Decimal("1"),
        fill_delta=Decimal("1"), avg_price=Decimal("90"), fee_cost=Decimal("0.09"), fee_currency="USDT"
    ), "exit")
    position = await pm.get_position(position_id)
    assert pm.realized_pnl(position) == Decimal("9.81")


@pytest.mark.asyncio
async def test_non_quote_fee_is_not_silently_subtracted_from_quote_pnl():
    from backend.core.position_manager import PositionManager
    from backend.core.exchange.base import OrderResponse
    pm = PositionManager(exchange_name="binance")
    position_id = await pm.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("1"),
        entry_price=Decimal("100"), strategy="test"
    )
    await pm.record_fill(position_id, OrderResponse(
        order_id="entry", status="closed", symbol="BTC/USDT", side="buy",
        order_type="market", quantity=Decimal("1"), filled_quantity=Decimal("1"),
        fill_delta=Decimal("1"), avg_price=Decimal("100"), fee_cost=Decimal("0.001"), fee_currency="BNB"
    ), "entry")
    await pm.record_fill(position_id, OrderResponse(
        order_id="exit", status="closed", symbol="BTC/USDT", side="sell",
        order_type="market", quantity=Decimal("1"), filled_quantity=Decimal("1"),
        fill_delta=Decimal("1"), avg_price=Decimal("110"), fee_cost=Decimal("0.001"), fee_currency="BNB"
    ), "exit")
    position = await pm.get_position(position_id)
    assert pm.realized_pnl(position) == Decimal("10")
    assert position.fee_unconverted == Decimal("0.002")

@pytest.mark.asyncio
async def test_partial_stop_loss_reduces_position_before_terminal_close():
    from backend.core.position_manager import PositionManager
    from backend.core.exchange.base import OrderResponse
    pm = PositionManager(exchange_name="binance")
    position_id = await pm.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("2"),
        entry_price=Decimal("100"), sl_price=Decimal("95"), strategy="test"
    )
    position = await pm.get_position(position_id)
    position.sl_order_id = "sl-1"
    await pm.record_fill(position_id, OrderResponse(
        order_id="entry", status="closed", symbol="BTC/USDT", side="buy",
        order_type="market", quantity=Decimal("2"), filled_quantity=Decimal("2"),
        fill_delta=Decimal("2"), avg_price=Decimal("100"), fee_cost=Decimal("0"), fee_currency="USDT"
    ), "entry")
    await pm.on_order_update("sl-1", OrderResponse(
        order_id="sl-1", status="open", symbol="BTC/USDT", side="sell",
        order_type="stop_loss", quantity=Decimal("2"), filled_quantity=Decimal("1"),
        fill_delta=Decimal("1"), avg_price=Decimal("95"), fee_cost=Decimal("0"), fee_currency="USDT"
    ))
    position = await pm.get_position(position_id)
    assert position is not None
    assert position.quantity == Decimal("1")
    await pm.on_order_update("sl-1", OrderResponse(
        order_id="sl-1", status="closed", symbol="BTC/USDT", side="sell",
        order_type="stop_loss", quantity=Decimal("2"), filled_quantity=Decimal("2"),
        fill_delta=Decimal("1"), avg_price=Decimal("95"), fee_cost=Decimal("0"), fee_currency="USDT"
    ))
    assert await pm.get_position(position_id) is None

@pytest.mark.asyncio
async def test_cumulative_fill_is_idempotent_across_repeated_updates():
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    position_id = await positions.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("3"),
        entry_price=Decimal("100"), tp_prices=[Decimal("105")],
    )
    tp_id = "tp-1"
    position = await positions.get_position(position_id)
    position.tp_order_ids = [tp_id]

    await positions.on_order_update(
        tp_id,
        OrderResponse(order_id=tp_id, status="open", filled_quantity=Decimal("1"),
                      fill_delta=Decimal("1"), avg_price=Decimal("105"),
                      symbol="BTC/USDT", side="sell", order_type="take_profit", quantity=Decimal("1")),
    )
    assert position.quantity == Decimal("2")

    # Same cumulative CCXT `filled=1` arrives again. It must not reduce the
    # position a second time.
    await positions.on_order_update(
        tp_id,
        OrderResponse(order_id=tp_id, status="closed", filled_quantity=Decimal("1"),
                      fill_delta=Decimal("0"), avg_price=Decimal("105"),
                      symbol="BTC/USDT", side="sell", order_type="take_profit", quantity=Decimal("1")),
    )
    assert position.quantity == Decimal("2")
    assert tp_id not in position.tp_order_ids


@pytest.mark.asyncio
async def test_terminal_order_without_new_fill_does_not_close_position():
    positions = PositionManager(exchange_name="binance")
    position_id = await positions.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("2"),
        entry_price=Decimal("100"), tp_prices=[Decimal("105")],
    )
    position = await positions.get_position(position_id)
    tp_id = "tp-cancelled"
    position.tp_order_ids = [tp_id]

    await positions.on_order_update(
        tp_id,
        OrderResponse(order_id=tp_id, status="canceled", filled_quantity=Decimal("0"),
                      fill_delta=Decimal("0"), symbol="BTC/USDT", side="sell",
                      order_type="take_profit", quantity=Decimal("1")),
    )
    assert await positions.get_position(position_id) is not None
    assert position.quantity == Decimal("2")
    assert tp_id not in position.tp_order_ids


@pytest.mark.asyncio
async def test_testnet_reconciliation_runs_for_futures(monkeypatch):
    class TestnetExchange(FakeExchange):
        def fetch_positions(self, symbol=None):
            return [
                type("Remote", (), {
                    "symbol": "BTC/USDT:USDT",
                    "side": "buy",
                    "quantity": Decimal("1"),
                    "unrealized_pnl": Decimal("2.5"),
                })()
            ]
        def get_exchange_name(self):
            return "binance"

    exchange = TestnetExchange()
    positions = PositionManager(exchange=exchange, exchange_name="binance")
    position_id = await positions.create_position(
        symbol="BTC/USDT:USDT", side="buy", quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )
    monkeypatch.setattr("backend.core.position_manager.settings.TRADING_MODE", "testnet")
    monkeypatch.setattr("backend.core.position_manager.settings.EXCHANGE_MARKET_TYPE", "futures")
    await positions.sync_with_exchange()
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.reconciliation_pending is False


@pytest.mark.asyncio
async def test_reconciliation_does_not_fabricate_close_when_remote_position_missing(monkeypatch):
    class MissingPositionExchange(FakeExchange):
        def fetch_positions(self, symbol=None):
            return []
        def get_exchange_name(self):
            return "binance"

    exchange = MissingPositionExchange()
    positions = PositionManager(exchange=exchange, exchange_name="binance")
    position_id = await positions.create_position(
        symbol="BTC/USDT:USDT", side="buy", quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )
    monkeypatch.setattr("backend.core.position_manager.settings.TRADING_MODE", "live")
    monkeypatch.setattr("backend.core.position_manager.settings.EXCHANGE_MARKET_TYPE", "futures")
    await positions.sync_with_exchange()
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.reconciliation_pending is True
    assert "missing" in position.reconciliation_message


def test_trade_history_reconciliation_closes_position_after_manual_exit():
    import asyncio
    from decimal import Decimal
    from backend.core.position_manager import PositionManager
    from backend.core.exchange.base import Exchange

    class DummyExchange(Exchange):
        def __init__(self, credentials=None, sandbox=False):
            self.exchange = self
        def fetch_balance(self): return {}
        def fetch_ticker(self, symbol): return None
        def fetch_ohlcv(self, symbol, timeframe="15m", limit=200): return []
        def fetch_order_book(self, symbol, limit=20): return []
        def create_order(self, order): return None
        def fetch_order(self, symbol, order_id): return None
        def cancel_order(self, symbol, order_id): return True
        def fetch_open_orders(self, symbol=None): return []
        def fetch_positions(self, symbol=None): return []
        def get_wallet_balance(self, asset): return Decimal("0")
        def get_exchange_name(self): return "dummy"
        def fetch_my_trades(self, symbol=None, since=None, limit=None):
            return [
                {"id": "entry", "symbol": symbol, "timestamp": 1000 * 1000, "side": "buy", "amount": 1, "price": 100,
                 "fee": {"cost": 0.1, "currency": "USDT"}},
                {"id": "manual-exit", "symbol": symbol, "timestamp": 1000 * 1000 + 1000, "side": "sell", "amount": 1, "price": 110,
                 "fee": {"cost": 0.11, "currency": "USDT"}},
            ]

    async def run():
        manager = PositionManager(exchange=DummyExchange(), exchange_name="dummy")
        pid = await manager.create_position(
            symbol="BTC/USDT", side="buy", quantity=Decimal("1"), entry_price=Decimal("100")
        )
        pos = await manager.get_position(pid)
        pos.entry_time = 1000
        resolved = await manager._reconcile_from_trade_history(pos)
        assert resolved is True
        assert pid not in manager.positions

    asyncio.run(run())


def test_executor_manager_routes_testnet_to_exchange_executor(monkeypatch):
    from backend.core.execution.executor import ExecutorManager, LiveExecutor, PaperExecutor
    from backend.config import settings

    class DummyExchange:
        pass

    original = settings.TRADING_MODE
    try:
        settings.TRADING_MODE = "testnet"
        manager = ExecutorManager(DummyExchange())
        assert isinstance(manager.get_executor(), LiveExecutor)
    finally:
        settings.TRADING_MODE = original


def test_executor_manager_keeps_paper_isolated(monkeypatch):
    from backend.core.execution.executor import ExecutorManager, PaperExecutor
    from backend.config import settings

    original = settings.TRADING_MODE
    try:
        settings.TRADING_MODE = "paper"
        manager = ExecutorManager(None)
        assert isinstance(manager.get_executor(), PaperExecutor)
    finally:
        settings.TRADING_MODE = original


@pytest.mark.asyncio
async def test_trade_history_reconciliation_aggregates_multiple_fees_and_unconverted_fees():
    from backend.core.position_manager import PositionManager

    class FeeExchange:
        def fetch_my_trades(self, symbol, since, limit):
            return [
                {
                    "id": "entry",
                    "symbol": symbol,
                    "timestamp": 1000 * 1000,
                    "side": "buy",
                    "amount": 1,
                    "price": 100,
                    "fees": [
                        {"cost": "0.10", "currency": "USDT"},
                        {"cost": "0.001", "currency": "BNB"},
                    ],
                },
                {
                    "id": "exit",
                    "symbol": symbol,
                    "timestamp": 1000 * 1000 + 1000,
                    "side": "sell",
                    "amount": 1,
                    "price": 110,
                    "fees": [
                        {"cost": "0.11", "currency": "USDT"},
                        {"cost": "0.002", "currency": "BNB"},
                    ],
                },
            ]

    pm = PositionManager(exchange=FeeExchange(), exchange_name="binance")
    pid = await pm.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("1"),
        entry_price=Decimal("100"), strategy="test"
    )
    pos = await pm.get_position(pid)
    pos.entry_time = 1000

    assert await pm._reconcile_from_trade_history(pos) is True
    assert pos.entry_fee_quote == Decimal("0.10")
    assert pos.exit_fee_quote == Decimal("0.11")
    assert pos.fee_unconverted == Decimal("0.003")
    assert pos.fee_currencies == {"USDT", "BNB"}
    assert pm.realized_pnl(pos) == Decimal("9.79")

@pytest.mark.asyncio
async def test_trade_history_reconciliation_ignores_fills_before_position_entry_and_treats_missing_fee_currency_as_quote():
    from backend.core.position_manager import PositionManager

    class FeeExchange:
        def fetch_my_trades(self, symbol, since, limit):
            return [
                {
                    "id": "old",
                    "symbol": symbol,
                    "timestamp": since,
                    "side": "buy",
                    "amount": 99,
                    "price": 1,
                    "fee": {"cost": "9"},
                },
                {
                    "id": "entry",
                    "symbol": symbol,
                    "timestamp": 1000 * 1000,
                    "side": "buy",
                    "amount": 1,
                    "price": 100,
                    "fee": {"cost": "0.10"},
                },
                {
                    "id": "exit",
                    "symbol": symbol,
                    "timestamp": 1000 * 1000 + 1000,
                    "side": "sell",
                    "amount": 1,
                    "price": 110,
                    "fee": {"cost": "0.11"},
                },
            ]

        def normalize_symbol(self, symbol):
            return symbol

    pm = PositionManager(exchange=FeeExchange(), exchange_name="binance")
    pid = await pm.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("1"),
        entry_price=Decimal("100"), strategy="test"
    )
    pos = await pm.get_position(pid)
    pos.entry_time = 1000

    assert await pm._reconcile_from_trade_history(pos) is True
    assert pos.initial_quantity == Decimal("1")
    assert pos.entry_cost == Decimal("100")
    assert pos.entry_fee_quote == Decimal("0.10")
    assert pos.exit_fee_quote == Decimal("0.11")
    assert pos.fee_unconverted == Decimal("0")


@pytest.mark.asyncio
async def test_close_position_is_idempotent_under_concurrent_calls():
    import asyncio

    exchange = FakeExchange()
    callbacks = []

    async def on_closed(position, reason):
        callbacks.append((position.position_id, reason))

    positions = PositionManager(
        exchange_name="binance",
        on_position_closed=on_closed,
    )
    position_id = await positions.create_position(
        symbol="BTC/USDT",
        side="buy",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )

    await asyncio.gather(
        positions.close_position(position_id, "take_profit"),
        positions.close_position(position_id, "take_profit"),
    )

    assert callbacks == [(position_id, "take_profit")]
    assert await positions.get_position(position_id) is None


@pytest.mark.asyncio
async def test_entry_uses_actual_fill_quantity_before_protection():
    class PartialEntryExchange(FakeExchange):
        def create_order(self, order: OrderRequest):
            self.counter += 1
            oid = f"o{self.counter}"
            if order.type == "market":
                filled = Decimal("0.7")
                response = OrderResponse(
                    order_id=oid, status="closed", filled_quantity=filled,
                    avg_price=Decimal("100"), symbol=order.symbol, side=order.side,
                    order_type=order.type, quantity=order.quantity,
                )
            else:
                response = OrderResponse(
                    order_id=oid, status="open", filled_quantity=Decimal("0"),
                    avg_price=order.price or Decimal("100"), symbol=order.symbol,
                    side=order.side, order_type=order.type, quantity=order.quantity,
                )
            self.orders[oid] = response
            return response

    exchange = PartialEntryExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await manager.open_position(
        "BTC/USDT", "buy", Decimal("1"), order_type="market",
        sl_price=Decimal("95"), tp_prices=[Decimal("105"), Decimal("110")]
    )
    position = await positions.get_position(position_id)
    assert position is not None
    assert position.quantity == Decimal("0.7")
    protective = [exchange.orders[position.sl_order_id], *(exchange.orders[x] for x in position.tp_order_ids)]
    assert protective[0].quantity == Decimal("0.7")
    assert sum((o.quantity for o in protective[1:]), Decimal("0")) == Decimal("0.35")


@pytest.mark.asyncio
async def test_position_exists_before_protective_order_submission():
    class OrderingExchange(FakeExchange):
        def __init__(self, positions):
            super().__init__()
            self.positions = positions
            self.saw_position_on_protection = False

        def create_order(self, order: OrderRequest):
            if order.type in {"stop_loss", "take_profit"} and self.positions.positions:
                self.saw_position_on_protection = True
            return super().create_order(order)

    positions = PositionManager(exchange_name="binance")
    exchange = OrderingExchange(positions)
    manager = OrderManager(exchange, positions)
    await manager.open_position(
        "BTC/USDT", "buy", Decimal("1"), order_type="market",
        sl_price=Decimal("95"), tp_prices=[Decimal("105")]
    )
    assert exchange.saw_position_on_protection

@pytest.mark.asyncio
async def test_protective_recovery_finds_existing_orders_by_client_id_without_duplicates():
    class RecoverableExchange(FakeExchange):
        def find_order_by_client_id(self, symbol, client_order_id):
            for order in self.orders.values():
                if getattr(order, "client_order_id", None) == client_order_id:
                    return order
            return None

    exchange = RecoverableExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await positions.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("2"),
        entry_price=Decimal("100"), sl_price=Decimal("95"),
        tp_prices=[Decimal("105"), Decimal("110")],
        position_id=str(__import__('uuid').uuid4()),
    )
    position = await positions.get_position(position_id)
    await manager._ensure_position_protection(position)
    first_count = len(exchange.orders)
    await manager._ensure_position_protection(position)
    assert len(exchange.orders) == first_count
    assert position.sl_order_id is not None
    assert len(position.tp_order_ids) == 1


@pytest.mark.asyncio
async def test_protective_recovery_recreates_missing_stop_with_stable_client_id():
    class RecoverableExchange(FakeExchange):
        def find_order_by_client_id(self, symbol, client_order_id):
            for order in self.orders.values():
                if getattr(order, "client_order_id", None) == client_order_id:
                    return order
            return None

    exchange = RecoverableExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    position_id = await positions.create_position(
        symbol="BTC/USDT", side="buy", quantity=Decimal("1"),
        entry_price=Decimal("100"), sl_price=Decimal("95"),
        tp_prices=[], position_id=str(__import__('uuid').uuid4()),
    )
    position = await positions.get_position(position_id)
    await manager._ensure_position_protection(position)
    first_id = position.sl_order_id
    client_id = position.protective_client_ids["sl"]
    exchange.orders.pop(first_id)
    position.sl_order_id = None
    await manager._ensure_position_protection(position)
    assert position.sl_order_id is not None
    assert position.protective_client_ids["sl"] == client_id
    assert position.sl_order_id != first_id


@pytest.mark.asyncio
async def test_recovery_recreates_stale_stop_and_splits_missing_tps_by_current_quantity():
    class RecoveryExchange(FakeExchange):
        def __init__(self):
            super().__init__()
            self.find_calls = []

        def find_order_by_client_id(self, symbol, client_order_id):
            self.find_calls.append((symbol, client_order_id))
            return None

    exchange = RecoveryExchange()
    positions = PositionManager(exchange_name="binance")
    position_id = await positions.create_position(
        symbol="BTC/USDT",
        side="buy",
        quantity=Decimal("0.7"),
        entry_price=Decimal("100"),
        sl_price=Decimal("95"),
        tp_prices=[Decimal("105"), Decimal("110")],
        entry_order_id="entry",
        sl_order_id="stale-sl",
        tp_order_ids=["stale-tp0"],
    )
    pos = await positions.get_position(position_id)
    pos.protective_client_ids = {
        "sl": "ats" + position_id.replace("-", "")[:20] + "sl0",
        "tp0": "ats" + position_id.replace("-", "")[:20] + "tp0",
    }

    manager = OrderManager(exchange, positions)
    await manager._ensure_position_protection(pos)

    assert pos.sl_order_id not in {"stale-sl", None}
    tp_orders = [exchange.orders[oid] for oid in pos.tp_order_ids]
    assert len(tp_orders) == 1
    assert sum((o.quantity for o in tp_orders), Decimal("0")) == Decimal("0.7")


@pytest.mark.asyncio
async def test_recovery_does_not_leave_stale_tp_ids_when_exchange_order_missing():
    class RecoveryExchange(FakeExchange):
        def find_order_by_client_id(self, symbol, client_order_id):
            return None

    exchange = RecoveryExchange()
    positions = PositionManager(exchange_name="binance")
    position_id = await positions.create_position(
        symbol="BTC/USDT",
        side="buy",
        quantity=Decimal("0.6"),
        entry_price=Decimal("100"),
        tp_prices=[Decimal("105"), Decimal("110")],
        tp_order_ids=["stale-tp0"],
    )
    pos = await positions.get_position(position_id)
    pos.protective_client_ids = {
        "tp0": "ats" + position_id.replace("-", "")[:20] + "tp0",
    }
    manager = OrderManager(exchange, positions)
    await manager._ensure_position_protection(pos)

    assert "stale-tp0" not in pos.tp_order_ids
    assert len(pos.tp_order_ids) == 1
    assert sum((exchange.orders[oid].quantity for oid in pos.tp_order_ids), Decimal("0")) == Decimal("0.6")


def test_risk_position_size_respects_margin_cap():
    risk = RiskManager(1000)
    # 1% risk with a 1-point stop would produce 10 units, but a 2x
    # leveraged 50% equity notional cap allows only 1 unit at price 1000.
    assert risk.calculate_position_size(1000, 999, leverage=2, max_notional_pct=0.5) == pytest.approx(1.0)


def test_risk_rejects_invalid_settings(monkeypatch):
    from backend.config import Settings
    with pytest.raises(ValueError):
        Settings(RISK_PER_TRADE_PCT=0)
    with pytest.raises(ValueError):
        Settings(MAX_DAILY_DRAWDOWN_PCT=1.1)
    with pytest.raises(ValueError):
        Settings(MAX_OPEN_TRADES=0)


@pytest.mark.asyncio
async def test_close_result_keeps_unfilled_position_and_risk_count():
    class UnfilledCloseExchange(FakeExchange):
        def create_order(self, order: OrderRequest):
            self.counter += 1
            oid = f"o{self.counter}"
            if order.type == "market" and self.counter > 1:
                response = OrderResponse(
                    order_id=oid, status="rejected", filled_quantity=Decimal("0"),
                    avg_price=Decimal("110"), symbol=order.symbol, side=order.side,
                    order_type=order.type, quantity=order.quantity,
                )
                self.orders[oid] = response
                return response
            return super().create_order(order)

    exchange = UnfilledCloseExchange()
    positions = PositionManager(exchange_name="binance")
    orders = OrderManager(exchange, positions)
    position_id = await orders.open_position(
        "BTC/USDT", "buy", Decimal("1"), order_type="market",
        sl_price=Decimal("95"), tp_prices=[Decimal("110")],
    )
    result = await orders.close_position(position_id)

    assert result.fully_closed is False
    assert result.requested_quantity == Decimal("1")
    assert result.filled_quantity == Decimal("0")
    assert result.remaining_quantity == Decimal("1")
    assert await positions.get_position(position_id) is not None


@pytest.mark.asyncio
async def test_strategy_close_decrements_risk_only_after_confirmed_full_fill():
    from backend.core.strategy.manager import StrategyManager

    class PartialCloseExchange(FakeExchange):
        def create_order(self, order: OrderRequest):
            self.counter += 1
            oid = f"o{self.counter}"
            if order.type == "market" and self.counter > 1:
                filled = order.quantity / Decimal("2")
                response = OrderResponse(
                    order_id=oid, status="closed", filled_quantity=filled,
                    avg_price=Decimal("110"), symbol=order.symbol, side=order.side,
                    order_type=order.type, quantity=order.quantity,
                )
                self.orders[oid] = response
                return response
            return super().create_order(order)

    class Registry:
        strategies = {}

    exchange = PartialCloseExchange()
    positions = PositionManager(exchange_name="binance")
    orders = OrderManager(exchange, positions)
    position_id = await orders.open_position("BTC/USDT", "buy", Decimal("1"), order_type="market")
    risk = RiskManager(1000)
    risk.trade_opened()
    manager = StrategyManager(Registry(), orders, risk)

    await manager._handle_signal({
        "action": "close", "position_id": position_id,
        "percentage": Decimal("1"), "reason": "strategy",
    })

    position = await positions.get_position(position_id)
    assert position is not None
    assert position.quantity == Decimal("0.5")
    assert risk.open_trades == 1
    assert risk.current_equity == pytest.approx(1000)


@pytest.mark.asyncio
async def test_confirmed_full_close_applies_realized_pnl_once():
    from backend.core.strategy.manager import StrategyManager

    class FullCloseExchange(FakeExchange):
        def create_order(self, order: OrderRequest):
            self.counter += 1
            oid = f"o{self.counter}"
            if order.type == "market" and self.counter > 1:
                response = OrderResponse(
                    order_id=oid, status="closed", filled_quantity=order.quantity,
                    avg_price=Decimal("110"), symbol=order.symbol, side=order.side,
                    order_type=order.type, quantity=order.quantity,
                )
                self.orders[oid] = response
                return response
            return super().create_order(order)

    class Registry:
        strategies = {}

    exchange = FullCloseExchange()
    positions = PositionManager(exchange_name="binance")
    orders = OrderManager(exchange, positions)
    position_id = await orders.open_position("BTC/USDT", "buy", Decimal("1"), order_type="market")
    risk = RiskManager(1000)
    risk.trade_opened()
    manager = StrategyManager(Registry(), orders, risk)

    await manager._handle_signal({
        "action": "close", "position_id": position_id,
        "percentage": Decimal("1"), "reason": "strategy",
    })
    # A duplicate callback/event must not decrement or apply PnL again.
    risk.trade_closed(Decimal("10"), event_id=position_id)

    assert await positions.get_position(position_id) is None
    assert risk.open_trades == 0
    assert risk.current_equity == pytest.approx(1010)


@pytest.mark.asyncio
async def test_close_callback_is_idempotent_for_risk_and_pnl():
    from backend.core.engine.smc_bot import SMCBot

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

    risk = RiskManager(1000)
    risk.trade_opened()
    bot = SMCBot.__new__(SMCBot)
    bot.risk_manager = risk
    bot.notifier = DummyNotifier()
    bot._persist_runtime_state = lambda: _completed_awaitable()
    position = type("Position", (), {
        "position_id": "position-close-once",
        "symbol": "BTC/USDT",
        "side": "buy",
        "initial_quantity": Decimal("1"),
        "entry_price": Decimal("100"),
        "entry_time": 1000000.0,
        "close_time": 1001000.0,
        "exit_notional": Decimal("110"),
        "exit_quantity": Decimal("1"),
        "quantity": Decimal("1"),
    })()
    bot.position_manager = type("PositionManager", (), {
        "realized_pnl": lambda self, pos: Decimal("10"),
    })()

    await bot._on_live_position_closed(position, "take_profit")
    await bot._on_live_position_closed(position, "take_profit")

    assert risk.open_trades == 0
    assert risk.current_equity == pytest.approx(1010)


async def _completed_awaitable():
    return None


@pytest.mark.asyncio
async def test_cleanup_stuck_orders_uses_timeout_boundary(monkeypatch):
    exchange = FakeExchange()
    positions = PositionManager(exchange_name="binance")
    manager = OrderManager(exchange, positions)
    timeout = 300
    now = 10_000.0
    monkeypatch.setattr("backend.core.order_manager.settings.STUCK_ORDER_TIMEOUT_SECONDS", timeout)
    monkeypatch.setattr("backend.core.order_manager.time.time", lambda: now)

    exact = OrderResponse(
        order_id="exact", status="open", symbol="BTC/USDT", side="buy",
        order_type="limit", quantity=Decimal("1"), created_at=now - timeout,
    )
    recent = OrderResponse(
        order_id="recent", status="partial", symbol="BTC/USDT", side="buy",
        order_type="limit", quantity=Decimal("1"), created_at=now - timeout + 1,
    )
    unknown_age = OrderResponse(
        order_id="unknown", status="open", symbol="BTC/USDT", side="buy",
        order_type="limit", quantity=Decimal("1"), created_at=0,
    )
    terminal = OrderResponse(
        order_id="terminal", status="rejected", symbol="BTC/USDT", side="buy",
        order_type="limit", quantity=Decimal("1"), created_at=now - timeout * 2,
    )
    manager.open_orders = {
        order.order_id: order
        for order in (exact, recent, unknown_age, terminal)
    }

    await manager._cleanup_stuck_orders()

    assert "exact" not in manager.open_orders
    assert "recent" in manager.open_orders
    assert "unknown" in manager.open_orders
    assert "terminal" in manager.open_orders
    assert "exact" not in exchange.orders


@pytest.mark.asyncio
async def test_close_result_reports_partial_fill_without_full_close():
    class PartialResponseExchange(FakeExchange):
        def create_order(self, order: OrderRequest):
            self.counter += 1
            oid = f"o{self.counter}"
            if order.type == "market" and self.counter > 1:
                response = OrderResponse(
                    order_id=oid, status="open", filled_quantity=Decimal("0.25"),
                    fill_delta=Decimal("0.25"), avg_price=Decimal("105"),
                    symbol=order.symbol, side=order.side, order_type=order.type,
                    quantity=order.quantity,
                )
                self.orders[oid] = response
                return response
            return super().create_order(order)

    exchange = PartialResponseExchange()
    positions = PositionManager(exchange_name="binance")
    orders = OrderManager(exchange, positions)
    position_id = await orders.open_position("BTC/USDT", "buy", Decimal("1"), order_type="market")
    result = await orders.close_position(position_id)

    assert result.filled_quantity == Decimal("0.25")
    assert result.remaining_quantity == Decimal("0.75")
    assert result.fully_closed is False
    assert (await positions.get_position(position_id)).quantity == Decimal("0.75")
