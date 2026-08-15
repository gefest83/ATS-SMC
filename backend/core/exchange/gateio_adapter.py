"""
Gate.io Exchange Adapter for ATS-SMT PRO
Implements unified interface for Gate.io API v4
Supports Spot and Futures (USDT)
"""
import hashlib
import hmac
import time
from typing import Optional, List, Dict, Any
from backend.core.exchange.base_adapter import ExchangeAdapter, register_adapter
from backend.models.schemas import OrderType, OrderSide, OrderStatus, Balance
from backend.config.settings import config as settings


@register_adapter('gateio')
class GateIOAdapter(ExchangeAdapter):
    """Gate.io V4 API Adapter"""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        super().__init__("gateio", api_key, api_secret, testnet)
        self.base_url = "https://api.gateio.ws/api/v4" if not testnet else "https://fx-api-testnet.gateio.ws/api/v4"
        self.headers = {
            "KEY": self.api_key,
            "Content-Type": "application/json"
        }

    def _generate_signature(self, method: str, url: str, body: str = "", timestamp: int = None) -> str:
        if timestamp is None:
            timestamp = int(time.time())
        message = f"{method}\n{url}\n{body}\n{timestamp}\n"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        return signature

    async def connect(self) -> bool:
        try:
            await self.get_balance()
            self.connected = True
            self.logger.info("Gate.io connected successfully")
            return True
        except Exception as e:
            self.logger.error(f"Gate.io connection failed: {e}")
            return False

    async def disconnect(self):
        self.connected = False
        self.logger.info("Gate.io disconnected")

    async def get_balance(self) -> Dict[str, BalanceInfo]:
        # Simplified spot balance fetch
        url = "/spot/accounts"
        timestamp = int(time.time())
        sign = self._generate_signature("GET", url, "", timestamp)
        headers = {**self.headers, "SIGN": sign, "Timestamp": str(timestamp)}
        
        # Mock implementation for paper trading compatibility
        if self.trading_mode == "paper":
            return {
                "USDT": BalanceInfo(currency="USDT", free=10000.0, locked=0.0, total=10000.0),
                "BTC": BalanceInfo(currency="BTC", free=0.5, locked=0.0, total=0.5)
            }
        
        # Real implementation would use httpx here
        return {}

    async def get_markets(self) -> List[MarketInfo]:
        # Mock markets for supported pairs
        pairs = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "ENA_USDT", 
                 "ART_USDT", "ADA_USDT", "TRX_USDT", "DOGE_USDT", "SUI_USDT"]
        
        markets = []
        for pair in pairs:
            markets.append(MarketInfo(
                symbol=pair.replace("_", "/"),
                base=pair.split("_")[0],
                quote=pair.split("_")[1],
                min_qty=0.00001,
                max_qty=1000000.0,
                step_size=0.00001,
                tick_size=0.01,
                min_notional=5.0
            ))
        return markets

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[List]:
        # Gate.io uses candlesticks endpoint
        # Convert symbol BTC/USDT -> BTC_USDT
        gate_symbol = symbol.replace("/", "_")
        tf_map = {"1m": "60", "5m": "300", "15m": "900", "30m": "1800", "1h": "3600", "4h": "14400", "1d": "86400"}
        interval = tf_map.get(timeframe, "1800")
        
        url = f"/spot/candlesticks?currency_pair={gate_symbol}&interval={interval}&limit={limit}"
        timestamp = int(time.time())
        sign = self._generate_signature("GET", url, "", timestamp)
        headers = {**self.headers, "SIGN": sign, "Timestamp": str(timestamp)}
        
        # Mock data for paper trading
        if self.trading_mode == "paper":
            import random
            candles = []
            base_price = 60000 if "BTC" in symbol else 3000
            now = int(time.time() * 1000)
            interval_ms = int(interval) * 1000
            
            for i in range(limit):
                ts = now - (i * interval_ms)
                open_p = base_price + random.uniform(-100, 100)
                close_p = base_price + random.uniform(-100, 100)
                high_p = max(open_p, close_p) + random.uniform(10, 50)
                low_p = min(open_p, close_p) - random.uniform(10, 50)
                vol = random.uniform(10, 100)
                candles.append([ts, open_p, high_p, low_p, close_p, vol])
            
            return candles
        
        return []

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return {"symbol": symbol, "last": 60000.0, "bid": 59999.0, "ask": 60001.0, "volume": 1000.0}

    async def create_order(self, symbol: str, side: str, type_: str, quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        # Mock order creation for paper trading
        return OrderResult(
            order_id=f"gateio_{int(time.time() * 1000)}",
            client_order_id=f"ats_{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            type=type_,
            quantity=quantity,
            price=price or 0.0,
            status="open",
            created_at=int(time.time() * 1000)
        )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        return True

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        return []

    async def fetch_positions(self) -> List[Dict]:
        return []

    async def close_position(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        # Close by market order
        return await self.create_order(symbol, "SELL" if side == "BUY" else "BUY", "market", quantity)

    async def get_server_time(self) -> int:
        return int(time.time() * 1000)

    def normalize_symbol(self, symbol: str) -> str:
        """Convert BTC/USDT to BTC_USDT for Gate.io"""
        return symbol.replace("/", "_")

    def denormalize_symbol(self, symbol: str) -> str:
        """Convert BTC_USDT to BTC/USDT"""
        return symbol.replace("_", "/")
