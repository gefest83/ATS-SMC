"""
HTX (Huobi) Exchange Adapter for ATS-SMT PRO.

Implements the ExchangeAdapter interface for HTX exchange.
Supports Spot trading with proper symbol normalization.
"""

import aiohttp
import time
import hashlib
import base64
import hmac
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime, timezone

from backend.core.exchange.base_adapter import ExchangeAdapter, OrderType, OrderSide, OrderStatus
from backend.config.settings import settings


class HTXAdapter(ExchangeAdapter):
    """HTX Exchange Adapter."""

    NAME = "htx"
    BASE_URL = "https://api.huobi.pro"
    WS_URL = "wss://api.huobi.pro/ws"

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        super().__init__(api_key, api_secret, testnet)
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0
        self._rate_limit_delay = 0.1  # 100ms between requests

    async def connect(self) -> bool:
        """Connect to HTX API."""
        try:
            self.session = aiohttp.ClientSession()
            server_time = await self.get_server_time()
            if server_time:
                self.connected = True
                return True
        except Exception as e:
            self.logger.error(f"Failed to connect to HTX: {e}")
            self.connected = False
        return False

    async def disconnect(self) -> None:
        """Disconnect from HTX API."""
        self.connected = False
        if self.session:
            await self.session.close()
            self.session = None

    async def _rate_limit(self) -> None:
        """Apply rate limiting."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        signed: bool = False
    ) -> Optional[Dict]:
        """Make HTTP request to HTX API."""
        await self._rate_limit()

        if not self.session:
            self.logger.error("Session not initialized")
            return None

        url = f"{self.BASE_URL}{endpoint}"
        headers = {"Content-Type": "application/json"}

        if signed and self.api_key and self.api_secret:
            params = params or {}
            params["AccessKeyId"] = self.api_key
            params["SignatureMethod"] = "HmacSHA256"
            params["SignatureVersion"] = "2"
            params["Timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

            # Create signature
            host = "api.huobi.pro"
            sorted_params = "&".join(
                [f"{k}={v}" for k, v in sorted(params.items())]
            )
            string_to_sign = f"{method}\n{host}\n{endpoint}\n{sorted_params}"
            signature = hmac.new(
                self.api_secret.encode(),
                string_to_sign.encode(),
                hashlib.sha256
            ).digest()
            signature_b64 = base64.b64encode(signature).decode()
            params["Signature"] = signature_b64

        try:
            async with self.session.request(
                method,
                url,
                params=params if method == "GET" else None,
                json=params if method == "POST" else None,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                data = await response.json()

                if response.status == 200:
                    if data.get("status") == "ok":
                        return data.get("data")
                    else:
                        err_code = data.get("err-code", "UNKNOWN")
                        err_msg = data.get("err-msg", str(data))
                        self.logger.warning(f"HTX API error: {err_code} - {err_msg}")
                        return None
                else:
                    self.logger.error(f"HTX HTTP error {response.status}: {data}")
                    return None

        except asyncio.TimeoutError:
            self.logger.error("HTX request timeout")
            return None
        except Exception as e:
            self.logger.error(f"HTX request failed: {e}")
            return None

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert canonical symbol to HTX format."""
        # BTC/USDT -> btcusdt
        return symbol.replace("/", "").lower()

    def _denormalize_symbol(self, ht_symbol: str) -> str:
        """Convert HTX symbol to canonical format."""
        # btcusdt -> BTC/USDT
        ht_symbol = ht_symbol.upper()
        for quote in ["USDT", "BTC", "ETH", "USD"]:
            if ht_symbol.endswith(quote):
                base = ht_symbol[:-len(quote)]
                return f"{base}/{quote}"
        return ht_symbol

    async def get_balance(self) -> Dict[str, float]:
        """Get account balance."""
        if not self.connected:
            return {}

        # Get account ID first
        accounts_data = await self._request("GET", "/v1/account/accounts", signed=True)
        if not accounts_data:
            return {}

        account_id = None
        for acc in accounts_data:
            if acc.get("type") == "spot":
                account_id = acc.get("id")
                break

        if not account_id:
            return {}

        # Get balance
        balance_data = await self._request(
            "GET",
            f"/v1/account/accounts/{account_id}/balance",
            signed=True
        )

        if not balance_data:
            return {}

        balances = {}
        for bal in balance_data.get("list", []):
            if bal.get("type") == "trade":
                currency = bal.get("currency", "").upper()
                amount = float(bal.get("balance", "0"))
                if amount > 0:
                    balances[currency] = amount

        return balances

    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all available markets."""
        data = await self._request("GET", "/v1/common/symbols")
        if not data:
            return []

        markets = []
        for symbol in data:
            base = symbol.get("base-currency", "")
            quote = symbol.get("quote-currency", "")
            state = symbol.get("state", "")

            if state == "online":
                markets.append({
                    "symbol": f"{base.upper()}/{quote.upper()}",
                    "base": base.upper(),
                    "quote": quote.upper(),
                    "active": True,
                    "precision": {
                        "amount": symbol.get("amount-precision", 8),
                        "price": symbol.get("price-precision", 8)
                    },
                    "limits": {
                        "amount": {
                            "min": float(symbol.get("min-order-amt", 0)),
                            "max": float(symbol.get("max-order-amt", 0))
                        },
                        "cost": {
                            "min": float(symbol.get("min-order-value", 0))
                        }
                    }
                })

        return markets

    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol info."""
        markets = await self.get_markets()
        ht_symbol = self._normalize_symbol(symbol)

        for market in markets:
            if market["symbol"] == symbol:
                return market
        return None

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100
    ) -> List[List]:
        """Fetch OHLCV candles."""
        ht_symbol = self._normalize_symbol(symbol)

        # Map timeframe to HTX period
        period_map = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "60min",
            "4h": "4hour",
            "1d": "1day",
            "1w": "1week",
            "1M": "1mon"
        }

        period = period_map.get(timeframe, "30min")

        data = await self._request(
            "GET",
            "/market/history/kline",
            params={"symbol": ht_symbol, "period": period, "size": limit}
        )

        if not data:
            return []

        # HTX returns newest first, reverse it
        candles = []
        for candle in reversed(data):
            candles.append([
                candle.get("id", 0) * 1000,  # ms timestamp
                float(candle.get("open", 0)),
                float(candle.get("high", 0)),
                float(candle.get("low", 0)),
                float(candle.get("close", 0)),
                float(candle.get("vol", 0))
            ])

        return candles

    async def fetch_ticker(self, symbol: str) -> Optional[Dict]:
        """Fetch ticker."""
        ht_symbol = self._normalize_symbol(symbol)

        data = await self._request(
            "GET",
            "/market/detail/merged",
            params={"symbol": ht_symbol}
        )

        if not data:
            return None

        tick = data.get("tick", {})
        return {
            "symbol": symbol,
            "last": float(tick.get("close", 0)),
            "bid": float(tick.get("bid", [0, 0])[0]),
            "ask": float(tick.get("ask", [0, 0])[0]),
            "high": float(tick.get("high", 0)),
            "low": float(tick.get("low", 0)),
            "volume": float(tick.get("amount", 0)),
            "timestamp": data.get("ts", 0)
        }

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Fetch open orders."""
        if not self.connected:
            return []

        params = {"signed": True}
        if symbol:
            params["symbol"] = self._normalize_symbol(symbol)

        data = await self._request("GET", "/v1/order/openOrders", params=params, signed=True)
        if not data:
            return []

        orders = []
        for order in data:
            orders.append({
                "order_id": str(order.get("id")),
                "client_order_id": order.get("client-order-id", ""),
                "symbol": self._denormalize_symbol(order.get("symbol", "")),
                "side": "BUY" if order.get("direction") == "buy" else "SELL",
                "type": "LIMIT" if "limit" in order.get("type", "") else "MARKET",
                "quantity": float(order.get("amount", 0)),
                "filled": float(order.get("field-amount", 0)),
                "price": float(order.get("price", 0)),
                "status": self._map_status(order.get("state", "")),
                "created_at": order.get("created-at", 0)
            })

        return orders

    async def fetch_order(self, order_id: str, symbol: str) -> Optional[Dict]:
        """Fetch specific order."""
        data = await self._request(
            "GET",
            f"/v1/order/orders/{order_id}",
            signed=True
        )

        if not data:
            return None

        return {
            "order_id": str(data.get("id")),
            "symbol": self._denormalize_symbol(data.get("symbol", "")),
            "side": "BUY" if data.get("direction") == "buy" else "SELL",
            "type": "LIMIT" if "limit" in data.get("type", "") else "MARKET",
            "quantity": float(data.get("amount", 0)),
            "filled": float(data.get("field-amount", 0)),
            "price": float(data.get("price", 0)),
            "status": self._map_status(data.get("state", "")),
            "created_at": data.get("created-at", 0)
        }

    async def fetch_positions(self) -> List[Dict]:
        """Fetch positions (Spot = balances)."""
        balance = await self.get_balance()
        positions = []

        for currency, amount in balance.items():
            if amount > 0:
                positions.append({
                    "symbol": f"{currency}/USDT",
                    "side": "LONG",
                    "quantity": amount,
                    "entry_price": 0,
                    "unrealized_pnl": 0
                })

        return positions

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None
    ) -> Optional[Dict]:
        """Create order."""
        if not self.connected:
            return None

        ht_symbol = self._normalize_symbol(symbol)

        # Map order type
        type_map = {
            (OrderSide.BUY, OrderType.LIMIT): "buy-limit",
            (OrderSide.BUY, OrderType.MARKET): "buy-market",
            (OrderSide.SELL, OrderType.LIMIT): "sell-limit",
            (OrderSide.SELL, OrderType.MARKET): "sell-market"
        }

        order_type_str = type_map.get((side, order_type), "buy-limit")

        params = {
            "account-id": await self._get_account_id(),
            "symbol": ht_symbol,
            "type": order_type_str,
            "amount": self._format_quantity(quantity, symbol)
        }

        if order_type == OrderType.LIMIT and price:
            params["price"] = self._format_price(price, symbol)

        data = await self._request("POST", "/v1/order/orders/place", params=params, signed=True)

        if not data:
            return None

        order_id = str(data)
        return {
            "order_id": order_id,
            "symbol": symbol,
            "side": side.value,
            "type": order_type.value,
            "quantity": quantity,
            "price": price,
            "status": "PENDING"
        }

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order."""
        data = await self._request(
            "POST",
            f"/v1/order/orders/{order_id}/submitcancel",
            signed=True
        )
        return data is not None and data.get("status") == "ok"

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """Cancel all orders."""
        open_orders = await self.fetch_open_orders(symbol)
        cancelled = 0

        for order in open_orders:
            if await self.cancel_order(order["order_id"]):
                cancelled += 1

        return cancelled

    async def close_position(self, symbol: str, quantity: Optional[float] = None) -> bool:
        """Close position (sell all)."""
        balance = await self.get_balance()
        base_currency = symbol.split("/")[0]

        available = balance.get(base_currency, 0)
        sell_qty = quantity if quantity else available

        if sell_qty <= 0:
            return False

        result = await self.create_order(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=sell_qty
        )

        return result is not None

    async def get_server_time(self) -> Optional[int]:
        """Get server time."""
        data = await self._request("GET", "/v1/common/timestamp")
        if data:
            return int(data)
        return None

    async def _get_account_id(self) -> Optional[str]:
        """Get spot account ID."""
        accounts = await self._request("GET", "/v1/account/accounts", signed=True)
        if accounts:
            for acc in accounts:
                if acc.get("type") == "spot":
                    return str(acc.get("id"))
        return None

    def _map_status(self, state: str) -> OrderStatus:
        """Map HTX status to internal status."""
        status_map = {
            "submitted": OrderStatus.PENDING,
            "partial-filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED
        }
        return status_map.get(state, OrderStatus.UNKNOWN)

    def _format_quantity(self, quantity: float, symbol: str) -> str:
        """Format quantity with correct precision."""
        precision = 6  # Default
        # Could fetch from symbol info
        return f"{quantity:.{precision}f}"

    def _format_price(self, price: float, symbol: str) -> str:
        """Format price with correct precision."""
        precision = 2  # Default
        return f"{price:.{precision}f}"
