"""
Exchange Abstraction Layer.

Provides a unified interface for all supported exchanges.
Each exchange adapter implements this interface.

CRITICAL: Strategy logic NEVER goes into adapters.
Adapters only handle:
- API communication
- Data normalization
- Error handling
- Rate limiting
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from datetime import datetime
import pandas as pd


class ExchangeAdapter(ABC):
    """
    Abstract base class for all exchange adapters.
    
    All adapters must implement these methods.
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.connected = False
        self.last_request_time: Optional[datetime] = None
        self.rate_limit_remaining: int = -1
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange name (e.g., 'binance', 'okx')."""
        pass
    
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to exchange API."""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Close connection."""
        pass
    
    @abstractmethod
    async def get_balance(self) -> Dict[str, float]:
        """
        Get account balances.
        
        Returns dict of currency -> available balance.
        """
        pass
    
    @abstractmethod
    async def get_markets(self) -> List[Dict[str, Any]]:
        """
        Get all available markets/symbols.
        
        Returns list of market info dictionaries.
        """
        pass
    
    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed info for a specific symbol.
        
        Includes precision, limits, etc.
        """
        pass
    
    @abstractmethod
    async def fetch_ohlcv(
        self, 
        symbol: str, 
        timeframe: str, 
        limit: int = 500,
        since: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candle data.
        
        Returns DataFrame with columns: timestamp, open, high, low, close, volume
        Timestamps should be in UTC.
        """
        pass
    
    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch current ticker data."""
        pass
    
    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all open orders."""
        pass
    
    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Fetch specific order by ID."""
        pass
    
    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: str,  # 'buy' or 'sell'
        type_: str,  # 'market', 'limit', etc.
        quantity: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a new order.
        
        Returns order confirmation.
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancel an existing order."""
        pass
    
    @abstractmethod
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """
        Cancel all open orders.
        
        Returns number of cancelled orders.
        """
        pass
    
    @abstractmethod
    async def fetch_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions (for futures)."""
        pass
    
    @abstractmethod
    async def close_position(
        self, 
        symbol: str, 
        side: str, 
        quantity: float
    ) -> Dict[str, Any]:
        """Close a position (market order)."""
        pass
    
    @abstractmethod
    async def get_server_time(self) -> datetime:
        """Get exchange server time."""
        pass
    
    def normalize_symbol(self, symbol: str) -> str:
        """
        Convert canonical symbol (BTC/USDT) to exchange-specific format.
        
        Override in subclasses for exchange-specific formatting.
        """
        return symbol.replace('/', '')
    
    def denormalize_symbol(self, exchange_symbol: str) -> str:
        """
        Convert exchange-specific symbol to canonical format (BTC/USDT).
        
        Override in subclasses if needed.
        """
        # Try to detect USDT pairs
        if exchange_symbol.endswith('USDT'):
            base = exchange_symbol[:-4]
            return f"{base}/USDT"
        return exchange_symbol
    
    async def _rate_limit_wait(self):
        """Implement rate limiting."""
        # Basic implementation - override in subclasses
        pass
    
    def _handle_error(self, error: Exception, context: str):
        """Handle API errors consistently."""
        # Log error with context
        raise error


# Registry of available adapters
EXCHANGE_ADAPTERS: Dict[str, type] = {}


def register_adapter(exchange_name: str):
    """Decorator to register an exchange adapter."""
    def decorator(cls):
        EXCHANGE_ADAPTERS[exchange_name.lower()] = cls
        return cls
    return decorator


def get_adapter(exchange_name: str, **kwargs) -> Optional[ExchangeAdapter]:
    """Get an adapter instance by exchange name."""
    adapter_class = EXCHANGE_ADAPTERS.get(exchange_name.lower())
    if adapter_class:
        return adapter_class(**kwargs)
    return None


def list_available_adapters() -> List[str]:
    """List all registered exchange adapters."""
    return list(EXCHANGE_ADAPTERS.keys())
