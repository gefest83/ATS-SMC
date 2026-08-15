"""
Bitget Exchange Adapter
Implements ExchangeAdapter interface for Bitget API
"""
import hashlib
import hmac
import time
from typing import Dict, List, Optional, Any
from decimal import Decimal
import aiohttp
from backend.core.exchange.base_adapter import ExchangeAdapter
from backend.models.schemas import OrderType, OrderSide, OrderStatus, Balance


class BitgetAdapter(ExchangeAdapter):
    """Bitget Exchange Adapter"""

    NAME = "bitget"
    BASE_URL = "https://api.bitget.com"

    def __init__(self, api_key: str, secret_key: str, passphrase: str, testnet: bool = False):
        super().__init__(api_key, secret_key, testnet)
        self.passphrase = passphrase
        self.base_url = "https://test.bitget.com" if testnet else self.BASE_URL

    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Generate HMAC SHA256 signature"""
        message = timestamp + method + path + body
        return hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).base64encode().decode()

    async def connect(self) -> bool:
        """Connect to Bitget"""
        try:
            await self.get_server_time()
            self.connected = True
            self.logger.info(f"Connected to Bitget ({'testnet' if self.testnet else 'live'})")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Bitget: {e}")
            return False

    async def disconnect(self):
        """Disconnect from Bitget"""
        self.connected = False
        self.logger.info("Disconnected from Bitget")

    async def get_balance(self) -> List[Balance]:
        """Get account balance"""
        if not self.connected:
            raise ConnectionError("Not connected to Bitget")

        path = "/api/v1/spot/account/assets"
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, "GET", path)

        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                data = await response.json()
                if data.get("code") == "00000":
                    balances = []
                    for bal in data.get("data", []):
                        free = Decimal(bal.get("available", "0"))
                        locked = Decimal(bal.get("frozen", "0"))
                        if free > 0 or locked > 0:
                            balances.append(Balance(
                                asset=bal["coin"],
                                free=free,
                                locked=locked,
                                total=free + locked
                            ))
                    return balances
                raise Exception(f"Bitget API Error: {data.get('msg', 'Unknown error')}")

    async def get_markets(self) -> Dict[str, Any]:
        """Get all markets"""
        path = "/api/v1/spot/public/symbols"
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}") as response:
                data = await response.json()
                if data.get("code") == "00000":
                    markets = {}
                    for symbol in data.get("data", []):
                        sym = symbol["symbolName"]
                        markets[sym] = {
                            "symbol": sym,
                            "baseAsset": symbol["baseCoin"],
                            "quoteAsset": symbol["quoteCoin"],
                            "status": symbol["status"],
                            "minTradeAmount": Decimal(symbol["minTradeAmount"]),
                            "maxTradeAmount": Decimal(symbol["maxTradeAmount"]),
                            "takerFeeRate": Decimal(symbol["takerFeeRate"]),
                            "makerFeeRate": Decimal(symbol["makerFeeRate"]),
                            "pricePrecision": int(symbol["pricePrecision"]),
                            "quantityPrecision": int(symbol["quantityPrecision"])
                        }
                    return markets
                raise Exception(f"Bitget API Error: {data.get('msg', 'Unknown error')}")

    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol info"""
        markets = await self.get_markets()
        return markets.get(symbol)

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch OHLCV data"""
        path = "/api/v1/spot/market/candles"
        params = {
            "symbol": symbol.replace("/", ""),
            "period": self._normalize_timeframe(timeframe),
            "limit": limit
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}", params=params) as response:
                data = await response.json()
                if data.get("code") == "00000":
                    candles = []
                    for k in data.get("data", []):
                        candles.append(Candle(
                            timestamp=int(k[0]),
                            open=Decimal(k[1]),
                            high=Decimal(k[2]),
                            low=Decimal(k[3]),
                            close=Decimal(k[4]),
                            volume=Decimal(k[5])
                        ))
                    return candles
                raise Exception(f"Bitget API Error: {data.get('msg', 'Unknown error')}")

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch ticker"""
        path = "/api/v1/spot/market/tickers"
        params = {"symbol": symbol.replace("/", "")}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}", params=params) as response:
                data = await response.json()
                if data.get("code") == "00000":
                    t = data["data"][0]
                    return Ticker(
                        symbol=symbol,
                        last_price=Decimal(t["last"]),
                        bid_price=Decimal(t["buyOne"]),
                        ask_price=Decimal(t["sellOne"]),
                        high_24h=Decimal(t["high24h"]),
                        low_24h=Decimal(t["low24h"]),
                        volume_24h=Decimal(t["baseVol"])
                    )
                raise Exception(f"Bitget API Error: {data.get('msg', 'Unknown error')}")

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open orders"""
        path = "/api/v1/spot/trade/unfilledOrders"
        params = {}
        if symbol:
            params["symbol"] = symbol.replace("/", "")
        
        timestamp = str(int(time.time() * 1000))
        query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        signature = self._generate_signature(timestamp, "GET", f"{path}?{query_string}" if query_string else path)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}{path}",
                headers=headers,
                params=params if params else None,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                data = await response.json()
                if data.get("code") == "00000":
                    orders = []
                    for o in data.get("data", []):
                        orders.append(Order(
                            exchange_order_id=o["orderId"],
                            client_order_id=o.get("clientOrderId", ""),
                            symbol=symbol or o["symbol"],
                            side=OrderSide.BUY if o["side"] == "buy" else OrderSide.SELL,
                            type=OrderType.LIMIT if o["orderType"] == "limit" else OrderType.MARKET,
                            quantity=Decimal(o["size"]),
                            price=Decimal(o["price"]) if o.get("price") else None,
                            status=OrderStatus.OPEN,
                            created_at=int(o["cTime"])
                        ))
                    return orders
                raise Exception(f"Bitget API Error: {data.get('msg', 'Unknown error')}")

    async def create_order(
        self,
        symbol: str,
        side: str,
        type_: OrderType,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create order"""
        path = "/api/v1/spot/trade/order"
        timestamp = str(int(time.time() * 1000))
        
        data = {
            "symbol": symbol.replace("/", ""),
            "side": side.value,
            "force": type_.value,
            "size": str(quantity),
            "timestamp": timestamp
        }
        
        if price and type_ == OrderType.LIMIT:
            data["price"] = str(price)
            
        if client_order_id:
            data["clientOrderId"] = client_order_id
        
        body = str(data).replace("'", '"')
        signature = self._generate_signature(timestamp, "POST", path, body)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
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
                if result.get("code") == "00000":
                    o = result["data"]
                    return Order(
                        exchange_order_id=o["orderId"],
                        client_order_id=client_order_id or "",
                        symbol=symbol,
                        side=side,
                        type=type_,
                        quantity=quantity,
                        price=price,
                        status=OrderStatus.SUBMITTED,
                        created_at=int(time.time() * 1000)
                    )
                raise Exception(f"Bitget Order Error: {result.get('msg', 'Unknown error')}")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel order"""
        path = "/api/v1/spot/trade/cancelOrder"
        timestamp = str(int(time.time() * 1000))
        
        data = {
            "symbol": symbol.replace("/", ""),
            "orderId": order_id
        }
        
        body = str(data).replace("'", '"')
        signature = self._generate_signature(timestamp, "POST", path, body)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
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
                return result.get("code") == "00000"

    async def get_server_time(self) -> int:
        """Get server time"""
        path = "/api/v1/common/time"
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}{path}") as response:
                data = await response.json()
                if data.get("code") == "00000":
                    return int(data["data"])
                raise Exception("Failed to get server time")

    def _normalize_timeframe(self, tf: str) -> str:
        """Normalize timeframe for Bitget"""
        mapping = {
            "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
            "30m": "30min", "1h": "1H", "2h": "2H", "4h": "4H",
            "6h": "6H", "12h": "12H", "1d": "1D", "1w": "7D", "1M": "1M"
        }
        return mapping.get(tf, "1min")
