"""Combines indicators and the SMC engine into a single market snapshot."""
import math
from decimal import Decimal
from typing import Dict, Optional

from backend.core.analysis.indicators import IndicatorService, indicator_dataframe, ohlcv_to_dataframe
from backend.core.analysis.smc import SMCEngine


class MarketAnalyzer:
    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol
        self.timeframe = timeframe

    def analyze(self, ohlcv: list) -> Optional[Dict]:
        if not ohlcv:
            return None

        raw_df = ohlcv_to_dataframe(ohlcv)
        indicator_df = IndicatorService.apply_indicators(indicator_dataframe(raw_df))
        atr_value = float(indicator_df["atr"].iloc[-1])
        if not math.isfinite(atr_value):
            atr_value = 0.0

        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "df": indicator_df,
            "raw_df": raw_df,
            "fvgs": SMCEngine.detect_fvg(raw_df),
            "structure": SMCEngine.detect_structure(raw_df),
            "order_blocks": SMCEngine.find_order_blocks(raw_df),
            "current_price": raw_df["close"].iloc[-1],
            "atr": Decimal(str(atr_value)),
        }
