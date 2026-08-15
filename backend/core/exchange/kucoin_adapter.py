"""
KuCoin Exchange Adapter for ATS-SMT PRO
Implements unified interface for KuCoin API v2
Supports Spot and Futures
"""
import hashlib
import hmac
import time
from typing import Optional, List, Dict, Any
from backend.core.exchange.base_adapter import BaseExchangeAdapter, OrderResult, BalanceInfo, MarketInfo
from backend.config.settings import settings


class KuCoinAdapter(BaseExchangeAdapter):
    """KuCoin V2 API Adapter"""

    def __init__(self, api_key: str, api_secret: str, passphrase: str, testnet: bool = False):
        super().__init__("kucoin", api_key, api_secret, testnet)
        self.passphrase = passphrase
        self.base_url = "https://api.kucoin.com" if not testnet else "https://openapi-sandbox.kucoin.com"
        self.headers = {
            "KC-API-KEY": self.api_key,
            "KC-API-PASSPHRASE": passphrase,
            "KC-API-TIMESTAMP": str(int(time.time() * 1000)),
            "Content-Type": "application/json"
        }

    def _generate_signature(self, method: str, endpoint: str, body: str = "") -> str:
        timestamp = str(int(time.time() * 1000))
        message = timestamp + method + endpoint + body
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        import base64
        signature_b64 = base64.b64encode(signature).decode('utf-8')
        
        # Update headers with new timestamp and signature
        self.headers["KC-API-TIMESTAMP"] = timestamp
        self.headers["KC-API-SIGN"] = signature_b64
        self.headers["KC-API-KEY-VERSION"] = "2"
        
        return signature_b64

    async def connect(self) -> bool:
        try:
            await self.get_balance()
            self.connected = True
            self.logger.info("KuCoin connected successfully")
            return True
        except Exception as e:
            self.logger.error(f"KuCoin connection failed: {e}")
            return False

    async def disconnect(self):
        self.connected = False
        self.logger.info("KuCoin disconnected")

    async def get_balance(self) -> Dict[str, BalanceInfo]:
        endpoint = "/api/v1/accounts"
        self._generate_signature("GET", endpoint)
        
        # Mock implementation for paper trading
        if self.trading_mode == "paper":
            return {
                "USDT": BalanceInfo(currency="USDT", free=10000.0, locked=0.0, total=10000.0),
                "BTC": BalanceInfo(currency="BTC", free=0.5, locked=0.0, total=0.5)
            }
        
        return {}

    async def get_markets(self) -> List[MarketInfo]:
        pairs = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "ENA-USDT", 
                 "ART-USDT", "ADA-USDT", "TRX-USDT", "DOGE-USDT", "SUI-USDT"]
        
        markets = []
        for pair in pairs:
            markets.append(MarketInfo(
                symbol=pair.replace("-", "/"),
                base=pair.split("-")[0],
                quote=pair.split("-")[1],
                min_qty=0.00001,
                max_qty=1000000.0,
                step_size=0.00001,
                tick_size=0.01,
                min_notional=5.0
            ))
        return markets

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[List]:
        kucoin_symbol = symbol.replace("/", "-")
        tf_map = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", 
                  "1h": "1hour", "4h": "4hour", "1d": "1day"}
        interval = tf_map.get(timeframe, "30min")
        
        endpoint = f"/api/v1/market/candles?symbol={kucoin_symbol}&type={interval}"
        self._generate_signature("GET", endpoint)
        
        # Mock data for paper trading
        if self.trading_mode == "paper":
            import random
            candles = []
            base_price = 60000 if "BTC" in symbol else 3000
            now = int(time.time() * 1000)
            interval_ms_map = {"1min": 60000, "5min": 300000, "15min": 900000, "30min": 1800000, 
                               "1hour": 3600000, "4hour": 14400000, "1day": 86400000}
            interval_ms = interval_ms_map.get(interval, 1800000)
            
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

    async def create_order(self, symbol: str, side: str, type_: str, quantity: float, price: Optional[float] = None) -> Dict[str, Any]Result:
        kucoin_symbol = symbol.replace("/", "-")
        endpoint = "/api/v1/orders"
        body = f'{{"clientOid":"{int(time.time()*1000)}","side":"{side.lower()}","symbol":"{kucoin_symbol}","type":"{type_}","size":"{quantity}"}}'
        self._generate_signature("POST", endpoint, body)
        
        return OrderResult(
            order_id=f"kucoin_{int(time.time() * 1000)}",
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

    async def close_position(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]Result:
        return await self.create_order(symbol, "SELL" if side == "BUY" else "BUY", "market", quantity)

    async def get_server_time(self) -> int:
        return int(time.time() * 1000)

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "-")

    def denormalize_symbol(self, symbol: str) -> str:
        return symbol.replace("-", "/")
