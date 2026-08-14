"""
BaseStrategy – abstract interface for all trading strategies.
Strategies are plug‑and‑play; new ones are auto‑discovered at runtime.
"""
import abc
import logging
from typing import Dict, Optional

from backend.core.exchange.base import MarketData

logger = logging.getLogger(__name__)


class BaseStrategy(abc.ABC):
    """
    Abstract base class for trading strategies.
    Subclasses must implement the `on_market_data` method.
    """

    def __init__(self, name: str, parameters: Optional[Dict] = None):
        self.name = name
        self.parameters = parameters or {}
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")

    @abc.abstractmethod
    def on_market_data(self, market_data: MarketData) -> Optional[Dict]:
        """
        Process a new market data tick/candle and return a trading signal if any.
        Expected signal dict format:
            {
                "action": "open",          # "open", "close", "adjust"
                "side": "buy" | "sell",
                "symbol": "BTC/USDT",
                "quantity": Decimal,
                "price": Optional[Decimal],
                "metadata": {...}
            }
        """
        pass

    def log_signal(self, signal: Dict):
        self.logger.info("Signal generated: %s", signal)


class StrategyRegistry:
    """
    Discovers and loads strategy plugins from the file system.
    Supports dynamic reloading on file change (simplified – watches at startup).
    """

    def __init__(self, strategies_dir: str):
        self.strategies_dir = strategies_dir
        self.strategies: Dict[str, BaseStrategy] = {}

    def load_strategies(self):
        """
        Scan the strategies directory for Python modules and import them.
        Each module must expose a `Strategy` class that extends BaseStrategy.
        """
        import importlib.util
        import os
        import sys

        if not os.path.isdir(self.strategies_dir):
            logger.warning("Strategies directory %s does not exist", self.strategies_dir)
            return

        for filename in os.listdir(self.strategies_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                module_name = filename[:-3]
                qualified_name = f"ats_strategy_{module_name}"
                module = sys.modules.get(qualified_name)
                if module is None:
                    spec = importlib.util.spec_from_file_location(qualified_name, os.path.join(self.strategies_dir, filename))
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[qualified_name] = module
                else:
                    spec = importlib.util.spec_from_file_location(qualified_name, os.path.join(self.strategies_dir, filename))
                try:
                    spec.loader.exec_module(module)
                except Exception as e:
                    logger.error("Failed to load strategy module %s: %s", filename, e)
                    continue

                # Look for a class named 'Strategy' in the module
                strategy_class = getattr(module, "Strategy", None)
                if strategy_class and issubclass(strategy_class, BaseStrategy):
                    # Instantiate with default parameters (could be extended with config)
                    instance = strategy_class()
                    self.strategies[instance.name] = instance
                    logger.info("Loaded strategy: %s", instance.name)
                else:
                    logger.warning("No 'Strategy' class found in %s", filename)
        logger.info("Total loaded strategies: %d", len(self.strategies))

    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        return self.strategies.get(name)