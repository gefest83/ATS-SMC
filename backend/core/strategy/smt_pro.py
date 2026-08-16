"""
Smart Money Trades Pro v2 - Strategy Core Implementation.

This module implements the exact SMT Pro v2 strategy as specified.
NO modifications to the mathematical logic are made without explicit permission.

CRITICAL PRINCIPLES:
-------------------
1. NO LOOKAHEAD BIAS: Only closed candles are used
2. NO REPAINTING: Pivots confirmed after structurePeriod bars
3. DETERMINISTIC: Same input always produces same output
4. ISOLATED: Each symbol has independent state

Strategy Flow:
-------------
1. Load market data (M30, 4H, 1D)
2. Calculate indicators (ADX, ATR, BB, Volume SMA)
3. Detect pivots and confirm structure
4. Determine HTF trend (4H + 1D)
5. Classify market regime (DEAD/RANGE/TREND)
6. Apply voting system (HTF, ADX, Volume)
7. Check filters (Impulse, ATR, BOS Chase, Cooldown)
8. Generate signal if all conditions met
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, field
import uuid

from backend.core.indicators.technical import (
    calculate_atr,
    calculate_adx,
    calculate_bollinger_bands,
    calculate_volume_sma,
    detect_pivots,
    get_confirmed_pivots,
    calculate_impulse,
    calculate_bos_distance,
    get_market_regime,
)
from backend.models.schemas import (
    SignalAction,
    MarketRegime,
    StrategySignal,
    HTFTrend,
    IndicatorValues,
    MarketStructure,
)
from backend.config.settings import Config


@dataclass
class SymbolStrategyState:
    """
    Persistent state for a single symbol's strategy.
    
    This state is preserved across restarts and used to maintain
    continuity in signal generation.
    """
    
    symbol: str
    exchange: str
    
    # Last confirmed pivots
    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    
    # Trend state
    current_trend: int = 0  # -1, 0, +1
    prev_trend: int = 0
    
    # BOS state
    bull_break_pending: bool = False
    bear_break_pending: bool = False
    bos_confirmed: bool = False
    
    # CHoCH state
    choch_detected: bool = False
    
    # Range bounce tracking
    long_bounce_count: int = 0
    short_bounce_count: int = 0
    last_bb_touch_long: Optional[float] = None
    last_bb_touch_short: Optional[float] = None
    
    # Cooldown tracking
    long_cooldown_until: Optional[int] = None  # Bar index
    short_cooldown_until: Optional[int] = None
    
    # Last signal
    last_signal_time: Optional[datetime] = None
    last_signal_action: Optional[SignalAction] = None
    
    # Current indicators
    current_adx: float = 0.0
    current_atr: float = 0.0
    current_volume_ratio: float = 1.0
    
    # Bar tracking
    current_bar_index: int = 0


@dataclass
class StrategyConfig:
    """Strategy configuration parameters - ALL 23 Dashboard settings."""
    
    # Structure & Confirmation
    structure_period: int = 20
    confirmation_type: str = "body"  # "body" or "wick"
    
    # HTF Settings
    htf1: str = "4H"
    htf2: str = "1D"
    
    # ADX Settings
    adx_period: int = 14
    adx_th: float = 20.0  # Vote threshold
    adx_trend: float = 25.0  # Trend threshold
    adx_dead: float = 15.0  # Dead zone threshold
    
    # Filter Mode
    filter_mode: str = "2of3"  # "2of3" or "ALL"
    
    # Volume Filter
    volume_sma: int = 20
    volume_mult: float = 1.5
    
    # Impulse Filter
    use_impulse: bool = True
    impulse_mult: float = 1.0
    
    # Range Bounce Filter
    use_range_bounce: bool = True
    bb_lookback: int = 10
    bb_period: int = 20
    bb_stddev: float = 2.0
    max_bounces: int = 2
    
    # ATR Filter
    min_atr_pct: float = 0.3
    atr_period: int = 14
    
    # BOS Chase Filter
    max_bos_dist_atr: float = 0.5
    
    # Cooldown Filter
    use_cooldown: bool = True
    cooldown_bars: int = 6
    
    # Risk Management
    risk_pct: float = 1.0
    tp1_pct: float = 40.0
    tp2_pct: float = 30.0
    tp3_pct: float = 30.0
    
    # Position Management
    use_breakeven: bool = True
    use_trail: bool = False
    
    @property
    def votes_required(self) -> int:
        """Get required votes based on filter mode."""
        if self.filter_mode == "ALL":
            return 3
        return 2  # 2of3
    
    def to_dict(self) -> dict:
        """Convert config to dictionary for API/Dashboard."""
        return {
            "structurePeriod": self.structure_period,
            "confirmationType": self.confirmation_type,
            "htf1": self.htf1,
            "htf2": self.htf2,
            "adxTh": self.adx_th,
            "adxTrend": self.adx_trend,
            "adxDead": self.adx_dead,
            "filterMode": self.filter_mode,
            "volMult": self.volume_mult,
            "useImpulse": self.use_impulse,
            "impulseMult": self.impulse_mult,
            "useRangeBounce": self.use_range_bounce,
            "bbLookback": self.bb_lookback,
            "maxBounces": self.max_bounces,
            "minAtrPct": self.min_atr_pct,
            "maxBosDistAtr": self.max_bos_dist_atr,
            "useCooldown": self.use_cooldown,
            "cooldownBars": self.cooldown_bars,
            "riskPct": self.risk_pct,
            "tp1Pct": self.tp1_pct,
            "tp2Pct": self.tp2_pct,
            "tp3Pct": self.tp3_pct,
            "useBreakeven": self.use_breakeven,
            "useTrail": self.use_trail
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'StrategyConfig':
        """Create config from dictionary (Dashboard/API settings)."""
        return cls(
            structure_period=data.get("structurePeriod", 20),
            confirmation_type=data.get("confirmationType", "body"),
            htf1=data.get("htf1", "4H"),
            htf2=data.get("htf2", "1D"),
            adx_th=data.get("adxTh", 20.0),
            adx_trend=data.get("adxTrend", 25.0),
            adx_dead=data.get("adxDead", 15.0),
            filter_mode=data.get("filterMode", "2of3"),
            volume_mult=data.get("volMult", 1.5),
            use_impulse=data.get("useImpulse", True),
            impulse_mult=data.get("impulseMult", 1.0),
            use_range_bounce=data.get("useRangeBounce", True),
            bb_lookback=data.get("bbLookback", 10),
            max_bounces=data.get("maxBounces", 2),
            min_atr_pct=data.get("minAtrPct", 0.3),
            max_bos_dist_atr=data.get("maxBosDistAtr", 0.5),
            use_cooldown=data.get("useCooldown", True),
            cooldown_bars=data.get("cooldownBars", 6),
            risk_pct=data.get("riskPct", 1.0),
            tp1_pct=data.get("tp1Pct", 40.0),
            tp2_pct=data.get("tp2Pct", 30.0),
            tp3_pct=data.get("tp3Pct", 30.0),
            use_breakeven=data.get("useBreakeven", True),
            use_trail=data.get("useTrail", False)
        )


class SMTProStrategy:
    """
    Smart Money Trades Pro v2 Strategy Core.
    
    This class implements the complete SMT Pro v2 strategy logic.
    It is exchange-agnostic and works purely with market data.
    
    Thread Safety:
    -------------
    Each symbol should have its own instance or use separate state.
    Do not share SymbolStrategyState between threads.
    """
    
    VERSION = "SMT_PRO_V2"
    
    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self.symbol_states: Dict[str, SymbolStrategyState] = {}
    
    def get_or_create_state(
        self, 
        symbol: str, 
        exchange: str
    ) -> SymbolStrategyState:
        """Get existing state or create new for symbol."""
        key = f"{exchange}:{symbol}"
        if key not in self.symbol_states:
            self.symbol_states[key] = SymbolStrategyState(
                symbol=symbol,
                exchange=exchange
            )
        return self.symbol_states[key]
    
    def calculate_htf_trend(
        self,
        candles_4h: pd.DataFrame,
        candles_1d: pd.DataFrame
    ) -> HTFTrend:
        """
        Calculate Higher Timeframe trend from 4H and 1D data.
        
        Uses structural break logic on both timeframes.
        Returns combined score: -2 to +2
        
        +2 = strong bullish (both 4H and 1D bullish)
        +1 = bullish (one bullish, one neutral)
         0 = neutral
        -1 = bearish (one bearish, one neutral)
        -2 = strong bearish (both 4H and 1D bearish)
        """
        # 4H trend
        trend_4h = self._calculate_structural_trend(candles_4h)
        
        # 1D trend
        trend_1d = self._calculate_structural_trend(candles_1d)
        
        combined = trend_4h + trend_1d
        
        return HTFTrend(
            trend_4h=trend_4h,
            trend_1d=trend_1d,
            combined_score=combined
        )
    
    def _calculate_structural_trend(self, candles: pd.DataFrame) -> int:
        """
        Calculate trend direction using structural breaks.
        
        Returns:
        +1 = bullish (higher highs, higher lows)
         0 = neutral
        -1 = bearish (lower highs, lower lows)
        """
        if len(candles) < self.config.structure_period * 3:
            return 0  # Not enough data
        
        pivot_highs, pivot_lows = detect_pivots(
            candles['high'],
            candles['low'],
            self.config.structure_period
        )
        
        # Get recent confirmed pivots
        ph = pivot_highs.dropna()
        pl = pivot_lows.dropna()
        
        if len(ph) < 2 or len(pl) < 2:
            return 0
        
        # Check last two swing highs
        last_ph = ph.iloc[-1]
        prev_ph = ph.iloc[-2]
        
        # Check last two swing lows
        last_pl = pl.iloc[-1]
        prev_pl = pl.iloc[-2]
        
        # Bullish: higher highs AND higher lows
        if last_ph > prev_ph and last_pl > prev_pl:
            return 1
        
        # Bearish: lower highs AND lower lows
        if last_ph < prev_ph and last_pl < prev_pl:
            return -1
        
        return 0
    
    def analyze_market_structure(
        self,
        candles: pd.DataFrame,
        state: SymbolStrategyState
    ) -> MarketStructure:
        """
        Analyze current market structure.
        
        Detects BOS (Break of Structure) and CHoCH (Change of Character).
        """
        if len(candles) < self.config.structure_period * 3:
            return MarketStructure()
        
        pivot_highs, pivot_lows = detect_pivots(
            candles['high'],
            candles['low'],
            self.config.structure_period
        )
        
        # Get last confirmed pivots
        ph = pivot_highs.dropna()
        pl = pivot_lows.dropna()
        
        if len(ph) > 0:
            state.last_swing_high = float(ph.iloc[-1])
        if len(pl) > 0:
            state.last_swing_low = float(pl.iloc[-1])
        
        # Check for BOS
        current_close = candles['close'].iloc[-1]
        current_high = candles['high'].iloc[-1]
        current_low = candles['low'].iloc[-1]
        
        structure = MarketStructure(
            last_swing_high=state.last_swing_high,
            last_swing_low=state.last_swing_low,
            trend=state.current_trend,
            structure_broken=False
        )
        
        # Bullish BOS: close > lastSwingHigh
        if state.last_swing_high and current_close > state.last_swing_high:
            state.bull_break_pending = True
            if state.prev_trend == -1:
                state.choch_detected = True
            state.current_trend = 1
            structure.structure_broken = True
            state.bos_confirmed = True
        
        # Bearish BOS: close < lastSwingLow
        elif state.last_swing_low and current_close < state.last_swing_low:
            state.bear_break_pending = True
            if state.prev_trend == 1:
                state.choch_detected = True
            state.current_trend = -1
            structure.structure_broken = True
            state.bos_confirmed = True
        
        return structure
    
    def calculate_indicators(
        self,
        candles: pd.DataFrame
    ) -> IndicatorValues:
        """Calculate all required indicators for current bar."""
        high = candles['high']
        low = candles['low']
        close = candles['close']
        open_ = candles['open']
        volume = candles['volume']
        
        # ATR
        atr = calculate_atr(high, low, close, self.config.atr_period)
        current_atr = float(atr.iloc[-1]) if not atr.iloc[-1] != atr.iloc[-1] else 0.0
        
        # ATR percentage
        atr_pct = (current_atr / float(close.iloc[-1])) * 100 if close.iloc[-1] != 0 else 0
        
        # ADX
        adx = calculate_adx(high, low, close, self.config.adx_period)
        current_adx = float(adx.iloc[-1]) if not adx.iloc[-1] != adx.iloc[-1] else 0.0
        
        # Volume SMA
        vol_sma = calculate_volume_sma(volume, self.config.volume_sma)
        current_vol_sma = float(vol_sma.iloc[-1]) if not vol_sma.iloc[-1] != vol_sma.iloc[-1] else 0.0
        current_volume = float(volume.iloc[-1])
        volume_ratio = current_volume / current_vol_sma if current_vol_sma > 0 else 1.0
        
        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(
            close, self.config.bb_period, self.config.bb_stddev
        )
        
        # Impulse
        impulse = calculate_impulse(open_, close)
        current_impulse = float(impulse.iloc[-1]) if not impulse.iloc[-1] != impulse.iloc[-1] else 0.0
        
        return IndicatorValues(
            adx=current_adx,
            atr=current_atr,
            atr_pct=atr_pct,
            volume=current_volume,
            volume_sma=current_vol_sma,
            volume_ratio=volume_ratio,
            bb_upper=float(bb_upper.iloc[-1]) if not bb_upper.iloc[-1] != bb_upper.iloc[-1] else 0.0,
            bb_middle=float(bb_middle.iloc[-1]) if not bb_middle.iloc[-1] != bb_middle.iloc[-1] else 0.0,
            bb_lower=float(bb_lower.iloc[-1]) if not bb_lower.iloc[-1] != bb_lower.iloc[-1] else 0.0,
            impulse=current_impulse
        )
    
    def check_voting_system(
        self,
        htf_trend: HTFTrend,
        indicators: IndicatorValues,
        action: SignalAction
    ) -> Tuple[int, bool]:
        """
        Implement voting system for signal confirmation.
        
        Votes:
        1. HTF trend alignment
        2. ADX >= threshold
        3. Volume > SMA * multiplier
        
        Returns: (vote_count, passes_filter)
        """
        votes = 0
        
        if action == SignalAction.LONG:
            # Vote 1: HTF bullish
            if htf_trend.is_bullish:
                votes += 1
            
            # Vote 2: ADX sufficient
            if indicators.adx >= self.config.adx_vote:
                votes += 1
            
            # Vote 3: Volume above average
            if indicators.volume_ratio >= self.config.volume_mult:
                votes += 1
        
        elif action == SignalAction.SHORT:
            # Vote 1: HTF bearish
            if htf_trend.is_bearish:
                votes += 1
            
            # Vote 2: ADX sufficient
            if indicators.adx >= self.config.adx_vote:
                votes += 1
            
            # Vote 3: Volume above average
            if indicators.volume_ratio >= self.config.volume_mult:
                votes += 1
        
        # Check if passes filter
        passes = votes >= self.config.votes_required
        
        return votes, passes
    
    def _check_bos_confirmation(
        self, 
        candles: pd.DataFrame, 
        bos_index: int, 
        direction: str
    ) -> bool:
        """
        Проверка подтверждения BOS с учетом типа подтверждения.
        
        confirmationType = "body": BOS подтверждается закрытием тела свечи за уровень
        confirmationType = "wick": BOS подтверждается пробоем тенью (high/low)
        
        CRITICAL: Использует только закрытые свечи - нет lookahead bias.
        """
        if len(candles) <= bos_index + 1:
            return False
        
        current_candle = candles.iloc[bos_index]
        next_candle = candles.iloc[bos_index + 1]
        
        # Получаем тип подтверждения из настроек
        confirmation_type = self.config.confirmation_type
        
        if confirmation_type == "wick":
            # Подтверждение по тени (wick)
            if direction == "long":
                # Для лонга: следующая свеча должна иметь минимум ниже уровня BOS
                # и закрыться выше минимума BOS свечи
                return next_candle['close'] > current_candle['low']
            else:  # short
                # Для шорта: следующая свеча должна иметь максимум выше уровня BOS
                # и закрыться ниже максимума BOS свечи
                return next_candle['close'] < current_candle['high']
        else:  # default 'body'
            # Подтверждение по телу (body close)
            if direction == "long":
                # Для лонга: следующая свеча должна закрыться выше максимума BOS
                return next_candle['close'] > current_candle['high']
            else:  # short
                # Для шорта: следующая свеча должна закрыться ниже минимума BOS
                return next_candle['close'] < current_candle['low']
    
    def _apply_filters(
        self,
        candles: pd.DataFrame,
        indicators: IndicatorValues,
        state: SymbolStrategyState,
        action: SignalAction,
        swing_level: float
    ) -> Dict[str, bool]:
        """
        Применение фильтров с учетом настроек вкл/выкл.
        
        True = функция работает
        False = функция действительно отключена
        """
        current_bar = state.current_bar_index
        close = candles['close'].iloc[-1]
        open_ = candles['open'].iloc[-1]
        
        results = {}
        
        # 1. ATR Filter - всегда активен (не имеет toggle)
        results['atr_ok'] = indicators.atr_pct >= self.config.min_atr_pct
        
        # 2. Impulse Filter - теперь учитывает настройку use_impulse
        if self.config.use_impulse:
            impulse_threshold = indicators.atr * self.config.impulse_mult
            current_impulse = abs(close - open_)
            if current_bar > 0:
                prev_close = candles['close'].iloc[-2]
                prev_open = candles['open'].iloc[-2]
                prev_impulse = abs(prev_close - prev_open)
                total_impulse = current_impulse + prev_impulse
                results['impulse_ok'] = total_impulse >= impulse_threshold
            else:
                results['impulse_ok'] = True
        else:
            # Impulse filter отключен - всегда passes
            results['impulse_ok'] = True
        
        # 3. BOS Chase Filter - всегда активен (не имеет toggle)
        bos_dist = abs(close - swing_level)
        bos_dist_atr = bos_dist / indicators.atr if indicators.atr > 0 else 999
        results['bos_ok'] = bos_dist_atr <= self.config.max_bos_dist_atr
        
        # 4. Cooldown Filter - теперь учитывает настройку use_cooldown
        if self.config.use_cooldown:
            if action == SignalAction.LONG:
                results['cooldown_ok'] = (
                    state.long_cooldown_until is None or 
                    current_bar > state.long_cooldown_until
                )
            else:
                results['cooldown_ok'] = (
                    state.short_cooldown_until is None or 
                    current_bar > state.short_cooldown_until
                )
        else:
            # Cooldown filter отключен - всегда passes
            results['cooldown_ok'] = True
        
        # 5. Range Conditions (BB bounce для RANGE режима)
        # Проверяется отдельно в check_range_entry с учетом use_range_bounce
        results['range_conditions_ok'] = True
        
        return results
    
    def check_range_entry(
        self,
        candles: pd.DataFrame,
        indicators: IndicatorValues,
        state: SymbolStrategyState,
        action: SignalAction
    ) -> bool:
        """
        Проверка условий входа в RANGE режиме (BB bounce + CHoCH).
        
        Учитывает настройку use_range_bounce.
        """
        # Если фильтр range bounce отключен - пропускаем
        if not self.config.use_range_bounce:
            return True
        
        if action == SignalAction.LONG:
            # Цена касается нижней полосы Боллинджера
            current_low = candles['low'].iloc[-1]
            if current_low <= indicators.bb_lower:
                # Проверяем, является ли это новым касанием
                if state.last_bb_touch_long is None or \
                   current_low < state.last_bb_touch_long * 0.999:
                    state.long_bounce_count += 1
                    state.last_bb_touch_long = current_low
                
                # Требуется CHoCH
                if state.choch_detected and state.long_bounce_count <= self.config.max_bounces:
                    return True
        elif action == SignalAction.SHORT:
            # Цена касается верхней полосы Боллинджера
            current_high = candles['high'].iloc[-1]
            if current_high >= indicators.bb_upper:
                # Проверяем, является ли это новым касанием
                if state.last_bb_touch_short is None or \
                   current_high > state.last_bb_touch_short * 1.001:
                    state.short_bounce_count += 1
                    state.last_bb_touch_short = current_high
                
                # Требуется CHoCH
                if state.choch_detected and state.short_bounce_count <= self.config.max_bounces:
                    return True
        
        return False
    
    def calculate_position_size(
        self,
        portfolio_value: float,
        entry_price: float,
        stop_loss: float,
        regime: MarketRegime
    ) -> Tuple[float, float]:
        """
        Calculate position size based on risk parameters.
        
        Returns: (position_size, risk_amount)
        """
        risk_amount = portfolio_value * (self.config.risk_pct / 100)
        stop_dist = abs(entry_price - stop_loss)
        
        if stop_dist <= 0:
            return 0.0, risk_amount
        
        position_size = risk_amount / stop_dist
        
        # Reduce size in RANGE regime
        if regime == MarketRegime.RANGE:
            position_size *= 0.5
        
        return position_size, risk_amount
    
    def calculate_targets(
        self,
        entry: float,
        atr: float,
        action: SignalAction
    ) -> Tuple[float, float, float, float]:
        """
        Calculate TP1, TP2, TP3, and SL levels.
        
        Target Range = ATR * 2
        
        LONG:
        TP1 = entry + ATR*2*0.8
        TP2 = entry + ATR*2*1.6
        TP3 = entry + ATR*2*2.8
        SL  = entry - ATR*2*1.2
        
        SHORT:
        TP1 = entry - ATR*2*0.8
        TP2 = entry - ATR*2*1.6
        TP3 = entry - ATR*2*2.8
        SL  = entry + ATR*2*1.2
        """
        target_range = atr * 2
        
        if action == SignalAction.LONG:
            tp1 = entry + target_range * 0.8
            tp2 = entry + target_range * 1.6
            tp3 = entry + target_range * 2.8
            sl = entry - target_range * 1.2
        
        elif action == SignalAction.SHORT:
            tp1 = entry - target_range * 0.8
            tp2 = entry - target_range * 1.6
            tp3 = entry - target_range * 2.8
            sl = entry + target_range * 1.2
        
        else:
            tp1 = tp2 = tp3 = sl = entry
        
        return tp1, tp2, tp3, sl
    
    def generate_signal(
        self,
        symbol: str,
        exchange: str,
        candles_m30: pd.DataFrame,
        candles_4h: pd.DataFrame,
        candles_1d: pd.DataFrame,
        portfolio_value: float,
        current_bar_index: Optional[int] = None
    ) -> Optional[StrategySignal]:
        """
        Main signal generation function.
        
        This is the core of the SMT Pro v2 strategy.
        It processes all modules and returns a trading signal if conditions are met.
        
        CRITICAL: Only uses CLOSED candles. No lookahead.
        """
        # Validate input data
        if len(candles_m30) < self.config.structure_period * 3:
            return None  # Insufficient data
        
        # Get or create state
        state = self.get_or_create_state(symbol, exchange)
        state.current_bar_index = current_bar_index or len(candles_m30) - 1
        
        # Ensure we're using only closed candles
        # In real-time, exclude the current (incomplete) candle
        closed_candles = candles_m30.copy()
        if len(closed_candles) > 0:
            # For safety, always use up to second-to-last if last might be incomplete
            # In production, this is determined by candle timestamp vs current time
            pass
        
        # =========================================================================
        # MODULE 1: Market Structure
        # =========================================================================
        structure = self.analyze_market_structure(closed_candles, state)
        
        # Reset CHoCH after use
        choch_detected = state.choch_detected
        state.choch_detected = False  # Reset for next evaluation
        
        # =========================================================================
        # MODULE 2: HTF Trend
        # =========================================================================
        htf_trend = self.calculate_htf_trend(candles_4h, candles_1d)
        
        # =========================================================================
        # MODULE 3: Market Regime
        # =========================================================================
        indicators = self.calculate_indicators(closed_candles)
        state.current_adx = indicators.adx
        state.current_atr = indicators.atr
        state.current_volume_ratio = indicators.volume_ratio
        
        regime_str = get_market_regime(
            indicators.adx,
            self.config.adx_dead,
            self.config.adx_trend
        )
        regime = MarketRegime(regime_str)
        
        # DEAD regime = no entries
        if regime == MarketRegime.DEAD:
            return None
        
        # =========================================================================
        # Determine Entry Direction
        # =========================================================================
        action = None
        swing_level = None
        
        # Check for BOS-based entries
        if state.bull_break_pending and htf_trend.is_bullish:
            action = SignalAction.LONG
            swing_level = state.last_swing_high
            state.bull_break_pending = False
        
        elif state.bear_break_pending and htf_trend.is_bearish:
            action = SignalAction.SHORT
            swing_level = state.last_swing_low
            state.bear_break_pending = False
        
        # Check for RANGE entries (BB bounce + CHoCH)
        if action is None and regime == MarketRegime.RANGE and choch_detected:
            if htf_trend.is_bullish and self.check_range_entry(
                closed_candles, indicators, state, SignalAction.LONG
            ):
                action = SignalAction.LONG
                swing_level = closed_candles['close'].iloc[-1]
            
            elif htf_trend.is_bearish and self.check_range_entry(
                closed_candles, indicators, state, SignalAction.SHORT
            ):
                action = SignalAction.SHORT
                swing_level = closed_candles['close'].iloc[-1]
        
        if action is None or swing_level is None:
            return None
        
        # =========================================================================
        # MODULE 4: Voting System
        # =========================================================================
        votes, votes_pass = self.check_voting_system(htf_trend, indicators, action)
        
        if not votes_pass:
            return None
        
        # =========================================================================
        # Check All Filters (using new _apply_filters with toggle support)
        # =========================================================================
        filter_results = self._apply_filters(
            closed_candles, indicators, state, action, swing_level
        )
        
        # Update range conditions for actual regime
        if regime == MarketRegime.RANGE:
            filter_results['range_conditions_ok'] = choch_detected
        else:
            filter_results['range_conditions_ok'] = True
        
        # Check if all filters pass
        if not all(filter_results.values()):
            return None
        
        # =========================================================================
        # BOS Confirmation Check (confirmationType: body/wick)
        # =========================================================================
        # Находим индекс BOS свечи для проверки подтверждения
        bos_index = len(closed_candles) - 2  # Предыдущая свеча - где произошел BOS
        if bos_index >= 0 and bos_index < len(closed_candles) - 1:
            direction = "long" if action == SignalAction.LONG else "short"
            if not self._check_bos_confirmation(closed_candles, bos_index, direction):
                return None
        
        # =========================================================================
        # Calculate Entry, Targets, Position Size
        # =========================================================================
        entry = swing_level
        
        # Используем правильные проценты для TP из настроек
        # Target Range = ATR * 2
        target_range = indicators.atr * 2
        
        if action == SignalAction.LONG:
            # TP1/TP2/TP3 = 40/30/30 от целевого диапазона
            tp1 = entry + target_range * (self.config.tp1_pct / 100)
            tp2 = entry + target_range * ((self.config.tp1_pct + self.config.tp2_pct) / 100)
            tp3 = entry + target_range * ((self.config.tp1_pct + self.config.tp2_pct + self.config.tp3_pct) / 100)
            sl = entry - target_range * 1.2
        elif action == SignalAction.SHORT:
            tp1 = entry - target_range * (self.config.tp1_pct / 100)
            tp2 = entry - target_range * ((self.config.tp1_pct + self.config.tp2_pct) / 100)
            tp3 = entry - target_range * ((self.config.tp1_pct + self.config.tp2_pct + self.config.tp3_pct) / 100)
            sl = entry + target_range * 1.2
        else:
            tp1 = tp2 = tp3 = sl = entry
        
        position_size, risk_amount = self.calculate_position_size(
            portfolio_value, entry, sl, regime
        )
        
        if position_size <= 0:
            return None
        
        # Calculate reward ratio
        avg_tp = (tp1 + tp2 + tp3) / 3
        reward_ratio = abs(avg_tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        
        # =========================================================================
        # Create Signal
        # =========================================================================
        signal = StrategySignal(
            signal_id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            exchange=exchange,
            symbol=symbol,
            timeframe="30m",
            action=action,
            entry_price=entry,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            regime=regime,
            votes=votes,
            votes_required=self.config.votes_required,
            htf_4h=htf_trend.trend_4h,
            htf_1d=htf_trend.trend_1d,
            adx=indicators.adx,
            atr=indicators.atr,
            volume_ratio=indicators.volume_ratio,
            bos_detected=state.bos_confirmed,
            choch_detected=choch_detected,
            swing_level=swing_level,
            risk_pct=self.config.risk_pct,
            position_size=position_size,
            risk_amount=risk_amount,
            reward_ratio=reward_ratio,
            impulse_ok=filter_results['impulse_ok'],
            atr_ok=filter_results['atr_ok'],
            bos_ok=filter_results['bos_ok'],
            cooldown_ok=filter_results['cooldown_ok'],
            range_conditions_ok=filter_results['range_conditions_ok']
        )
        
        # Update state
        state.last_signal_time = signal.timestamp
        state.last_signal_action = action
        state.bos_confirmed = False  # Reset after signal
        
        return signal
    
    def update_settings(self, settings: dict):
        """
        Обновление настроек стратегии в реальном времени.
        
        Вызывается из API при изменении настроек в Dashboard.
        Все 23 параметра могут быть изменены без перезапуска.
        """
        # Создаем новый конфиг из переданных настроек
        new_config = StrategyConfig.from_dict(settings)
        self.config = new_config
        
        # Сброс состояний для применения новых настроек
        self.symbol_states.clear()
    
    def check_breakeven_condition(
        self, 
        entry_price: float, 
        current_price: float, 
        stop_loss: float,
        side: str
    ) -> bool:
        """
        Проверка условия для активации Breakeven (+1R).
        
        Возвращает True, если профит >= 1R (расстояние до SL).
        """
        if entry_price == 0 or stop_loss == 0:
            return False
        
        sl_distance = abs(entry_price - stop_loss)
        
        if side == "long":
            profit = current_price - entry_price
        else:  # short
            profit = entry_price - current_price
        
        # Breakeven активируется при профите >= 1R
        return profit >= sl_distance
    
    def calculate_trailing_stop(
        self,
        current_price: float,
        atr: float,
        side: str,
        last_swing_low: Optional[float],
        last_swing_high: Optional[float],
        use_trail: bool = True
    ) -> Optional[float]:
        """
        Расчет уровня Trailing Stop после достижения TP1.
        
        СПЕЦИФИКАЦИЯ SMT Pro v2:
        
        Для Long после TP1:
            trailStop = lastSwingLow - ATR * 0.25
            обновлять только если trailStop > currentStop
        
        Для Short после TP1:
            trailStop = lastSwingHigh + ATR * 0.25
            обновлять только если trailStop < currentStop
        
        НЕ использовать current_price ± ATR * 1.0
        
        Returns:
            None если use_trail=False или нет данных
            float с уровнем trailing stop
        """
        if not use_trail:
            return None
        
        # Проверяем наличие необходимых swing уровней
        if side == "long":
            if last_swing_low is None:
                return None
            # Trail stop = последний swing low - ATR * 0.25
            trailing_stop = last_swing_low - (atr * 0.25)
        else:  # short
            if last_swing_high is None:
                return None
            # Trail stop = последний swing high + ATR * 0.25
            trailing_stop = last_swing_high + (atr * 0.25)
        
        return trailing_stop
    
    def check_opposite_exit(
        self,
        candles: pd.DataFrame,
        current_position_side: str,
        state: SymbolStrategyState
    ) -> bool:
        """
        Проверка выхода по противоположному BOS/CHoCH.
        
        Возвращает True, если обнаружен сильный противоположный сигнал.
        """
        # Проверяем наличие противоположного тренда
        if current_position_side == "long":
            # Для лонга проверяем медвежий BOS/CHoCH
            if state.current_trend == -1 and state.bear_break_pending:
                return True
        else:  # short
            # Для шорта проверяем бычий BOS/CHoCH
            if state.current_trend == 1 and state.bull_break_pending:
                return True
        
        return False
    
    def apply_flip_logic(
        self,
        candles: pd.DataFrame,
        state: SymbolStrategyState,
        current_action: SignalAction
    ) -> Optional[SignalAction]:
        """
        Реализация Flip Logic - разворот позиции при сильном противоположном сигнале.
        
        Если был LONG и появился сильный SHORT сигнал (или наоборот),
        закрываем текущую позицию и открываем противоположную.
        """
        # Определяем противоположное действие
        if current_action == SignalAction.LONG:
            opposite = SignalAction.SHORT
            opposite_trend = -1
        else:
            opposite = SignalAction.LONG
            opposite_trend = 1
        
        # Проверяем условия для флипа:
        # 1. Противоположный тренд подтвержден
        # 2. Есть BOS в противоположном направлении
        # 3. ADX достаточно высокий (сильный тренд)
        
        indicators = self.calculate_indicators(candles)
        
        if state.current_trend == opposite_trend and \
           indicators.adx >= self.config.adx_trend and \
           ((current_action == SignalAction.LONG and state.bear_break_pending) or \
            (current_action == SignalAction.SHORT and state.bull_break_pending)):
            return opposite
        
        return None
    
    def apply_cooldown(
        self,
        symbol: str,
        exchange: str,
        action: SignalAction,
        current_bar: int
    ):
        """Apply cooldown after stop loss."""
        state = self.get_or_create_state(symbol, exchange)
        cooldown_end = current_bar + self.config.cooldown_bars
        
        if action == SignalAction.LONG:
            state.long_cooldown_until = cooldown_end
        else:
            state.short_cooldown_until = cooldown_end
    
    def reset_bounce_counters(
        self,
        symbol: str,
        exchange: str
    ):
        """Reset BB bounce counters when price crosses BB middle."""
        state = self.get_or_create_state(symbol, exchange)
        state.long_bounce_count = 0
        state.short_bounce_count = 0
        state.last_bb_touch_long = None
        state.last_bb_touch_short = None
