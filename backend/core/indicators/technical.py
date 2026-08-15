"""
Technical Indicators for ATS-SMT Pro Strategy.

CRITICAL: All indicators must use ONLY closed candles to avoid lookahead bias.
No future data is permitted in any calculation.
"""

import numpy as np
import pandas as pd
from typing import Tuple, Optional
from functools import lru_cache


# =============================================================================
# AVERAGE TRUE RANGE (ATR)
# =============================================================================

def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14
) -> pd.Series:
    """
    Calculate Average True Range (ATR).
    
    Uses Wilder's smoothing method as per original specification.
    
    Parameters:
    -----------
    high : pd.Series
        High prices
    low : pd.Series
        Low prices
    close : pd.Series
        Close prices (for previous close)
    period : int
        ATR period (default: 14)
    
    Returns:
    --------
    pd.Series
        ATR values
    
    Note:
    -----
    ATR[0] cannot be calculated until we have `period` bars.
    This ensures no lookahead bias.
    """
    # Calculate True Range components
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    
    # True Range is the maximum of the three
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Wilder's smoothing: EMA with alpha = 1/period
    atr = true_range.ewm(alpha=1/period, min_periods=period).mean()
    
    return atr


def calculate_atr_percentage(atr: pd.Series, close: pd.Series) -> pd.Series:
    """
    Calculate ATR as percentage of price.
    
    Used for min ATR filter (minAtrPct = 0.3%).
    """
    return (atr / close) * 100


# =============================================================================
# AVERAGE DIRECTIONAL INDEX (ADX)
# =============================================================================

def calculate_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14
) -> pd.Series:
    """
    Calculate Average Directional Index (ADX).
    
    ADX measures trend strength regardless of direction.
    
    Market Regime Classification:
    -----------------------------
    DEAD:   ADX < 15
    RANGE:  15 <= ADX < 25
    TREND:  ADX >= 25
    
    Parameters:
    -----------
    high : pd.Series
        High prices
    low : pd.Series
        Low prices
    close : pd.Series
        Close prices
    period : int
        ADX period (default: 14)
    
    Returns:
    --------
    pd.Series
        ADX values
    """
    # Calculate DM+ and DM-
    plus_dm = high.diff()
    minus_dm = low.diff().abs() * -1
    
    # Apply DM rules
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm.abs() > plus_dm) & (minus_dm < 0), 0).abs()
    
    # Calculate True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Smooth DM and TR using Wilder's method
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / 
                     true_range.ewm(alpha=1/period, min_periods=period).mean())
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / 
                      true_range.ewm(alpha=1/period, min_periods=period).mean())
    
    # Calculate DX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    
    # ADX is smoothed DX
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    
    return adx


# =============================================================================
# BOLLINGER BANDS
# =============================================================================

def calculate_bollinger_bands(
    close: pd.Series,
    period: int = 20,
    stddev: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate Bollinger Bands.
    
    Used for range entry detection (BB bounce strategy).
    
    Parameters:
    -----------
    close : pd.Series
        Close prices
    period : int
        BB period (default: 20)
    stddev : float
        Standard deviation multiplier (default: 2.0)
    
    Returns:
    --------
    Tuple[pd.Series, pd.Series, pd.Series]
        (upper_band, middle_band, lower_band)
    """
    # Middle band = SMA
    middle = close.rolling(window=period, min_periods=period).mean()
    
    # Standard deviation
    std = close.rolling(window=period, min_periods=period).std()
    
    # Upper and lower bands
    upper = middle + (stddev * std)
    lower = middle - (stddev * std)
    
    return upper, middle, lower


# =============================================================================
# VOLUME SMA
# =============================================================================

def calculate_volume_sma(
    volume: pd.Series,
    period: int = 20
) -> pd.Series:
    """
    Calculate Volume Simple Moving Average.
    
    Used for volume vote filter (volume > SMA20 * 1.5).
    """
    return volume.rolling(window=period, min_periods=period).mean()


# =============================================================================
# PIVOT HIGH / PIVOT LOW DETECTION
# =============================================================================

def detect_pivots(
    high: pd.Series,
    low: pd.Series,
    structure_period: int = 20
) -> Tuple[pd.Series, pd.Series]:
    """
    Detect Pivot Highs and Pivot Lows.
    
    CRITICAL: Pivots are confirmed only after `structurePeriod` bars.
    This prevents repainting and lookahead bias.
    
    Pivot High Definition:
    ----------------------
    A bar is a pivot high if its high is the maximum among:
    - N bars to the left (structure_period)
    - N bars to the right (structure_period)
    
    Pivot Low Definition:
    ---------------------
    A bar is a pivot low if its low is the minimum among:
    - N bars to the left (structure_period)
    - N bars to the right (structure_period)
    
    Confirmation Rule:
    ------------------
    A pivot is considered CONFIRMED only after `structure_period` bars
    have passed since the pivot bar. Until then, it is provisional.
    
    Parameters:
    -----------
    high : pd.Series
        High prices
    low : pd.Series
        Low prices
    structure_period : int
        Number of bars on each side (default: 20)
    
    Returns:
    --------
    Tuple[pd.Series, pd.Series]
        (pivot_highs, pivot_lows)
        Values are NaN where no pivot exists
    """
    n = structure_period
    
    # Rolling window for left and right sides
    # We need 2*n + 1 total window (n left, current, n right)
    window = 2 * n + 1
    
    # Pivot High: current bar is max of window
    # We shift by n to ensure we only confirm after n bars have passed
    pivot_high_candidates = high.rolling(window=window, center=True, min_periods=window).max()
    pivot_highs = (high == pivot_high_candidates).astype(float) * high
    pivot_highs = pivot_highs.replace(0, np.nan)
    
    # Shift by n to confirm only after structure_period bars
    pivot_highs = pivot_highs.shift(n)
    
    # Pivot Low: current bar is min of window
    pivot_low_candidates = low.rolling(window=window, center=True, min_periods=window).min()
    pivot_lows = (low == pivot_low_candidates).astype(float) * low
    pivot_lows = pivot_lows.replace(0, np.nan)
    
    # Shift by n to confirm only after structure_period bars
    pivot_lows = pivot_lows.shift(n)
    
    return pivot_highs, pivot_lows


def get_confirmed_pivots(
    high: pd.Series,
    low: pd.Series,
    structure_period: int = 20,
    current_index: Optional[int] = None
) -> Tuple[Optional[float], Optional[float]]:
    """
    Get the most recent confirmed pivot high and low.
    
    This function ensures that only pivots confirmed by passing
    `structure_period` bars are returned.
    
    Parameters:
    -----------
    high : pd.Series
        High prices
    low : pd.Series
        Low prices
    structure_period : int
        Confirmation period
    current_index : Optional[int]
        Current bar index (for real-time use)
    
    Returns:
    --------
    Tuple[Optional[float], Optional[float]]
        (last_confirmed_pivot_high, last_confirmed_pivot_low)
    """
    pivot_highs, pivot_lows = detect_pivots(high, low, structure_period)
    
    # Get last non-NaN values
    if current_index is not None:
        ph_series = pivot_highs.iloc[:current_index + 1]
        pl_series = pivot_lows.iloc[:current_index + 1]
    else:
        ph_series = pivot_highs
        pl_series = pivot_lows
    
    last_pivot_high = ph_series.dropna().iloc[-1] if len(ph_series.dropna()) > 0 else None
    last_pivot_low = pl_series.dropna().iloc[-1] if len(pl_series.dropna()) > 0 else None
    
    return last_pivot_high, last_pivot_low


# =============================================================================
# IMPULSE CALCULATION
# =============================================================================

def calculate_impulse(
    open_: pd.Series,
    close: pd.Series
) -> pd.Series:
    """
    Calculate impulse (momentum) based on candle bodies.
    
    impulse = |close - open| + |previousClose - previousOpen|
    
    Used for impulse filter to ensure sufficient momentum.
    """
    body = abs(close - open_)
    prev_body = body.shift(1)
    
    impulse = body + prev_body
    
    return impulse


# =============================================================================
# BOS CHASE FILTER
# =============================================================================

def calculate_bos_distance(
    close: pd.Series,
    swing_level: float,
    atr: pd.Series
) -> pd.Series:
    """
    Calculate distance from current price to swing level in ATR units.
    
    Used for BOS chase filter:
    - LONG: bosDistL = |close - swingHigh|
    - SHORT: bosDistS = |close - swingLow|
    
    Filter passes if bosDist <= ATR * 0.5
    """
    bos_dist = abs(close - swing_level)
    bos_dist_atr = bos_dist / atr
    
    return bos_dist_atr


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_market_regime(adx_value: float, dead_threshold: float = 15.0, trend_threshold: float = 25.0) -> str:
    """
    Classify market regime based on ADX value.
    
    DEAD:   ADX < 15  - No new entries allowed
    RANGE:  15 <= ADX < 25 - Only BB bounce + CHoCH entries
    TREND:  ADX >= 25 - Normal BOS entries allowed
    """
    if adx_value < dead_threshold:
        return "DEAD"
    elif adx_value < trend_threshold:
        return "RANGE"
    else:
        return "TREND"


def normalize_timeframe(tf: str) -> str:
    """
    Normalize timeframe string to standard format.
    
    Examples:
    - "30m" -> "30m"
    - "4h" -> "4h"
    - "1d" -> "1d"
    - "30" -> "30m"
    - "240" -> "4h"
    """
    tf = tf.lower().strip()
    
    # Already in correct format
    if tf.endswith('m') or tf.endswith('h') or tf.endswith('d'):
        return tf
    
    # Numeric only - assume minutes
    try:
        minutes = int(tf)
        if minutes >= 1440:
            days = minutes // 1440
            return f"{days}d"
        elif minutes >= 60:
            hours = minutes // 60
            return f"{hours}h"
        else:
            return f"{minutes}m"
    except ValueError:
        return tf


@lru_cache(maxsize=128)
def timeframe_to_minutes(tf: str) -> int:
    """
    Convert timeframe string to minutes.
    
    Examples:
    - "30m" -> 30
    - "4h" -> 240
    - "1d" -> 1440
    """
    tf = tf.lower().strip()
    
    if tf.endswith('m'):
        return int(tf[:-1])
    elif tf.endswith('h'):
        return int(tf[:-1]) * 60
    elif tf.endswith('d'):
        return int(tf[:-1]) * 1440
    else:
        # Assume minutes
        return int(tf)
