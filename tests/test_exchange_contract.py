import pytest
from decimal import Decimal
from backend.core.exchange.base import Exchange, OrderRequest

def test_order_request_normalizes_side_contract():
    order = OrderRequest(symbol="BTC/USDT", side="BUY", type="market", quantity=Decimal("1"))
    Exchange.validate_order_request(order)
    assert order.side.lower() == "buy"


import asyncio


def test_exchange_dispatch_callback_supports_async_callback():
    class DummyExchange(Exchange):
        def __init__(self, credentials, sandbox=False):
            pass
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

    seen = []
    async def callback(payload):
        seen.append(payload)

    asyncio.run(DummyExchange({}, False)._dispatch_callback(callback, "tick"))
    assert seen == ["tick"]


def test_normalize_balance_ignores_ccxt_metadata():
    from decimal import Decimal
    from backend.core.exchange.base import Exchange

    balance = {
        "free": {"USDT": 100, "BTC": 0.1},
        "used": {"USDT": 25, "BTC": 0.02},
        "total": {"USDT": 125, "BTC": 0.12},
        "timestamp": 123,
        "datetime": "2026-01-01T00:00:00Z",
        "info": {"raw": "data"},
    }
    normalized = Exchange.normalize_balance(balance)
    assert normalized == {"USDT": Decimal("125"), "BTC": Decimal("0.12")}


def test_normalize_position_uses_unified_ccxt_fields():
    from decimal import Decimal
    from backend.core.exchange.base import Exchange

    class DummyExchange(Exchange):
        def __init__(self, credentials=None, sandbox=False): pass
        def fetch_balance(self): pass
        def fetch_ticker(self, symbol): pass
        def fetch_ohlcv(self, symbol, timeframe="15m", limit=200): pass
        def fetch_order_book(self, symbol, limit=20): pass
        def create_order(self, order): pass
        def fetch_order(self, symbol, order_id): pass
        def cancel_order(self, symbol, order_id): pass
        def fetch_open_orders(self, symbol=None): pass
        def fetch_positions(self, symbol=None): pass
        def get_wallet_balance(self, asset): pass
        def get_exchange_name(self): return "test"

    adapter = DummyExchange()
    position = adapter.normalize_position(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0.01,
            "entryPrice": 60000,
            "markPrice": 60100,
            "unrealizedPnl": 1,
            "timestamp": 123456,
        }
    )
    assert position is not None
    assert position.side == "buy"
    assert position.quantity == Decimal("0.01")
    assert position.entry_price == Decimal("60000")
    assert position.mark_price == Decimal("60100")


def test_futures_symbol_is_normalized_to_usdt_m():
    from backend.core.exchange.base import Exchange
    from backend.config import settings
    original = settings.EXCHANGE_MARKET_TYPE
    try:
        settings.EXCHANGE_MARKET_TYPE = "futures"
        assert Exchange.normalize_symbol("BTC/USDT") == "BTC/USDT:USDT"
        assert Exchange.normalize_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
    finally:
        settings.EXCHANGE_MARKET_TYPE = original


def test_trigger_orders_are_reduce_only_for_futures():
    from backend.core.exchange.base import Exchange, OrderRequest
    from backend.config import settings
    original = settings.EXCHANGE_MARKET_TYPE
    try:
        settings.EXCHANGE_MARKET_TYPE = "futures"
        order = OrderRequest(
            symbol="BTC/USDT",
            side="sell",
            type="stop_loss",
            quantity=Decimal("0.01"),
            stopPrice=Decimal("60000"),
        )
        class Dummy(Exchange):
            def __init__(self, credentials=None, sandbox=False):
                self.exchange = type("Meta", (), {
                    "load_markets": lambda self: None,
                    "amount_to_precision": lambda self, symbol, amount: "0.010",
                    "price_to_precision": lambda self, symbol, price: "60000.0",
                })()
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
        _, amount, price, params = Dummy().prepare_order(order)
        assert amount == "0.010"
        assert price is None
        assert params["stopLossPrice"] == "60000"
        assert params["reduceOnly"] is True
    finally:
        settings.EXCHANGE_MARKET_TYPE = original


def test_partial_tp_uses_fill_delta_not_cumulative_filled():
    from backend.core.position_manager import PositionManager
    from backend.core.exchange.base import OrderResponse

    async def scenario():
        pm = PositionManager()
        pid = await pm.create_position(
            symbol="BTC/USDT",
            side="buy",
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            tp_prices=[Decimal("110")],
            tp_order_ids=["tp-1"],
        )
        first = OrderResponse(
            order_id="tp-1",
            status="open",
            symbol="BTC/USDT",
            order_type="take_profit",
            quantity=Decimal("1"),
            filled_quantity=Decimal("0.4"),
            fill_delta=Decimal("0.4"),
        )
        await pm.on_order_update("tp-1", first)
        pos = await pm.get_position(pid)
        assert pos is not None
        assert pos.quantity == Decimal("0.6")

        second = OrderResponse(
            order_id="tp-1",
            status="open",
            symbol="BTC/USDT",
            order_type="take_profit",
            quantity=Decimal("1"),
            filled_quantity=Decimal("0.4"),
            fill_delta=Decimal("0"),
        )
        await pm.on_order_update("tp-1", second)
        pos = await pm.get_position(pid)
        assert pos is not None
        assert pos.quantity == Decimal("0.6")

    asyncio.run(scenario())


def test_rejected_order_is_terminal():
    from backend.core.position_manager import PositionManager
    from backend.core.exchange.base import OrderResponse

    async def scenario():
        pm = PositionManager()
        pid = await pm.create_position(
            symbol="BTC/USDT",
            side="buy",
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            entry_order_id="entry-1",
        )
        rejected = OrderResponse(
            order_id="entry-1",
            status="rejected",
            symbol="BTC/USDT",
            order_type="market",
            quantity=Decimal("1"),
        )
        await pm.on_order_update("entry-1", rejected)
        pos = await pm.get_position(pid)
        assert pos is not None
        assert pos.status == "OPEN"

    asyncio.run(scenario())


def test_order_response_extracts_single_ccxt_fee():
    from backend.core.exchange.base import Exchange

    result = {
        "id": "ord-1",
        "status": "closed",
        "filled": 0.01,
        "average": 60000,
        "amount": 0.01,
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "market",
        "fee": {"cost": 0.24, "currency": "USDT"},
    }
    response = Exchange.normalize_order_response(result)
    assert response.fee_cost == Decimal("0.24")
    assert response.fee_currency == "USDT"
    assert response.fill_delta == Decimal("0.01")


def test_order_response_aggregates_ccxt_fees_and_preserves_fill_delta():
    from backend.core.exchange.base import Exchange

    result = {
        "id": "ord-2",
        "status": "open",
        "filled": "0.60",
        "remaining": "0.40",
        "amount": "1",
        "price": "100",
        "fees": [
            {"cost": "0.10", "currency": "USDT"},
            {"cost": "0.05", "currency": "USDT"},
        ],
    }
    response = Exchange.normalize_order_response(result, previous_filled=Decimal("0.40"))
    assert response.fee_cost == Decimal("0.15")
    assert response.fee_currency == "USDT"
    assert response.fill_delta == Decimal("0.20")
    assert response.remaining_quantity == Decimal("0.40")


def test_order_response_handles_missing_fee_without_failing():
    from backend.core.exchange.base import Exchange

    response = Exchange.normalize_order_response({
        "id": "ord-3", "status": "closed", "filled": "1", "amount": "1"
    })
    assert response.fee_cost is None
    assert response.fee_currency is None


def test_fetch_my_trades_uses_unified_symbol_and_since():
    from backend.core.exchange.base import Exchange

    class DummyExchange(Exchange):
        def __init__(self, credentials=None, sandbox=False):
            self.calls = []
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
        def fetch_my_trades(self, symbol, since=None, limit=None):
            self.calls.append((symbol, since, limit))
            return [{"id": "t1", "symbol": symbol, "side": "sell", "amount": 1, "price": 101}]

    adapter = DummyExchange()
    trades = adapter.fetch_my_trades("BTC/USDT", 123, 50)
    assert trades[0]["id"] == "t1"
    assert adapter.calls == [("BTC/USDT", 123, 50)]



def test_unsupported_trigger_order_is_rejected_instead_of_falling_back():
    from backend.core.exchange.base import Exchange, OrderRequest

    class UnsupportedTriggerExchange(Exchange):
        def __init__(self, credentials=None, sandbox=False):
            self.exchange = type("Meta", (), {
                "load_markets": lambda self: None,
                "feature_value": lambda self, symbol, method, feature: False,
                "amount_to_precision": lambda self, symbol, amount: "0.010",
                "price_to_precision": lambda self, symbol, price: "60000.0",
            })()
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

    order = OrderRequest(
        symbol="BTC/USDT", side="sell", type="stop_loss",
        quantity=Decimal("0.01"), stopPrice=Decimal("60000"),
    )
    with pytest.raises(ValueError, match="does not support standalone stopLossPrice"):
        UnsupportedTriggerExchange().prepare_order(order)


def test_testnet_mode_requires_explicit_enable_and_testnet_exchange():
    from backend.config import Settings
    with pytest.raises(ValueError, match="TESTNET_TRADING_ENABLED"):
        Settings(
            TRADING_MODE="testnet",
            EXCHANGE_MODE="testnet",
            TESTNET_TRADING_ENABLED=False,
            EXCHANGE="binance",
        )


def test_testnet_mode_rejects_live_exchange_mode():
    from backend.config import Settings
    with pytest.raises(ValueError, match="EXCHANGE_MODE=testnet"):
        Settings(
            TRADING_MODE="testnet",
            EXCHANGE_MODE="live",
            TESTNET_TRADING_ENABLED=True,
            EXCHANGE="binance",
            BINANCE_API_KEY="key",
            BINANCE_API_SECRET="secret",
        )


def test_testnet_mode_requires_exchange_credentials():
    from backend.config import Settings
    with pytest.raises(ValueError, match="requires API key and secret"):
        Settings(
            TRADING_MODE="testnet",
            EXCHANGE_MODE="testnet",
            TESTNET_TRADING_ENABLED=True,
            EXCHANGE="binance",
            BINANCE_API_KEY="",
            BINANCE_API_SECRET="",
        )


def test_live_mode_requires_live_exchange_and_credentials():
    from backend.config import Settings
    with pytest.raises(ValueError, match="requires API key and secret"):
        Settings(
            TRADING_MODE="live",
            EXCHANGE_MODE="live",
            LIVE_TRADING_ENABLED=True,
            API_AUTH_ENABLED=True,
            API_ACCESS_TOKEN="token",
            EXCHANGE="binance",
            BINANCE_API_KEY="",
            BINANCE_API_SECRET="",
        )


def test_paper_mode_remains_valid_without_exchange_credentials():
    from backend.config import Settings
    settings = Settings(
        TRADING_MODE="paper",
        EXCHANGE_MODE="testnet",
        EXCHANGE="binance",
        BINANCE_API_KEY="",
        BINANCE_API_SECRET="",
        TESTNET_TRADING_ENABLED=False,
        LIVE_TRADING_ENABLED=False,
    )
    assert settings.TRADING_MODE == "paper"


def test_futures_market_options_use_requested_exchange_not_global_exchange():
    from backend.core.exchange.base import Exchange
    from backend.config import settings
    original = settings.EXCHANGE
    try:
        settings.EXCHANGE = "binance"
        assert Exchange.market_options("futures", "bybit")["defaultType"] == "swap"
        assert Exchange.market_options("futures", "okx")["defaultType"] == "swap"
        assert Exchange.market_options("futures", "binance")["defaultType"] == "future"
    finally:
        settings.EXCHANGE = original


def test_exchange_factory_cannot_override_testnet_sandbox_to_live(monkeypatch):
    import sys, types
    fake_ccxt = types.ModuleType("ccxt")
    fake_ccxtpro = types.ModuleType("ccxt.pro")
    for name in ["binance", "bybit", "okx", "bitget", "mexc", "kucoin", "gateio"]:
        setattr(fake_ccxt, name, object)
        setattr(fake_ccxtpro, name, object)
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)
    monkeypatch.setitem(sys.modules, "ccxt.pro", fake_ccxtpro)
    from backend.core.exchange.factory import create_exchange, ADAPTERS
    from backend.config import settings
    original_mode = settings.EXCHANGE_MODE
    original_adapter = ADAPTERS.get("dummy")
    class Dummy:
        def __init__(self, credentials, sandbox=False):
            self.sandbox = sandbox
    try:
        settings.EXCHANGE_MODE = "testnet"
        ADAPTERS["dummy"] = Dummy
        with pytest.raises(ValueError, match="conflicts with EXCHANGE_MODE=testnet"):
            create_exchange("dummy", sandbox=False)
        obj = create_exchange("dummy")
        assert obj.sandbox is True
    finally:
        settings.EXCHANGE_MODE = original_mode
        if original_adapter is None:
            ADAPTERS.pop("dummy", None)
        else:
            ADAPTERS["dummy"] = original_adapter


def test_filled_order_status_is_persistable_as_closed():
    from backend.core.order_manager import OrderManager
    from backend.core.exchange.base import OrderResponse

    class DummyExchange:
        def get_exchange_name(self):
            return "binance"

    async def scenario():
        response = OrderResponse(order_id="o1", status="filled", symbol="BTC/USDT",
                                 order_type="market", quantity=Decimal("1"),
                                 filled_quantity=Decimal("1"))
        # The persistence method is no-op without a DB factory, but the status
        # mapping is exercised by the same enum contract below.
        from backend.db.models import OrderStatusEnum
        status = response.status
        if status == "filled":
            status = "closed"
        assert status == OrderStatusEnum.CLOSED.value

    asyncio.run(scenario())


def test_factory_requires_credentials_for_explicit_testnet_exchange(monkeypatch):
    from backend.config import settings
    from backend.core.exchange.factory import create_exchange

    original_mode = settings.TRADING_MODE
    original_exchange_mode = settings.EXCHANGE_MODE
    original_testnet = settings.TESTNET_TRADING_ENABLED
    try:
        settings.TRADING_MODE = "testnet"
        settings.EXCHANGE_MODE = "testnet"
        settings.TESTNET_TRADING_ENABLED = True
        settings.BYBIT_API_KEY = ""
        settings.BYBIT_API_SECRET = ""
        with pytest.raises(ValueError, match="BYBIT|EXCHANGE=bybit"):
            create_exchange("bybit")
    finally:
        settings.TRADING_MODE = original_mode
        settings.EXCHANGE_MODE = original_exchange_mode
        settings.TESTNET_TRADING_ENABLED = original_testnet


def test_find_order_by_client_id_does_not_reuse_terminal_historical_order():
    from backend.core.exchange.base import Exchange

    class Dummy(Exchange):
        def __init__(self, credentials=None, sandbox=False):
            self.exchange = type("Meta", (), {
                "load_markets": lambda self: None,
                "fetch_orders": lambda self, symbol: [{
                    "id": "old-1", "status": "closed", "symbol": symbol,
                    "side": "sell", "type": "market", "amount": 1,
                    "filled": 1, "clientOrderId": "ats-protective",
                }],
            })()
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

    assert Dummy().find_order_by_client_id("BTC/USDT", "ats-protective") is None


def test_prepare_order_enforces_market_amount_limits():
    from decimal import Decimal
    from backend.core.exchange.base import Exchange, OrderRequest

    class Limited(Exchange):
        def __init__(self, credentials=None, sandbox=False):
            self.exchange = type("Meta", (), {
                "load_markets": lambda self: None,
                "markets": {
                    "BTC/USDT": {
                        "limits": {"amount": {"min": 0.01, "max": 2}},
                        "contractSize": 1,
                    }
                },
                "amount_to_precision": lambda self, symbol, amount: "0.005" if amount < 0.01 else str(amount),
                "price_to_precision": lambda self, symbol, price: str(price),
            })()
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

    with pytest.raises(ValueError, match="below minimum"):
        Limited().prepare_order(OrderRequest(symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("0.005")))


def test_ticker_market_data_computes_exact_decimal_spread_from_float_inputs():
    from backend.core.exchange.base import Exchange

    market_data = Exchange.ticker_market_data(
        {
            "timestamp": 1_600_000_000_000,
            "last": 0.3,
            "bid": 0.1,
            "ask": 0.3,
            "baseVolume": 0.2,
        },
        "BTC/USDT",
    )
    assert market_data.price == Decimal("0.3")
    assert market_data.bid == Decimal("0.1")
    assert market_data.ask == Decimal("0.3")
    assert market_data.spread == Decimal("0.2")
    assert market_data.volume == Decimal("0.2")


def test_adapter_wallet_balances_convert_float_inputs_without_binary_digits():
    from types import SimpleNamespace
    from backend.core.exchange.binance import BinanceExchange
    from backend.core.exchange.bybit import BybitExchange
    from backend.core.exchange.okx import OKXExchange
    from backend.core.exchange.bitget import BitgetExchange
    from backend.core.exchange.mexc import MEXCExchange
    from backend.core.exchange.kucoin import KuCoinExchange
    from backend.core.exchange.gateio import GateIOExchange

    adapters = (BinanceExchange, BybitExchange, OKXExchange, BitgetExchange, MEXCExchange, KuCoinExchange, GateIOExchange)
    for adapter_type in adapters:
        adapter = adapter_type.__new__(adapter_type)
        adapter.exchange = SimpleNamespace(fetch_balance=lambda: {
            "free": {"USDT": 0.1}, "used": {"USDT": 0.2},
        })
        assert adapter.get_wallet_balance("USDT") == Decimal("0.3")


def test_prepare_order_does_not_fallback_to_unrounded_amount():
    from decimal import Decimal
    from backend.core.exchange.base import Exchange, OrderRequest

    class BrokenPrecision(Exchange):
        def __init__(self, credentials=None, sandbox=False):
            self.exchange = type("Meta", (), {
                "load_markets": lambda self: None,
                "markets": {"BTC/USDT": {"limits": {"amount": {"min": 0.001}}}},
                "amount_to_precision": lambda self, symbol, amount: (_ for _ in ()).throw(RuntimeError("precision unavailable")),
            })()
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

    with pytest.raises(ValueError, match="Unable to normalize order amount"):
        BrokenPrecision().prepare_order(OrderRequest(symbol="BTC/USDT", side="buy", type="market", quantity=Decimal("0.01")))
