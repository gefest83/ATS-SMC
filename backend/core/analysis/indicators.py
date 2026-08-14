"""Technical indicators (pure pandas/numpy, no external TA dependency)."""
from decimal import Decimal

import pandas as pd

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


def _as_decimal(value) -> Decimal:
    """Convert an exchange scalar without importing binary float digits."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def ohlcv_to_dataframe(ohlcv: list) -> pd.DataFrame:
    """Build a raw OHLCV frame while retaining Decimal price and volume data."""
    df = pd.DataFrame(ohlcv, columns=OHLCV_COLUMNS)
    # Convert milliseconds to timezone-aware UTC datetime.
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for column in PRICE_COLUMNS:
        df[column] = df[column].map(_as_decimal)
    return df.set_index("timestamp")


def indicator_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Create the explicit float boundary used by pandas/numpy indicators."""
    result = df.copy()
    for column in PRICE_COLUMNS:
        result[column] = result[column].map(float)
    return result


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False).mean()


class IndicatorService:
    @staticmethod
    def apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_20"] = ema(df["close"], 20)
        df["ema_50"] = ema(df["close"], 50)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df, 14)
        return df

    @staticmethod
    def calculate_volatility(df: pd.DataFrame) -> float:
        return float(df["atr"].iloc[-1] / df["close"].iloc[-1])
