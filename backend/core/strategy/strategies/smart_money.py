"""
Smart Money strategy – aggregates incoming ticks into candles and runs the
SMC engine (FVG, Order Blocks, BOS/CHOCH) to score setups.
"""
from decimal import Decimal
from typing import Dict, List, Optional

from backend.config import settings
from backend.core.analysis.market_analyzer import MarketAnalyzer
from backend.core.analysis.signal_generator import SignalGenerator
from backend.core.exchange.base import MarketData
from backend.core.strategy.base import BaseStrategy

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


class CandleBuilder:
    """Aggregates tick-level MarketData into OHLCV candles."""

    def __init__(self, interval_seconds: int, max_candles: int = 300):
        self.interval_ms = interval_seconds * 1000
        self.max_candles = max_candles
        self.candles: List[list] = []
        self._current: Optional[list] = None

    def update(self, data: MarketData) -> bool:
        """Returns True when a candle has just closed."""
        # Keep exchange prices and volumes exact throughout candle aggregation.
        price = data.price if isinstance(data.price, Decimal) else Decimal(str(data.price))
        volume = data.volume if isinstance(data.volume, Decimal) else Decimal(str(data.volume))
        bucket = (data.timestamp // self.interval_ms) * self.interval_ms

        if self._current is None:
            self._current = [bucket, price, price, price, price, volume]
            return False

        if bucket > self._current[0]:
            self.candles.append(self._current)
            self.candles = self.candles[-self.max_candles :]
            self._current = [bucket, price, price, price, price, volume]
            return True

        self._current[2] = max(self._current[2], price)
        self._current[3] = min(self._current[3], price)
        self._current[4] = price
        self._current[5] += volume
        return False

    def series(self) -> List[list]:
        return self.candles + ([self._current] if self._current else [])


class Strategy(BaseStrategy):
    def __init__(self, parameters: Optional[Dict] = None):
        params = {
            "timeframe": settings.TIMEFRAME,
            "score_threshold": 4,
            "min_rr": settings.MIN_RR_RATIO,
            "min_candles": 50,
        }
        params.update(parameters or {})
        super().__init__(name="smart_money", parameters=params)

        interval = TIMEFRAME_SECONDS.get(params["timeframe"], 900)
        self.builder = CandleBuilder(interval)
        self.analyzer = MarketAnalyzer(settings.SYMBOL, params["timeframe"])
        self.signal_gen = SignalGenerator(
            min_rr=params["min_rr"], score_threshold=params["score_threshold"]
        )

    def on_market_data(self, data: MarketData) -> Optional[Dict]:
        candle_closed = self.builder.update(data)
        candles = self.builder.series()
        if not candle_closed or len(candles) < self.parameters["min_candles"]:
            return None

        self.analyzer.symbol = data.symbol
        analysis = self.analyzer.analyze(candles)
        side = self.signal_gen.generate_signal(analysis)
        if not side:
            return None

        levels = self.signal_gen.build_levels(analysis, side)
        # Central StrategyManager owns risk and sizing. This strategy only
        # produces the setup; it must not maintain a second independent
        # open-trade counter.
        quantity = None

        return {
            "action": "open",
            "side": side.lower(),
            "symbol": data.symbol,
            "quantity": quantity,
            "price": levels["entry"],
            "sl_price": levels["stop_loss"],
            "tp_prices": [levels["tp1"], levels["tp2"], levels["tp3"]],
            "strategy": self.name,
            "metadata": {
                "score": self.signal_gen.score(analysis),
                "fvg_count": len(analysis["fvgs"]),
                "order_blocks": len(analysis["order_blocks"]),
                "structure": analysis["structure"]["trend"],
            },
        }
