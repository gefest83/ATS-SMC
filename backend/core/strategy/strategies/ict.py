"""
ICT (Inner Circle Trader) style strategy – EMA crossover filtered by ATR-based
volatility, computed on rolling tick data.
"""
import logging
from collections import deque
from decimal import Decimal
from typing import Dict, Optional

from backend.core.exchange.base import MarketData
from backend.core.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    def __init__(self, parameters: Optional[Dict] = None):
        params = {"ema_short": 9, "ema_long": 21, "atr_period": 14, "min_volatility": 0.0005}
        params.update(parameters or {})
        super().__init__(name="ict", parameters=params)

        self.ema_short: Optional[Decimal] = None
        self.ema_long: Optional[Decimal] = None
        self.atr: Optional[Decimal] = None
        self.last_price: Optional[Decimal] = None
        self.prices: deque = deque(maxlen=params["ema_long"] * 5)
        self.position_side: Optional[str] = None

    @staticmethod
    def _ema(previous: Optional[Decimal], value: Decimal, length: int) -> Decimal:
        alpha = Decimal("2") / Decimal(length + 1)
        return value if previous is None else previous + alpha * (value - previous)

    @staticmethod
    def _decimal(value) -> Decimal:
        return value if isinstance(value, Decimal) else Decimal(str(value))

    def on_market_data(self, data: MarketData) -> Optional[Dict]:
        price = self._decimal(data.price)
        self.prices.append(price)

        self.ema_short = self._ema(self.ema_short, price, self.parameters["ema_short"])
        self.ema_long = self._ema(self.ema_long, price, self.parameters["ema_long"])

        if self.last_price is not None:
            true_range = abs(price - self.last_price)
            self.atr = self._ema(self.atr, true_range, self.parameters["atr_period"])
        self.last_price = price

        if len(self.prices) < self.parameters["ema_long"] or self.atr is None:
            return None

        volatility = self.atr / price
        if volatility < self._decimal(self.parameters["min_volatility"]):
            return None

        side = "buy" if self.ema_short > self.ema_long else "sell"
        if side == self.position_side:
            return None
        self.position_side = side

        stop_distance = self.atr * Decimal("2")
        entry = price
        tp_distance = stop_distance * Decimal("2")
        return {
            "action": "open",
            "side": side,
            "symbol": data.symbol,
            "quantity": None,
            "price": entry,
            "sl_price": entry - stop_distance if side == "buy" else entry + stop_distance,
            "tp_prices": [
                entry + tp_distance if side == "buy" else entry - tp_distance
            ],
            "strategy": self.name,
            "metadata": {
                "ema_short": self.ema_short,
                "ema_long": self.ema_long,
                "atr": self.atr,
                "volatility": volatility,
            },
        }


