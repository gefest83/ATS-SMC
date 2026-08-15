"""
MEXC Exchange Adapter for ATS-SMT PRO.
Implements unified interface for MEXC Spot API.
"""
import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

from backend.core.exchange.base_adapter import ExchangeAdapter, register_adapter


@register_adapter('mexc')
class MEXCAdapter(ExchangeAdapter):
    """Adapter for MEXC Exchange."""

    BASE_URL = "https://api.mexc.com"
    TESTNET_URL = "https://testnet-api.mexc.com"

    @property
    def name(self) -> str:
        return "mexc"

    def __init__(self, api_key: str, secret_key: str, testnet: bool = False):
        super().__init__(api_key, secret_key, testnet)
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        self.recv_window = 5000

    def _generate_signature(self, query_string: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def connect(self) -> bool:
        try:
            await self.get_server_time()
            self.connected = True
            return True
        except Exception:
            return False

    async def disconnect(self):
        self.connected = False

    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, signed: bool = False) -> Any:
        url = f"{self.base_url}{endpoint}"
        
        if params is None:
            params = {}

        headers = {}

        if signed:
            params["recvWindow"] = self.recv_window
            params["timestamp"] = int(time.time() * 1000)
            query_string = urlencode(params)
            signature = self._generate_signature(query_string)
            params["signature"] = signature
            headers = {
                "X-MEXC-APIKEY": self.api_key,
                "X-MEXC-SIGNATURE": signature,
                "X-MEXC-TIMESTAMP": str(params["timestamp"]),
            }

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, params=params, headers=headers) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise Exception(f"MEXC Error {resp.status}: {data}")
                return data

    async def get_balance(self) -> Dict[str, float]:
        data = await self._request("GET", "/api/v3/account", signed=True)
        balances = {}
        for bal in data.get("balances", []):
            free = float(bal.get("free", 0))
            locked = float(bal.get("locked", 0))
            if free > 0 or locked > 0:
                balances[bal["asset"]] = free
        return balances

    async def get_markets(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/api/v3/exchangeInfo")
        markets = []
        for symbol in data.get("symbols", []):
            if symbol.get("status") == "TRADING":
                filters = symbol.get("filters", [])
                lot_size = next((f for f in filters if f.get("filterType") == "LOT_SIZE"), {})
                price_filter = next((f for f in filters if f.get("filterType") == "PRICE_FILTER"), {})
                notional = next((f for f in filters if f.get("filterType") == "NOTIONAL"), {})
                
                markets.append({
                    "symbol": symbol["symbol"],
                    "base_asset": symbol["baseAsset"],
                    "quote_asset": symbol["quoteAsset"],
                    "min_qty": float(lot_size.get("minQty", 0)),
                    "max_qty": float(lot_size.get("maxQty", 1000000)),
                    "step_size": float(lot_size.get("stepSize", 0.00001)),
                    "tick_size": float(price_filter.get("tickSize", 0.01)),
                    "min_notional": float(notional.get("minNotional", 5))
                })
        return markets

    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        markets = await self.get_markets()
        norm_symbol = self.normalize_symbol(symbol)
        for m in markets:
            if m["symbol"] == norm_symbol:
                return m
        return None

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100, since: Optional[int] = None) -> Any:
        import pandas as pd
        interval_map = {
            "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h",
            "1d": "1d", "3d": "3d", "1w": "1w", "1M": "1M"
        }
        interval = interval_map.get(timeframe, "30m")
        data = await self._request("GET", "/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df[["timestamp", "open", "high", "low", "close", "volume"]]

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        data = await self._request("GET", "/api/v3/ticker/24hr", params={"symbol": symbol})
        return {
            "symbol": data.get("symbol"),
            "last_price": float(data.get("lastPrice", 0)),
            "bid": float(data.get("bidPrice", 0)),
            "ask": float(data.get("askPrice", 0)),
            "volume": float(data.get("volume", 0)),
            "change_24h": float(data.get("priceChangePercent", 0))
        }

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/api/v3/openOrders", params=params, signed=True)
        return data

    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        data = await self._request("GET", "/api/v3/order", params={"symbol": symbol, "orderId": order_id}, signed=True)
        return data

    async def create_order(self, symbol: str, side: str, type_: str, quantity: float, price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        order_params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": type_.upper(),
            "quantity": f"{quantity}",
            "newOrderRespType": "FULL"
        }
        if price and type_.upper() == "LIMIT":
            order_params["price"] = f"{price}"
            order_params["timeInForce"] = "GTC"

        data = await self._request("POST", "/api/v3/order", params=order_params, signed=True)
        
        return {
            "order_id": str(data.get("orderId", "")),
            "client_order_id": data.get("clientOrderId", ""),
            "status": data.get("status", "submitted"),
            "filled_qty": float(data.get("executedQty", 0)),
            "avg_price": float(data.get("avgPrice", 0) or price or 0)
        }

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        data = await self._request("DELETE", "/api/v3/order", params={"symbol": symbol, "orderId": order_id}, signed=True)
        return data

    async def cancel_all_orders(self, symbol: str) -> None:
        await self._request("DELETE", "/api/v3/openOrders", params={"symbol": symbol}, signed=True)

    async def close_position(self, symbol: str, quantity: float, side: str) -> Dict[str, Any]:
        return await self.create_order(symbol, side, "MARKET", quantity)

    async def get_server_time(self) -> int:
        data = await self._request("GET", "/api/v3/time")
        return data["serverTime"]

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "")

    def denormalize_symbol(self, symbol: str) -> str:
        symbol = symbol.upper()
        for quote in ["USDT", "BUSD", "BTC", "ETH"]:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}"
        return symbol

    async def fetch_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return []
