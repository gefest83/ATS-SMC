"""
Smart Money Concepts engine: Fair Value Gaps, market structure (BOS/CHOCH)
and Order Blocks. Operates on an OHLCV DataFrame.
"""
from decimal import Decimal
from typing import Dict, List

import pandas as pd


def _as_decimal(value) -> Decimal:
    """Normalize a price scalar without a Decimal-to-float round trip."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


class SMCEngine:
    @staticmethod
    def detect_fvg(df: pd.DataFrame) -> List[Dict]:
        """Fair Value Gaps (three-candle imbalance)."""
        fvgs: List[Dict] = []
        for i in range(2, len(df)):
            if df["low"].iloc[i] > df["high"].iloc[i - 2]:
                fvgs.append(
                    {
                        "type": "BULLISH",
                        "top": _as_decimal(df["low"].iloc[i]),
                        "bottom": _as_decimal(df["high"].iloc[i - 2]),
                        "index": df.index[i],
                    }
                )
            elif df["high"].iloc[i] < df["low"].iloc[i - 2]:
                fvgs.append(
                    {
                        "type": "BEARISH",
                        "top": _as_decimal(df["low"].iloc[i - 2]),
                        "bottom": _as_decimal(df["high"].iloc[i]),
                        "index": df.index[i],
                    }
                )
        return fvgs

    @staticmethod
    def swing_points(df: pd.DataFrame, lookback: int = 2) -> Dict[str, List[Dict]]:
        """Fractal swing highs/lows used as structure reference points."""
        highs: List[Dict] = []
        lows: List[Dict] = []
        for i in range(lookback, len(df) - lookback):
            window = slice(i - lookback, i + lookback + 1)
            if df["high"].iloc[i] == df["high"].iloc[window].max():
                highs.append({"index": df.index[i], "price": _as_decimal(df["high"].iloc[i])})
            if df["low"].iloc[i] == df["low"].iloc[window].min():
                lows.append({"index": df.index[i], "price": _as_decimal(df["low"].iloc[i])})
        return {"highs": highs, "lows": lows}

    @staticmethod
    def detect_structure(df: pd.DataFrame, lookback: int = 2) -> Dict[str, List[Dict]]:
        """
        Break of Structure (continuation) and Change of Character (reversal),
        derived from fractal swing points.
        """
        swings = SMCEngine.swing_points(df, lookback)
        structure: Dict[str, List[Dict]] = {"bos": [], "choch": [], "trend": []}
        trend = None

        events = [("high", s) for s in swings["highs"]] + [("low", s) for s in swings["lows"]]
        events.sort(key=lambda item: item[1]["index"])

        last_high = None
        last_low = None
        for kind, swing in events:
            if kind == "high":
                if last_high is not None and swing["price"] > last_high:
                    event = {"type": "BULLISH", "price": swing["price"], "index": swing["index"]}
                    if trend == "BEARISH":
                        structure["choch"].append(event)
                    else:
                        structure["bos"].append(event)
                    trend = "BULLISH"
                last_high = swing["price"] if last_high is None else max(last_high, swing["price"])
            else:
                if last_low is not None and swing["price"] < last_low:
                    event = {"type": "BEARISH", "price": swing["price"], "index": swing["index"]}
                    if trend == "BULLISH":
                        structure["choch"].append(event)
                    else:
                        structure["bos"].append(event)
                    trend = "BEARISH"
                last_low = swing["price"] if last_low is None else min(last_low, swing["price"])

        structure["trend"] = [{"type": trend}] if trend else []
        return structure

    @staticmethod
    def find_order_blocks(df: pd.DataFrame) -> List[Dict]:
        """Last opposing candle before an impulsive move."""
        obs: List[Dict] = []
        for i in range(1, len(df) - 1):
            body_range = (_as_decimal(df["low"].iloc[i]), _as_decimal(df["high"].iloc[i]))
            if df["close"].iloc[i + 1] > df["high"].iloc[i] and df["close"].iloc[i] < df["open"].iloc[i]:
                obs.append({"type": "BULLISH", "price": _as_decimal(df["low"].iloc[i]), "range": body_range})
            elif df["close"].iloc[i + 1] < df["low"].iloc[i] and df["close"].iloc[i] > df["open"].iloc[i]:
                obs.append({"type": "BEARISH", "price": _as_decimal(df["high"].iloc[i]), "range": body_range})
        return obs
