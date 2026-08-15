"""
Binance Exchange Adapter.

Implements the ExchangeAdapter interface for Binance API.
Supports both spot and testnet.
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import pandas as pd
import aiohttp

from backend.core.exchange.base_adapter import ExchangeAdapter, register_adapter


@register_adapter('binance')
class BinanceAdapter(ExchangeAdapter):
    """Binance exchange adapter using official API."""
    
    SPOT_BASE_URL = "https://api.binance.com"
    TESTNET_BASE_URL = "https://testnet.binance.vision"
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = True):
        super().__init__(api_key, api_secret, testnet)
        self.base_url = self.TESTNET_BASE_URL if testnet else self.SPOT_BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self._markets_cache: List[Dict] = []
    
    @property
    def name(self) -> str:
        return "binance"
    
    async def connect(self) -> bool:
        """Initialize HTTP session and verify connection."""
        try:
            self.session = aiohttp.ClientSession(
                headers={
                    'X-MBX-APIKEY': self.api_key or ''
                }
            )
            
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
    
    async def get_balance(self) -> Dict[str, float]:
        """Get account balances."""
        if not self.connected:
            raise RuntimeError("Not connected")
        
        url = f"{self.base_url}/api/v3/account"
        params = {'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000)}
        
        # Signature would be added here for production
        # For now, return empty dict (paper trading mode)
        return {}
    
    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all available markets."""
        if self._markets_cache:
            return self._markets_cache
        
        url = f"{self.base_url}/api/v3/exchangeInfo"
        
        async with self.session.get(url) as response:
            data = await response.json()
            
            if 'symbols' in data:
                self._markets_cache = [
                    s for s in data['symbols'] 
                    if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT'
                ]
        
        return self._markets_cache
    
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol info."""
        markets = await self.get_markets()
        exchange_symbol = self.normalize_symbol(symbol)
        
        for market in markets:
            if market['symbol'] == exchange_symbol:
                return {
                    'symbol': symbol,
                    'exchange_symbol': exchange_symbol,
                    'base_currency': market['baseAsset'],
                    'quote_currency': market['quoteAsset'],
                    'price_precision': market['quotePrecision'],
                    'quantity_precision': market['baseAssetPrecision'],
                    'min_quantity': float(market['filters'][0].get('minQty', 0)) if market['filters'] else 0,
                    'max_quantity': float(market['filters'][0].get('maxQty', 0)) if market['filters'] else 0,
                    'min_notional': float(market['filters'][0].get('minNotional', 0)) if market['filters'] else 0,
                    'tick_size': float(market['filters'][0].get('tickSize', 0)) if market['filters'] else 0,
                    'step_size': float(market['filters'][0].get('stepSize', 0)) if market['filters'] else 0,
                    'is_active': market['status'] == 'TRADING'
                }
        return None
    
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        since: Optional[int] = None
    ) -> pd.DataFrame:
        """Fetch OHLCV data from Binance."""
        exchange_symbol = self.normalize_symbol(symbol)
        interval = self._normalize_timeframe(timeframe)
        
        url = f"{self.base_url}/api/v3/klines"
        params = {
            'symbol': exchange_symbol,
            'interval': interval,
            'limit': limit
        }
        
        if since:
            params['startTime'] = since
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            if isinstance(data, list):
                df = pd.DataFrame(data, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                    'taker_buy_quote', 'ignore'
                ])
                
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
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
        url = f"{self.base_url}/api/v3/ticker/24hr"
        params = {'symbol': exchange_symbol}
        
        async with self.session.get(url, params=params) as response:
            data = await response.json()
            
            return {
                'symbol': symbol,
                'last': float(data.get('lastPrice', 0)),
                'bid': float(data.get('bidPrice', 0)),
                'ask': float(data.get('askPrice', 0)),
                'high_24h': float(data.get('highPrice', 0)),
                'low_24h': float(data.get('lowPrice', 0)),
                'volume_24h': float(data.get('volume', 0)),
                'change_24h': float(data.get('priceChangePercent', 0)),
                'timestamp': datetime.now(timezone.utc)
            }
    
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
        # In paper mode, simulate order creation
        return {
            'order_id': f"PAPER_{datetime.now().timestamp()}",
            'symbol': symbol,
            'side': side.upper(),
            'type': type_.upper(),
            'quantity': quantity,
            'price': price,
            'status': 'FILLED',  # Simulate instant fill in paper mode
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
        """Get Binance server time."""
        url = f"{self.base_url}/api/v3/time"
        
        async with self.session.get(url) as response:
            data = await response.json()
            return datetime.fromtimestamp(data['serverTime'] / 1000, tz=timezone.utc)
    
    def normalize_symbol(self, symbol: str) -> str:
        """Convert BTC/USDT to BTCUSDT."""
        return symbol.replace('/', '').replace('_', '')
    
    def denormalize_symbol(self, exchange_symbol: str) -> str:
        """Convert BTCUSDT to BTC/USDT."""
        # Common quote currencies
        for quote in ['USDT', 'BUSD', 'USDC', 'BTC', 'ETH', 'BNB']:
            if exchange_symbol.endswith(quote):
                base = exchange_symbol[:-len(quote)]
                return f"{base}/{quote}"
        return exchange_symbol
    
    def _normalize_timeframe(self, tf: str) -> str:
        """Convert timeframe to Binance format."""
        tf = tf.lower().strip()
        
        mapping = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m',
            '30m': '30m', '1h': '1h', '2h': '2h', '4h': '4h',
            '6h': '6h', '8h': '8h', '12h': '12h', '1d': '1d',
            '3d': '3d', '1w': '1w', '1M': '1M'
        }
        
        return mapping.get(tf, tf)
