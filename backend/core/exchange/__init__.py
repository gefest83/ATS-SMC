"""
Exchange Adapters Package
"""
from backend.core.exchange.base_adapter import ExchangeAdapter
from backend.core.exchange.binance_adapter import BinanceAdapter
from backend.core.exchange.okx_adapter import OKXAdapter
from backend.core.exchange.bybit_adapter import BybitAdapter
from backend.core.exchange.mexc_adapter import MEXCAdapter
from backend.core.exchange.htx_adapter import HTXAdapter
from backend.core.exchange.bingx_adapter import BingXAdapter
from backend.core.exchange.bitget_adapter import BitgetAdapter
from backend.core.exchange.gateio_adapter import GateIOAdapter
from backend.core.exchange.kucoin_adapter import KuCoinAdapter
from backend.core.exchange.kraken_adapter import KrakenAdapter

__all__ = [
    "ExchangeAdapter",
    "BinanceAdapter",
    "OKXAdapter",
    "BybitAdapter",
    "MEXCAdapter",
    "HTXAdapter",
    "BingXAdapter",
    "BitgetAdapter",
    "GateIOAdapter",
    "KuCoinAdapter",
    "KrakenAdapter",
]


class ExchangeRegistry:
    """Реестр всех доступных биржевых адаптеров"""
    
    _adapters = {
        "binance": BinanceAdapter,
        "okx": OKXAdapter,
        "bybit": BybitAdapter,
        "mexc": MEXCAdapter,
        "htx": HTXAdapter,
        "bingx": BingXAdapter,
        "bitget": BitgetAdapter,
        "gateio": GateIOAdapter,
        "kucoin": KuCoinAdapter,
        "kraken": KrakenAdapter,
    }
    
    @classmethod
    def get_adapter(cls, exchange_name: str):
        """Получить класс адаптера по имени биржи"""
        adapter_class = cls._adapters.get(exchange_name.lower())
        if not adapter_class:
            raise ValueError(f"Unknown exchange: {exchange_name}. Available: {list(cls._adapters.keys())}")
        return adapter_class
    
    @classmethod
    def list_exchanges(cls):
        """Вернуть список всех поддерживаемых бирж"""
        return list(cls._adapters.keys())