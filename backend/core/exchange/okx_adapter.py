"""
OKX Exchange Adapter.

Implements the ExchangeAdapter interface for OKX API.
Supports both spot and testnet.
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import pandas as pd
import aiohttp
import hmac
import base64
import time

from backend.core.exchange.base_adapter import ExchangeAdapter, register_adapter


@register_adapter('okx')
class OKXAdapter(ExchangeAdapter):
    """OKX exchange adapter."""
    
    BASE_URL = "https://www.okx.com"
    TESTNET_BASE_URL = "https://www.okx.com"  # OKX uses same URL with different keys
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, 
                 passphrase: Optional[str] = None, testnet: bool = True):
        super().__init__(api_key, api_secret, testnet)
        self.passphrase = passphrase or ""
        self.base_url = self.TESTNET_BASE_URL if testnet else self.BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self._markets_cache: List[Dict] = []
    
    @property
    def name(self) -> str:
        return "okx"
    
    async def connect(self) -> bool:
        """Initialize HTTP session and verify connection."""
        try:
            self.session = aiohttp.ClientSession()
            
            # Test connection
            await self.get_server_time()
            self.connected = True
            return True
        except Exception as e:
            self.connected = False
            raise
    
    async def disconnect(self):
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None
        self.connected = False
    
    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Generate OKX signature."""
        message = timestamp + method + path + body
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            digestmod='sha256'
        )
        return base64.b64encode(signature.digest()).decode()
    
    def _get_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Get headers with signature."""
        timestamp = datetime.utcnow().isoformat(timespec='milliseconds').split('.')[0] + 'Z'
        
        headers = {
            'OK-ACCESS-KEY': self.api_key or '',
            'OK-ACCESS-SIGN': self._generate_signature(timestamp, method, path, body),
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
        
        return headers
    
    async def get_balance(self) -> Dict[str, float]:
        """Get account balances."""
        if not self.connected:
            raise RuntimeError("Not connected")
        
        # Paper trading mode - return empty
        return {}
    
    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all available markets."""
        if self._markets_cache:
            return self._markets_cache
        
        url = f"{self.base_url}/api/v5/public/instruments"
        params = {'instType': 'SPOT'}
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            if data.get('code') == '0' and 'data' in data:
                self._markets_cache = [
                    inst for inst in data['data']
                    if inst.get('state') == 'live'
                ]
        
        return self._markets_cache
    
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol info."""
        markets = await self.get_markets()
        exchange_symbol = self.normalize_symbol(symbol)
        
        for market in markets:
            if market.get('instId') == exchange_symbol:
                return {
                    'symbol': symbol,
                    'exchange_symbol': exchange_symbol,
                    'base_currency': market.get('baseCcy'),
                    'quote_currency': market.get('quoteCcy'),
                    'price_precision': int(market.get('tickSz', 4)),
                    'quantity_precision': int(market.get('lotSz', 4)),
                    'min_quantity': float(market.get('minSz', 0)),
                    'max_quantity': float(market.get('maxSz', 0)) if market.get('maxSz') else 999999999,
                    'min_notional': float(market.get('minSz', 0)),
                    'tick_size': float(market.get('tickSz', 0)),
                    'step_size': float(market.get('lotSz', 0)),
                    'is_active': market.get('state') == 'live'
                }
        return None
    
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 300,
        since: Optional[int] = None
    ) -> pd.DataFrame:
        """Fetch OHLCV data from OKX."""
        exchange_symbol = self.normalize_symbol(symbol)
        interval = self._normalize_timeframe(timeframe)
        
        url = f"{self.base_url}/api/v5/market/candles"
        params = {
            'instId': exchange_symbol,
            'bar': interval,
            'limit': min(limit, 300)
        }
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            if data.get('code') == '0' and 'data' in data:
                candles = data['data']
                
                df = pd.DataFrame(candles, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'vol_ccy', 'vol_ccy_quote', 'trades', 'ignore1', 'ignore2'
                ])
                
                df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms', utc=True)
                df['open'] = df['open'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                df['close'] = df['close'].astype(float)
                df['volume'] = df['volume'].astype(float)
                
                return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        
        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch current ticker."""
        exchange_symbol = self.normalize_symbol(symbol)
        url = f"{self.base_url}/api/v5/market/ticker"
        params = {'instId': exchange_symbol}
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            if data.get('code') == '0' and data.get('data'):
                ticker = data['data'][0]
                
                return {
                    'symbol': symbol,
                    'last': float(ticker.get('last', 0)),
                    'bid': float(ticker.get('bidPx', 0)),
                    'ask': float(ticker.get('askPx', 0)),
                    'high_24h': float(ticker.get('high24h', 0)),
                    'low_24h': float(ticker.get('low24h', 0)),
                    'volume_24h': float(ticker.get('vol24h', 0)),
                    'change_24h': float(ticker.get('chgUtc24h', 0)),
                    'timestamp': datetime.now(timezone.utc)
                }
        
        return {'symbol': symbol, 'last': 0, 'timestamp': datetime.now(timezone.utc)}
    
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open orders."""
        # Paper trading - return empty
        return []
    
    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Fetch specific order."""
        # Paper trading - return placeholder
        return {'order_id': order_id, 'status': 'UNKNOWN'}
    
    async def create_order(
        self,
        symbol: str,
        side: str,
        type_: str,
        quantity: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create order (paper trading mode)."""
        return {
            'order_id': f"PAPER_{datetime.now().timestamp()}",
            'symbol': symbol,
            'side': side.upper(),
            'type': type_.upper(),
            'quantity': quantity,
            'price': price,
            'status': 'FILLED',
            'filled_quantity': quantity,
            'average_price': price or 0,
            'created_at': datetime.now(timezone.utc)
        }
    
    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancel order."""
        return {'order_id': order_id, 'status': 'CANCELLED'}
    
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """Cancel all orders."""
        return 0
    
    async def fetch_positions(self) -> List[Dict[str, Any]]:
        """Fetch positions (spot = empty)."""
        return []
    
    async def close_position(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> Dict[str, Any]:
        """Close position."""
        return await self.create_order(symbol, side, 'market', quantity)
    
    async def get_server_time(self) -> datetime:
        """Get OKX server time."""
        url = f"{self.base_url}/api/v5/public/time"
        
        async with self.session.get(url) as response:
            data = await response.json()
            if data.get('code') == '0' and data.get('data'):
                ts = int(data['data'][0].get('ts', 0))
                return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        
        return datetime.now(timezone.utc)
    
    def normalize_symbol(self, symbol: str) -> str:
        """Convert BTC/USDT to BTC-USDT."""
        return symbol.replace('/', '-').replace('_', '-')
    
    def denormalize_symbol(self, exchange_symbol: str) -> str:
        """Convert BTC-USDT to BTC/USDT."""
        return exchange_symbol.replace('-', '/')
    
    def _normalize_timeframe(self, tf: str) -> str:
        """Convert timeframe to OKX format."""
        tf = tf.lower().strip()
        
        mapping = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m',
            '30m': '30m', '1h': '1H', '2h': '2H', '4h': '4H',
            '6h': '6H', '12h': '12H', '1d': '1D', '1w': '1W', '1M': '1M'
        }
        
        return mapping.get(tf, tf)
