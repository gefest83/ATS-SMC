"""
Bybit Exchange Adapter.

Implements the ExchangeAdapter interface for Bybit API.
Supports both spot and testnet.
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import pandas as pd
import aiohttp
import hmac
import hashlib
import time

from backend.core.exchange.base_adapter import ExchangeAdapter, register_adapter


@register_adapter('bybit')
class BybitAdapter(ExchangeAdapter):
    """Bybit exchange adapter."""
    
    BASE_URL = "https://api.bybit.com"
    TESTNET_BASE_URL = "https://api-testnet.bybit.com"
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = True):
        super().__init__(api_key, api_secret, testnet)
        self.base_url = self.TESTNET_BASE_URL if testnet else self.BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self._markets_cache: List[Dict] = []
    
    @property
    def name(self) -> str:
        return "bybit"
    
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
    
    def _generate_signature(self, params: Dict[str, Any], timestamp: int, recv_window: int = 5000) -> str:
        """Generate Bybit signature."""
        param_str = f"{timestamp}{self.api_key}{recv_window}"
        return hmac.new(
            self.api_secret.encode('utf-8'),
            param_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def _get_headers(self, method: str, path: str, params: Dict = None) -> Dict[str, str]:
        """Get headers with signature."""
        timestamp = int(time.time() * 1000)
        recv_window = 5000
        
        headers = {
            'X-BAPI-API-KEY': self.api_key or '',
            'X-BAPI-TIMESTAMP': str(timestamp),
            'X-BAPI-RECV-WINDOW': str(recv_window),
        }
        
        if params and self.api_secret:
            signature = self._generate_signature(params, timestamp, recv_window)
            headers['X-BAPI-SIGN'] = signature
        
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
        
        url = f"{self.base_url}/v5/market/instruments-info"
        params = {'category': 'spot'}
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            if data.get('retCode') == 0 and 'result' in data:
                self._markets_cache = data['result'].get('list', [])
        
        return self._markets_cache
    
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol info."""
        markets = await self.get_markets()
        exchange_symbol = self.normalize_symbol(symbol)
        
        for market in markets:
            if market.get('symbol') == exchange_symbol:
                price_filter = next((f for f in market.get('lotSizeFilter', [])), {})
                
                return {
                    'symbol': symbol,
                    'exchange_symbol': exchange_symbol,
                    'base_currency': market.get('baseCoin'),
                    'quote_currency': market.get('quoteCoin'),
                    'price_precision': int(market.get('priceFilter', {}).get('tickSize', 4)),
                    'quantity_precision': int(market.get('lotSizeFilter', {}).get('qtyStep', 4)),
                    'min_quantity': float(market.get('lotSizeFilter', {}).get('minOrderQty', 0)),
                    'max_quantity': float(market.get('lotSizeFilter', {}).get('maxOrderQty', 0)),
                    'min_notional': float(market.get('lotSizeFilter', {}).get('minOrderAmt', 0)),
                    'tick_size': float(market.get('priceFilter', {}).get('tickSize', 0)),
                    'step_size': float(market.get('lotSizeFilter', {}).get('qtyStep', 0)),
                    'is_active': market.get('status') == 'Trading'
                }
        return None
    
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        since: Optional[int] = None
    ) -> pd.DataFrame:
        """Fetch OHLCV data from Bybit."""
        exchange_symbol = self.normalize_symbol(symbol)
        interval = self._normalize_timeframe(timeframe)
        
        url = f"{self.base_url}/v5/market/kline"
        params = {
            'category': 'spot',
            'symbol': exchange_symbol,
            'interval': interval,
            'limit': min(limit, 200)
        }
        
        if since:
            params['start'] = since
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            if data.get('retCode') == 0 and 'result' in data:
                candles = data['result'].get('list', [])
                
                df = pd.DataFrame(candles, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
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
        url = f"{self.base_url}/v5/market/tickers"
        params = {'category': 'spot', 'symbol': exchange_symbol}
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                ticker = data['result']['list'][0]
                
                return {
                    'symbol': symbol,
                    'last': float(ticker.get('lastPrice', 0)),
                    'bid': float(ticker.get('bid1Price', 0)),
                    'ask': float(ticker.get('ask1Price', 0)),
                    'high_24h': float(ticker.get('highPrice24h', 0)),
                    'low_24h': float(ticker.get('lowPrice24h', 0)),
                    'volume_24h': float(ticker.get('volume24h', 0)),
                    'change_24h': float(ticker.get('price24hPcnt', 0)),
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
        """Get Bybit server time."""
        url = f"{self.base_url}/v5/market/time"
        
        async with self.session.get(url) as response:
            data = await response.json()
            if data.get('retCode') == 0 and data.get('result'):
                ts = int(data['result'].get('timeSecond', 0)) * 1000
                return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        
        return datetime.now(timezone.utc)
    
    def normalize_symbol(self, symbol: str) -> str:
        """Convert BTC/USDT to BTCUSDT."""
        return symbol.replace('/', '').replace('_', '')
    
    def denormalize_symbol(self, exchange_symbol: str) -> str:
        """Convert BTCUSDT to BTC/USDT."""
        # Common quote currencies
        for quote in ['USDT', 'USDC', 'BTC', 'ETH']:
            if exchange_symbol.endswith(quote):
                base = exchange_symbol[:-len(quote)]
                return f"{base}/{quote}"
        return exchange_symbol
    
    def _normalize_timeframe(self, tf: str) -> str:
        """Convert timeframe to Bybit format."""
        tf = tf.lower().strip()
        
        mapping = {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15',
            '30m': '30', '1h': '60', '2h': '120', '4h': '240',
            '6h': '360', '12h': '720', '1d': 'D', '1w': 'W', '1M': 'M'
        }
        
        return mapping.get(tf, tf)
