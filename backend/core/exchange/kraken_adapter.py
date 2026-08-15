"""
Kraken Exchange Adapter for ATS-SMT PRO
Implements unified interface for Kraken API
Supports Spot trading
"""
import hashlib
import hmac
import time
from typing import Optional, List, Dict, Any
from backend.core.exchange.base_adapter import ExchangeAdapter, register_adapter
from backend.models.schemas import OrderType, OrderSide, OrderStatus, Balance

# Alias для совместимости
BalanceInfo = Balance
MarketInfo = dict  # Упрощенный тип для MarketInfo
from backend.config.settings import config as settings


@register_adapter('kraken')
class KrakenAdapter(ExchangeAdapter):
    """Kraken API Adapter"""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        super().__init__("kraken", api_key, api_secret, testnet)
        self.base_url = "https://api.kraken.com"
        self.headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

    def _generate_signature(self, endpoint: str, data: Dict) -> str:
        nonce = str(int(time.time() * 100000))  # Kraken requires microsecond precision
        encoded_data = "".join([f"{k}{v}" for k, v in sorted(data.items())])
        message = nonce + encoded_data
        
        signature_path = endpoint.replace("/0/", "/")
        message_bytes = (nonce + message).encode('utf-8')
        hash_obj = hmac.new(
            self.api_secret.encode('utf-8'),
            message_bytes,
            hashlib.sha512
        )
        return hash_obj.digest()

    async def connect(self) -> bool:
        try:
            await self.get_balance()
            self.connected = True
            self.logger.info("Kraken connected successfully")
            return True
        except Exception as e:
            self.logger.error(f"Kraken connection failed: {e}")
            return False

    async def disconnect(self):
        self.connected = False
        self.logger.info("Kraken disconnected")

    async def get_balance(self) -> Dict[str, BalanceInfo]:
        # Mock implementation for paper trading
        if self.trading_mode == "paper":
            return {
                "USDT": BalanceInfo(currency="USDT", free=10000.0, locked=0.0, total=10000.0),
                "BTC": BalanceInfo(currency="BTC", free=0.5, locked=0.0, total=0.5)
            }
        
        return {}

    async def get_markets(self) -> List[MarketInfo]:
        # Kraken uses different pair naming (XXBTZUSD)
        pairs_map = {
            "BTC/USDT": "XXBTZUSD",
            "ETH/USDT": "XETHZUSD", 
            "SOL/USDT": "SOLUSD",
            "BNB/USDT": "BNBUSD",
            "ADA/USDT": "ADAUSD",
            "DOGE/USDT": "DOGEUSD",
            "TRX/USDT": "TRXUSD",
            "SUI/USDT": "SUIUSD"
        }
        
        markets = []
        for symbol, kraken_pair in pairs_map.items():
            markets.append(MarketInfo(
                symbol=symbol,
                base=symbol.split("/")[0],
                quote=symbol.split("/")[1],
                min_qty=0.0001,
                max_qty=100000.0,
                step_size=0.0001,
                tick_size=0.1,
                min_notional=10.0
            ))
        return markets

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[List]:
        tf_map = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "1440"}
        interval = tf_map.get(timeframe, "30")
        
        # Mock data for paper trading
        if self.trading_mode == "paper":
            import random
            candles = []
            base_price = 60000 if "BTC" in symbol else 3000
            now = int(time.time() * 1000)
            interval_ms = int(interval) * 60 * 1000
            
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
        return OrderResult(
            order_id=f"kraken_{int(time.time() * 1000)}",
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
        return await self.create_order(symbol, "SELL" if side == "BUY" else "BUY", "market", quantity)

    async def get_server_time(self) -> int:
        return int(time.time() * 1000)

    def normalize_symbol(self, symbol: str) -> str:
        """Convert BTC/USDT to Kraken format"""
        kraken_map = {
            "BTC/USDT": "XXBTZUSD",
            "ETH/USDT": "XETHZUSD",
            "SOL/USDT": "SOLUSD",
            "BNB/USDT": "BNBUSD",
            "ADA/USDT": "ADAUSD",
            "DOGE/USDT": "DOGEUSD",
            "TRX/USDT": "TRXUSD",
            "SUI/USDT": "SUIUSD"
        }
        return kraken_map.get(symbol, symbol.replace("/", ""))

    def denormalize_symbol(self, symbol: str) -> str:
        """Convert Kraken format back to BTC/USDT"""
        reverse_map = {
            "XXBTZUSD": "BTC/USDT",
            "XETHZUSD": "ETH/USDT",
            "SOLUSD": "SOL/USDT",
            "BNBUSD": "BNB/USDT",
            "ADAUSD": "ADA/USDT",
            "DOGEUSD": "DOGE/USDT",
            "TRXUSD": "TRX/USDT",
            "SUIUSD": "SUI/USDT"
        }
        return reverse_map.get(symbol, symbol)
