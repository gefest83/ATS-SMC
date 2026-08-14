import asyncio
from decimal import Decimal

import pytest

from backend.core.exchange.base import OrderRequest, OrderResponse
from backend.core.order_manager import OrderManager


class AmbiguousExchange:
    def __init__(self):
        self.calls = 0

    def create_order(self, order):
        self.calls += 1
        raise TimeoutError("response lost after submission")

    def get_exchange_name(self):
        return "test"


@pytest.mark.asyncio
async def test_create_order_is_not_blindly_retried():
    exchange = AmbiguousExchange()
    manager = OrderManager.__new__(OrderManager)
    manager.exchange = exchange
    manager.retry_counts = {}

    request = OrderRequest(
        symbol="BTC/USDT",
        side="buy",
        type="market",
        quantity=Decimal("1"),
    )

    with pytest.raises(RuntimeError, match="automatic retry disabled"):
        await manager._place_order_with_retry(request)

    assert exchange.calls == 1


class ReconciledExchange:
    def __init__(self):
        self.calls = 0
        self.recovered = OrderResponse(
            order_id="exchange-42",
            status="closed",
            filled_quantity=Decimal("1"),
        )
        self.client_id = None

    def create_order(self, order):
        self.calls += 1
        self.client_id = order.client_order_id
        raise TimeoutError("response lost after submission")

    def find_order_by_client_id(self, symbol, client_order_id):
        assert client_order_id == self.client_id
        self.recovered.client_order_id = client_order_id
        return self.recovered

    def get_exchange_name(self):
        return "test"


@pytest.mark.asyncio
async def test_ambiguous_submission_is_recovered_by_client_order_id():
    exchange = ReconciledExchange()
    manager = OrderManager.__new__(OrderManager)
    manager.exchange = exchange
    manager.retry_counts = {}

    request = OrderRequest(
        symbol="BTC/USDT",
        side="buy",
        type="market",
        quantity=Decimal("1"),
    )

    response = await manager._place_order_with_retry(request)

    assert exchange.calls == 1
    assert response.order_id == "exchange-42"
    assert response.client_order_id == request.client_order_id
    assert request.client_order_id.startswith("ats-")

@pytest.mark.parametrize(
    "exchange_name,expected_param",
    [
        ("binance", "newClientOrderId"),
        ("bybit", "orderLinkId"),
        ("okx", "clOrdId"),
        ("bitget", "clientOid"),
        ("mexc", "clientOrderId"),
        ("kucoin", "clientOid"),
        ("gateio", "text"),
    ],
)
def test_exchange_specific_client_order_id_parameter(exchange_name, expected_param):
    from backend.core.exchange.base import Exchange, OrderRequest

    class Dummy(Exchange):
        def __init__(self):
            self.exchange = type("Meta", (), {
                "load_markets": lambda self: None,
                "amount_to_precision": lambda self, symbol, amount: "1",
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
        def get_exchange_name(self): return exchange_name

    adapter = Dummy()
    order = OrderRequest(
        symbol="BTC/USDT",
        side="buy",
        type="market",
        quantity=Decimal("1"),
        client_order_id="ats-test1234567890",
    )
    _, _, _, params = adapter.prepare_order(order)
    assert params[expected_param] == order.client_order_id
    assert "clientOrderId" not in params or expected_param == "clientOrderId"


def test_normalize_order_response_recovers_native_client_id_from_info():
    from backend.core.exchange.base import Exchange

    result = {
        "id": "ord-1",
        "info": {"clOrdId": "ats-native-id"},
        "status": "open",
        "filled": "0",
    }
    normalized = Exchange.normalize_order_response(result)
    assert normalized.client_order_id == "ats-native-id"


def test_extract_fee_preserves_multiple_currencies():
    from backend.core.exchange.base import Exchange
    result = {
        "id": "ord-fee",
        "status": "closed",
        "filled": "1",
        "fees": [
            {"cost": "0.10", "currency": "USDT"},
            {"cost": "0.001", "currency": "BNB"},
        ],
    }
    cost, currency, items = Exchange.extract_fee(result)
    assert cost == Decimal("0.101")
    assert currency is None
    assert (Decimal("0.10"), "USDT") in items
    assert (Decimal("0.001"), "BNB") in items
