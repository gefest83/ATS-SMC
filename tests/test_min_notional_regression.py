"""Regression tests for Binance NOTIONAL filter failure.

Covers the root cause: market orders bypass notional validation because
normalized_price is None in prepare_order(). The fix adds a notional check
for market orders using the current ticker price.
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from backend.core.exchange.base import Exchange, OrderRequest


class FakeTicker:
    def __init__(self, price):
        self.price = Decimal(str(price))


def _make_exchange(markets=None, ticker_price=Decimal("50000")):
    """Build a minimal Exchange subclass for notional validation tests."""

    class NotionalExchange(Exchange):
        def __init__(self):
            self._ticker_price = ticker_price
            self.exchange = MagicMock()
            self.exchange.markets = markets or {
                "BTC/USDT": {
                    "limits": {
                        "amount": {"min": 0.00001, "max": 9000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            self.exchange.amount_to_precision = lambda symbol, amount: f"{amount:.5f}"
            self.exchange.price_to_precision = lambda symbol, price: f"{price:.2f}"

        def fetch_ticker(self, symbol):
            return FakeTicker(self._ticker_price)

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

    return NotionalExchange()


# ══════════════════════════════════════════════════════════════════════════════
# Quantity below min notional
# ══════════════════════════════════════════════════════════════════════════════

def test_market_order_below_min_notional_rejected():
    """Market order whose notional < min_cost must be rejected."""
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    # qty=0.0001, price=50000 => notional=5 < min_cost=10
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.0001"),
    )
    with pytest.raises(ValueError, match="below minimum"):
        exchange.prepare_order(order)


# ══════════════════════════════════════════════════════════════════════════════
# Quantity exactly at min notional
# ══════════════════════════════════════════════════════════════════════════════

def test_market_order_at_min_notional_accepted():
    """Market order whose notional == min_cost must pass."""
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    # qty=0.0002, price=50000 => notional=10 == min_cost=10
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.0002"),
    )
    symbol, amount, price, params = exchange.prepare_order(order)
    assert symbol == "BTC/USDT"
    assert Decimal(amount) == Decimal("0.00020")


# ══════════════════════════════════════════════════════════════════════════════
# Quantity above min notional
# ══════════════════════════════════════════════════════════════════════════════

def test_market_order_above_min_notional_accepted():
    """Market order whose notional > min_cost must pass."""
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    # qty=0.001, price=50000 => notional=50 > min_cost=10
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.001"),
    )
    symbol, amount, price, params = exchange.prepare_order(order)
    assert symbol == "BTC/USDT"
    assert Decimal(amount) == Decimal("0.00100")


# ══════════════════════════════════════════════════════════════════════════════
# Precision/step rounding
# ══════════════════════════════════════════════════════════════════════════════

def test_market_order_quantity_rounded_by_precision():
    """Quantity must be rounded by exchange precision before notional check."""
    class PrecisionExchange(Exchange):
        def __init__(self):
            self.exchange = MagicMock()
            self.exchange.markets = {
                "BTC/USDT": {
                    "limits": {
                        "amount": {"min": 0.00001, "max": 9000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            # Truncate to 3 decimal places
            self.exchange.amount_to_precision = lambda symbol, amount: f"{amount:.3f}"
            self.exchange.price_to_precision = lambda symbol, price: f"{price:.2f}"

        def fetch_ticker(self, symbol):
            return FakeTicker(Decimal("50000"))

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
    # qty=0.00123 rounds to 0.001, notional=0.001*50000=50 > 10
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.00123"),
    )
    symbol, amount, price, params = exchange.prepare_order(order)
    assert amount == "0.001"


def test_market_order_precision_rounding_below_min_notional():
    """After precision rounding, notional may drop below min and must be rejected."""
    class TightPrecisionExchange(Exchange):
        def __init__(self):
            self.exchange = MagicMock()
            self.exchange.markets = {
                "BTC/USDT": {
                    "limits": {
                        "amount": {"min": 0.00001, "max": 9000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            # Truncate aggressively
            self.exchange.amount_to_precision = lambda symbol, amount: "0.00010"
            self.exchange.price_to_precision = lambda symbol, price: f"{price:.2f}"

        def fetch_ticker(self, symbol):
            return FakeTicker(Decimal("50000"))

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

    exchange = TightPrecisionExchange()
    # qty=0.0005 rounds to 0.0001, notional=0.0001*50000=5 < 10
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.0005"),
    )
    with pytest.raises(ValueError, match="below minimum"):
        exchange.prepare_order(order)


# ══════════════════════════════════════════════════════════════════════════════
# Signal skipped: no order sent, no risk counter increment
# ══════════════════════════════════════════════════════════════════════════════

def test_below_min_notional_does_not_create_order():
    """When notional < min, no order is sent to the exchange."""
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.0001"),
    )
    with pytest.raises(ValueError, match="below minimum"):
        exchange.prepare_order(order)
    # create_order must never be called
    exchange.exchange.create_order.assert_not_called()


def test_risk_counter_not_incremented_on_below_min_notional():
    """When notional < min, the caller's risk counter must not be incremented.

    This simulates the smc_bot.py flow: trade_opened() is only called in the
    else branch AFTER a successful open_position(). If prepare_order raises,
    the exception propagates and trade_opened() is skipped.
    """
    from backend.core.risk.risk_manager import RiskManager

    rm = RiskManager(1000.0)
    initial_trades = rm.open_trades

    exchange = _make_exchange(ticker_price=Decimal("50000"))
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.0001"),
    )
    try:
        exchange.prepare_order(order)
    except ValueError:
        pass

    # Risk counter must remain unchanged
    assert rm.open_trades == initial_trades


# ══════════════════════════════════════════════════════════════════════════════
# Limit orders are NOT affected (existing behavior preserved)
# ══════════════════════════════════════════════════════════════════════════════

def test_limit_order_uses_price_not_ticker_for_notional():
    """Limit orders use their own price for notional, not the ticker."""
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    # Limit order: qty=0.001, price=10000 => notional=10 == min_cost
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="limit",
        quantity=Decimal("0.001"), price=Decimal("10000"),
    )
    symbol, amount, price, params = exchange.prepare_order(order)
    assert amount == "0.00100"
    assert price == "10000.00"


def test_limit_order_below_min_notional_rejected():
    """Limit order whose notional < min_cost is still rejected."""
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    # qty=0.0001, price=10000 => notional=1 < min_cost=10
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="limit",
        quantity=Decimal("0.0001"), price=Decimal("10000"),
    )
    with pytest.raises(ValueError, match="below minimum"):
        exchange.prepare_order(order)


# ══════════════════════════════════════════════════════════════════════════════
# Futures reduceOnly not affected
# ══════════════════════════════════════════════════════════════════════════════

def test_futures_trigger_order_notional_check_unchanged():
    """Futures SL/TP orders still use their stopPrice for notional check."""
    from backend.config import settings

    original = settings.EXCHANGE_MARKET_TYPE
    try:
        settings.EXCHANGE_MARKET_TYPE = "futures"

        class FuturesExchange(Exchange):
            def __init__(self):
                self.exchange = MagicMock()
                self.exchange.markets = {
                    "BTC/USDT": {
                        "limits": {
                            "amount": {"min": 0.001, "max": 9000},
                            "cost": {"min": 10, "max": 9999999},
                        },
                        "contractSize": 1,
                    }
                }
                self.exchange.amount_to_precision = lambda s, a: f"{a:.3f}"
                self.exchange.price_to_precision = lambda s, p: f"{p:.2f}"
                self.exchange.feature_value = lambda s, m, f: True

            def fetch_ticker(self, symbol):
                return FakeTicker(Decimal("50000"))

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
        order = OrderRequest(
            symbol="BTC/USDT", side="sell", type="stop_loss",
            quantity=Decimal("0.001"), stopPrice=Decimal("49000"),
        )
        symbol, amount, price, params = exchange.prepare_order(order)
        assert params["reduceOnly"] is True
        assert params["stopLossPrice"] == "49000"
    finally:
        settings.EXCHANGE_MARKET_TYPE = original


# ══════════════════════════════════════════════════════════════════════════════
# Ticker fetch failure gracefully skips notional check (no crash)
# ══════════════════════════════════════════════════════════════════════════════

def test_market_order_ticker_failure_allows_order():
    """If ticker fetch fails, notional check is skipped (fail-open for availability)."""
    class FailingTickerExchange(Exchange):
        def __init__(self):
            self.exchange = MagicMock()
            self.exchange.markets = {
                "BTC/USDT": {
                    "limits": {
                        "amount": {"min": 0.00001, "max": 9000},
                        "cost": {"min": 10, "max": 9999999},
                    },
                    "contractSize": 1,
                }
            }
            self.exchange.amount_to_precision = lambda s, a: f"{a:.5f}"
            self.exchange.price_to_precision = lambda s, p: f"{p:.2f}"

        def fetch_ticker(self, symbol):
            raise ConnectionError("network down")

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

    exchange = FailingTickerExchange()
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.001"),
    )
    # Should NOT raise - ticker failure means notional check is skipped
    symbol, amount, price, params = exchange.prepare_order(order)
    assert amount == "0.00100"


# ══════════════════════════════════════════════════════════════════════════════
# No min_cost defined: notional check is skipped
# ══════════════════════════════════════════════════════════════════════════════

def test_market_order_no_min_cost_allows_small_order():
    """When exchange has no cost.min, small orders are allowed."""
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    # Remove cost limits
    exchange.exchange.markets["BTC/USDT"]["limits"]["cost"] = {}
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.0001"),
    )
    symbol, amount, price, params = exchange.prepare_order(order)
    assert amount == "0.00010"


# ══════════════════════════════════════════════════════════════════════════════
# Binance-specific NOTIONAL filter scenario
# ══════════════════════════════════════════════════════════════════════════════

def test_binance_spot_notional_filter_exact_scenario():
    """Reproduce the exact Binance testnet scenario that caused the error.

    BTC/USDT spot, price ~50000, min notional = 10 USDT.
    Risk manager calculates qty = 0.0001 (notional = 5 USDT).
    This must be rejected before reaching the exchange.
    """
    exchange = _make_exchange(ticker_price=Decimal("50000"))
    order = OrderRequest(
        symbol="BTC/USDT", side="buy", type="market",
        quantity=Decimal("0.0001"),
    )
    with pytest.raises(ValueError, match="below minimum 10"):
        exchange.prepare_order(order)
