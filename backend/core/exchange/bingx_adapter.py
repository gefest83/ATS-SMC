"""
BingX Exchange Adapter
Implements ExchangeAdapter interface for BingX API
"""
import hashlib
import hmac
import time
from typing import Dict, List, Optional, Any
from decimal import Decimal
import aiohttp
from backend.core.exchange.base_adapter import ExchangeAdapter
from backend.models.market_data import Candle, Ticker, OrderBook
from backend.models.order import Order, OrderType, OrderSide, OrderStatus
from backend.models.balance import Balance
from backend.config.settings import settings


class BingXAdapter(ExchangeAdapter):
    """BingX Exchange Adapter"""

    NAME = "bingx"
    BASE_URL = "https://open-api.bingx.com"
    TESTNET_URL = "https://open-api-testnet.bingx.com"

    def __init__(self, api_key: str, secret_key: str, testnet: bool = False):
        super().__init__(api_key, secret_key, testnet)
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        self.recv_window = 5000

    def _generate_signature(self, method: str, path: str, timestamp: int, query_string: str = "") -> str:
        """Generate HMAC SHA256 signature"""
        params = f"{timestamp}{method}{path}"
        if query_string:
            params += f"?{query_string}"
        return hmac.new(
            self.secret_key.encode('utf-8'),
            params.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    async def connect(self) -> bool:
        """Connect to BingX"""
        try:
            await self.get_server_time()
            self.connected = True
            self.logger.info(f"Connected to BingX ({'testnet' if self.testnet else 'live'})")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to BingX: {e}")
            return False

    async def disconnect(self):
        """Disconnect from BingX"""
        self.connected = False
        self.logger.info("Disconnected from BingX")

    async def get_balance(self) -> List[Balance]:
        """Get account balance"""
        if not self.connected:
            raise ConnectionError("Not connected to BingX")

        path = "/openApi/spot/v1/account/balance"
        timestamp = int(time.time() * 1000)
        signature = self._generate_signature("GET", path, timestamp)

        headers = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}{path}",
                headers=headers,
                params={"timestamp": timestamp, "signature": signature},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                data = await response.json()
                if data.get("code") == 0:
                    balances = []
                    for bal in data.get("data", {}).get("balances", []):
                        free = Decimal(bal.get("free", "0"))
                        locked = Decimal(bal.get("locked", "0"))
                        if free > 0 or locked > 0:
                            balances.append(Balance(
                                asset=bal["asset"],
                                free=free,
                                locked=locked,
                                total=free + locked
                            ))
                    return balances
                raise Exception(f"BingX API Error: {data.get('msg', 'Unknown error')}")

    async def get_markets(self) -> Dict[str, Any]:
        """Get all markets"""
        path = "/openApi/spot/v1/common/symbols"
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}") as response:
                data = await response.json()
                if data.get("code") == 0:
                    markets = {}
                    for symbol in data.get("data", []):
                        sym = symbol["symbol"]
                        markets[sym] = {
                            "symbol": sym,
                            "baseAsset": symbol["baseAsset"],
                            "quoteAsset": symbol["quoteAsset"],
                            "status": symbol["status"],
                            "minTradeAmount": Decimal(symbol["minTradeAmount"]),
                            "maxTradeAmount": Decimal(symbol["maxTradeAmount"]),
                            "takerFeeRate": Decimal(symbol["takerFeeRate"]),
                            "makerFeeRate": Decimal(symbol["makerFeeRate"]),
                            "pricePrecision": symbol["pricePrecision"],
                            "quantityPrecision": symbol["quantityPrecision"]
                        }
                    return markets
                raise Exception(f"BingX API Error: {data.get('msg', 'Unknown error')}")

    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol info"""
        markets = await self.get_markets()
        return markets.get(symbol)

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[Candle]:
        """Fetch OHLCV data"""
        path = "/openApi/spot/v1/market/kline"
        params = {
            "symbol": symbol.replace("/", ""),
            "interval": self._normalize_timeframe(timeframe),
            "limit": limit
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}", params=params) as response:
                data = await response.json()
                if data.get("code") == 0:
                    candles = []
                    for k in data.get("data", []):
                        candles.append(Candle(
                            timestamp=k[0],
                            open=Decimal(k[1]),
                            high=Decimal(k[2]),
                            low=Decimal(k[3]),
                            close=Decimal(k[4]),
                            volume=Decimal(k[5])
                        ))
                    return candles
                raise Exception(f"BingX API Error: {data.get('msg', 'Unknown error')}")

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch ticker"""
        path = "/openApi/spot/v1/market/ticker"
        params = {"symbol": symbol.replace("/", "")}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}", params=params) as response:
                data = await response.json()
                if data.get("code") == 0:
                    t = data["data"]
                    return Ticker(
                        symbol=symbol,
                        last_price=Decimal(t["last"]),
                        bid_price=Decimal(t["buy"]),
                        ask_price=Decimal(t["sell"]),
                        high_24h=Decimal(t["high"]),
                        low_24h=Decimal(t["low"]),
                        volume_24h=Decimal(t["vol"])
                    )
                raise Exception(f"BingX API Error: {data.get('msg', 'Unknown error')}")

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Fetch open orders"""
        path = "/openApi/spot/v1/trade/openOrders"
        timestamp = int(time.time() * 1000)
        params = {"timestamp": timestamp}
        if symbol:
            params["symbol"] = symbol.replace("/", "")
        
        signature = self._generate_signature("GET", path, timestamp)
        params["signature"] = signature
        
        headers = {"X-BX-APIKEY": self.api_key}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}{path}",
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                data = await response.json()
                if data.get("code") == 0:
                    orders = []
                    for o in data.get("data", {}).get("orderDetails", []):
                        orders.append(Order(
                            exchange_order_id=str(o["orderId"]),
                            client_order_id=o.get("clientOrderId", ""),
                            symbol=symbol or o["symbol"],
                            side=OrderSide.BUY if o["side"] == "BUY" else OrderSide.SELL,
                            type=OrderType.LIMIT if o["type"] == "LIMIT" else OrderType.MARKET,
                            quantity=Decimal(o["qty"]),
                            price=Decimal(o["price"]) if o.get("price") else None,
                            status=OrderStatus.OPEN,
                            created_at=o["orderTime"]
                        ))
                    return orders
                raise Exception(f"BingX API Error: {data.get('msg', 'Unknown error')}")

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        type_: OrderType,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        client_order_id: Optional[str] = None
    ) -> Order:
        """Create order"""
        path = "/openApi/spot/v1/trade/order"
        timestamp = int(time.time() * 1000)
        
        data = {
            "symbol": symbol.replace("/", ""),
            "side": side.value,
            "type": type_.value,
            "quantity": str(quantity),
            "timestamp": timestamp
        }
        
        if price and type_ == OrderType.LIMIT:
            data["price"] = str(price)
        
        if client_order_id:
            data["clientOrderId"] = client_order_id
            
        query_string = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
        signature = self._generate_signature("POST", path, timestamp, query_string)
        data["signature"] = signature
        
        headers = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}{path}",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()
                if result.get("code") == 0:
                    o = result["data"]
                    return Order(
                        exchange_order_id=str(o["orderId"]),
                        client_order_id=client_order_id or "",
                        symbol=symbol,
                        side=side,
                        type=type_,
                        quantity=quantity,
                        price=price,
                        status=OrderStatus.SUBMITTED,
                        created_at=int(time.time() * 1000)
                    )
                raise Exception(f"BingX Order Error: {result.get('msg', 'Unknown error')}")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel order"""
        path = "/openApi/spot/v1/trade/cancel"
        timestamp = int(time.time() * 1000)
        
        data = {
            "symbol": symbol.replace("/", ""),
            "orderId": order_id,
            "timestamp": timestamp
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
        signature = self._generate_signature("DELETE", path, timestamp, query_string)
        data["signature"] = signature
        
        headers = {"X-BX-APIKEY": self.api_key}
        
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{self.base_url}{path}",
                headers=headers,
                params=data,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()
                return result.get("code") == 0

    async def get_server_time(self) -> int:
        """Get server time"""
        path = "/openApi/spot/v1/common/time"
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}") as response:
                data = await response.json()
                if data.get("code") == 0:
                    return data["data"]
                raise Exception("Failed to get server time")

    def _normalize_timeframe(self, tf: str) -> str:
        """Normalize timeframe for BingX"""
        mapping = {
            "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h",
            "6h": "6h", "12h": "12h", "1d": "1d", "1w": "1w", "1M": "1M"
        }
        return mapping.get(tf, "1m")
