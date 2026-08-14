"""Trend-following strategy with signal gating and risk-derived sizing upstream."""
from decimal import Decimal
from typing import Optional

from backend.core.exchange.base import MarketData
from backend.core.strategy.base import BaseStrategy


class Strategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="trend", parameters={"short_ma": 20, "long_ma": 50, "atr_period": 14, "rr": 2.0})
        self.short_ma = []
        self.long_ma = []
        self.last_signal_side: Optional[str] = None

    def on_market_data(self, data: MarketData):
        price = data.price if isinstance(data.price, Decimal) else Decimal(str(data.price))
        self.short_ma.append(price)
        self.long_ma.append(price)
        self.short_ma = self.short_ma[-self.parameters["short_ma"]:]
        self.long_ma = self.long_ma[-self.parameters["long_ma"]:]
        if len(self.long_ma) < self.parameters["long_ma"]:
            return None

        short = sum(self.short_ma, Decimal("0")) / Decimal(len(self.short_ma))
        long = sum(self.long_ma, Decimal("0")) / Decimal(len(self.long_ma))
        side = "buy" if short > long else "sell"
        if side == self.last_signal_side:
            return None

        window = self.long_ma[-self.parameters["atr_period"]:]
        moves = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
        atr = sum(moves, Decimal("0")) / Decimal(len(moves) or 1)
        if atr <= 0:
            return None
        stop_distance = atr * Decimal("2")
        entry = price
        sl = entry - stop_distance if side == "buy" else entry + stop_distance
        tp = entry + stop_distance * Decimal(str(self.parameters["rr"])) if side == "buy" else entry - stop_distance * Decimal(str(self.parameters["rr"]))
        self.last_signal_side = side
        return {
            "action": "open", "side": side, "symbol": data.symbol,
            "quantity": None, "price": entry, "sl_price": sl, "tp_prices": [tp],
            "strategy": self.name, "metadata": {"sma_short": short, "sma_long": long, "atr": atr},
        }
