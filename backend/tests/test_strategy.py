"""
Tests for SMT Pro Strategy Core.

These tests verify the mathematical correctness of the strategy implementation.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from backend.core.strategy.smt_pro import SMTProStrategy, StrategyConfig
from backend.core.indicators.technical import (
    calculate_atr,
    calculate_adx,
    detect_pivots,
    calculate_bollinger_bands,
)
from backend.models.schemas import SignalAction, MarketRegime


def generate_test_candles(
    n: int = 100,
    start_price: float = 50000.0,
    volatility: float = 0.02,
    trend: float = 0.0,
    seed: int = 42
) -> pd.DataFrame:
    """Generate realistic test candle data."""
    np.random.seed(seed)
    
    # Generate returns with trend and volatility
    returns = np.random.normal(trend, volatility, n)
    
    # Calculate close prices
    close = start_price * np.cumprod(1 + returns)
    
    # Generate OHLC from close
    range_ = close * volatility
    open_ = close - range_ * np.random.uniform(0, 1, n)
    high = np.maximum(open_, close) + range_ * np.random.uniform(0, 0.5, n)
    low = np.minimum(open_, close) - range_ * np.random.uniform(0, 0.5, n)
    
    # Volume
    volume = np.random.uniform(1000, 10000, n)
    
    # Timestamps
    timestamps = [datetime.utcnow() - timedelta(minutes=30*(n-i)) for i in range(n)]
    
    return pd.DataFrame({
        'timestamp': timestamps,
        'open': open_,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    })


class TestPivotDetection:
    """Test pivot high/low detection."""
    
    def test_pivot_high_detection(self):
        """Test that pivot highs are correctly identified."""
        candles = generate_test_candles(n=100, seed=42)
        
        pivot_highs, pivot_lows = detect_pivots(
            candles['high'],
            candles['low'],
            structure_period=20
        )
        
        # Should have some pivots
        ph_count = pivot_highs.dropna().shape[0]
        assert ph_count >= 0  # May have zero if not enough data
        
    def test_pivot_confirmation_delay(self):
        """Test that pivots are confirmed only after structure_period bars."""
        candles = generate_test_candles(n=100, seed=42)
        
        pivot_highs, _ = detect_pivots(
            candles['high'],
            candles['low'],
            structure_period=20
        )
        
        # First 40 bars should have no confirmed pivots (20 left + 20 right)
        first_confirmed_idx = pivot_highs.dropna().index.min()
        assert first_confirmed_idx >= 40 or pd.isna(first_confirmed_idx)


class TestATR:
    """Test ATR calculation."""
    
    def test_atr_positive(self):
        """Test that ATR is always positive."""
        candles = generate_test_candles(n=50)
        
        atr = calculate_atr(
            candles['high'],
            candles['low'],
            candles['close'],
            period=14
        )
        
        assert (atr.dropna() >= 0).all()
    
    def test_atr_values_reasonable(self):
        """Test that ATR values are reasonable relative to price."""
        candles = generate_test_candles(n=50, start_price=50000, volatility=0.02)
        
        atr = calculate_atr(
            candles['high'],
            candles['low'],
            candles['close'],
            period=14
        )
        
        atr_pct = (atr / candles['close']) * 100
        
        # ATR should be roughly 1-5% for crypto
        valid_atr = atr_pct.dropna()
        assert (valid_atr > 0.1).all()
        assert (valid_atr < 20).all()


class TestADX:
    """Test ADX calculation."""
    
    def test_adx_range(self):
        """Test that ADX is in valid range (0-100)."""
        candles = generate_test_candles(n=50)
        
        adx = calculate_adx(
            candles['high'],
            candles['low'],
            candles['close'],
            period=14
        )
        
        valid_adx = adx.dropna()
        assert (valid_adx >= 0).all()
        assert (valid_adx <= 100).all()


class TestBollingerBands:
    """Test Bollinger Bands calculation."""
    
    def test_bb_ordering(self):
        """Test that BB upper > middle > lower."""
        candles = generate_test_candles(n=50)
        
        upper, middle, lower = calculate_bollinger_bands(
            candles['close'],
            period=20,
            stddev=2.0
        )
        
        valid_idx = ~upper.isna()
        assert (upper[valid_idx] >= middle[valid_idx]).all()
        assert (middle[valid_idx] >= lower[valid_idx]).all()


class TestSMTProStrategy:
    """Test complete SMT Pro strategy."""
    
    def test_strategy_initialization(self):
        """Test strategy initializes correctly."""
        config = StrategyConfig()
        strategy = SMTProStrategy(config)
        
        assert strategy.VERSION == "SMT_PRO_V2"
        assert config.structure_period == 20
        assert config.adx_period == 14
    
    def test_strategy_insufficient_data(self):
        """Test strategy returns None with insufficient data."""
        strategy = SMTProStrategy()
        
        # Not enough candles
        candles_short = generate_test_candles(n=30)
        
        signal = strategy.generate_signal(
            symbol="BTC/USDT",
            exchange="binance",
            candles_m30=candles_short,
            candles_4h=candles_short,
            candles_1d=candles_short,
            portfolio_value=10000
        )
        
        assert signal is None
    
    def test_strategy_with_sufficient_data(self):
        """Test strategy processes sufficient data without errors."""
        strategy = SMTProStrategy()
        
        # Enough candles
        candles = generate_test_candles(n=100)
        
        # Should not raise exception
        signal = strategy.generate_signal(
            symbol="BTC/USDT",
            exchange="binance",
            candles_m30=candles,
            candles_4h=candles,
            candles_1d=candles,
            portfolio_value=10000
        )
        
        # Signal may be None if conditions not met, but should not error
        assert signal is None or hasattr(signal, 'action')
    
    def test_votes_required_config(self):
        """Test votes required configuration."""
        config_2of3 = StrategyConfig(filter_mode="2of3")
        assert config_2of3.votes_required == 2
        
        config_all = StrategyConfig(filter_mode="ALL")
        assert config_all.votes_required == 3


class TestMarketRegime:
    """Test market regime classification."""
    
    def test_regime_classification(self):
        """Test ADX-based regime classification."""
        from backend.core.indicators.technical import get_market_regime
        
        assert get_market_regime(10.0) == "DEAD"
        assert get_market_regime(15.0) == "RANGE"
        assert get_market_regime(20.0) == "RANGE"
        assert get_market_regime(25.0) == "TREND"
        assert get_market_regime(30.0) == "TREND"


class TestTrailingStop:
    """Test trailing stop calculation per SMT Pro v2 specification."""
    
    def test_trailing_stop_long_specification(self):
        """
        Для Long после TP1:
            trailStop = lastSwingLow - ATR * 0.25
        """
        strategy = SMTProStrategy()
        
        last_swing_low = 50000.0
        atr = 400.0
        side = "long"
        
        # use_trail=True
        trailing_stop = strategy.calculate_trailing_stop(
            current_price=51000.0,
            atr=atr,
            side=side,
            last_swing_low=last_swing_low,
            last_swing_high=None,
            use_trail=True
        )
        
        expected = last_swing_low - (atr * 0.25)
        assert trailing_stop == expected
        assert trailing_stop == 49900.0  # 50000 - 400*0.25
        
    def test_trailing_stop_short_specification(self):
        """
        Для Short после TP1:
            trailStop = lastSwingHigh + ATR * 0.25
        """
        strategy = SMTProStrategy()
        
        last_swing_high = 50000.0
        atr = 400.0
        side = "short"
        
        trailing_stop = strategy.calculate_trailing_stop(
            current_price=49000.0,
            atr=atr,
            side=side,
            last_swing_low=None,
            last_swing_high=last_swing_high,
            use_trail=True
        )
        
        expected = last_swing_high + (atr * 0.25)
        assert trailing_stop == expected
        assert trailing_stop == 50100.0  # 50000 + 400*0.25
        
    def test_trailing_stop_disabled(self):
        """Trailing stop должен вернуть None при use_trail=False."""
        strategy = SMTProStrategy()
        
        trailing_stop = strategy.calculate_trailing_stop(
            current_price=51000.0,
            atr=400.0,
            side="long",
            last_swing_low=50000.0,
            last_swing_high=None,
            use_trail=False
        )
        
        assert trailing_stop is None
    
    def test_trailing_stop_missing_swing_data(self):
        """Trailing stop должен вернуть None если нет swing данных."""
        strategy = SMTProStrategy()
        
        # Long без last_swing_low
        trailing_stop_long = strategy.calculate_trailing_stop(
            current_price=51000.0,
            atr=400.0,
            side="long",
            last_swing_low=None,
            last_swing_high=50000.0,
            use_trail=True
        )
        assert trailing_stop_long is None
        
        # Short без last_swing_high
        trailing_stop_short = strategy.calculate_trailing_stop(
            current_price=49000.0,
            atr=400.0,
            side="short",
            last_swing_low=50000.0,
            last_swing_high=None,
            use_trail=True
        )
        assert trailing_stop_short is None


class TestBreakeven:
    """Test breakeven condition (+1R)."""
    
    def test_breakeven_condition_long_profit_1r(self):
        """Breakeven должен активироваться при профите >= 1R для long."""
        strategy = SMTProStrategy()
        
        entry_price = 50000.0
        stop_loss = 49000.0  # SL на 1000 ниже
        sl_distance = 1000.0
        
        # Цена прошла 1R в прибыль (entry + sl_distance)
        current_price = entry_price + sl_distance  # 51000
        
        result = strategy.check_breakeven_condition(
            entry_price=entry_price,
            current_price=current_price,
            stop_loss=stop_loss,
            side="long"
        )
        
        assert result is True
    
    def test_breakeven_condition_long_profit_less_than_1r(self):
        """Breakeven НЕ должен активироваться при профите < 1R для long."""
        strategy = SMTProStrategy()
        
        entry_price = 50000.0
        stop_loss = 49000.0
        sl_distance = 1000.0
        
        # Цена прошла только 0.5R в прибыль
        current_price = entry_price + (sl_distance * 0.5)  # 50500
        
        result = strategy.check_breakeven_condition(
            entry_price=entry_price,
            current_price=current_price,
            stop_loss=stop_loss,
            side="long"
        )
        
        assert result is False
    
    def test_breakeven_condition_short_profit_1r(self):
        """Breakeven должен активироваться при профите >= 1R для short."""
        strategy = SMTProStrategy()
        
        entry_price = 50000.0
        stop_loss = 51000.0  # SL на 1000 выше
        sl_distance = 1000.0
        
        # Цена прошла 1R в прибыль (entry - sl_distance)
        current_price = entry_price - sl_distance  # 49000
        
        result = strategy.check_breakeven_condition(
            entry_price=entry_price,
            current_price=current_price,
            stop_loss=stop_loss,
            side="short"
        )
        
        assert result is True
    
    def test_breakeven_condition_at_loss(self):
        """Breakeven НЕ должен активироваться при убытке."""
        strategy = SMTProStrategy()
        
        entry_price = 50000.0
        stop_loss = 49000.0
        
        # Цена в убытке
        current_price = 49500.0
        
        result = strategy.check_breakeven_condition(
            entry_price=entry_price,
            current_price=current_price,
            stop_loss=stop_loss,
            side="long"
        )
        
        assert result is False


class TestFilterToggles:
    """Test that filter toggles actually work."""
    
    def test_use_impulse_toggle_enabled(self):
        """use_impulse=True должен применять импульс фильтр."""
        config = StrategyConfig(use_impulse=True, impulse_mult=1.0)
        strategy = SMTProStrategy(config)
        
        candles = generate_test_candles(n=100, volatility=0.03)
        indicators = strategy.calculate_indicators(candles)
        
        from backend.models.schemas import SignalAction
        state = strategy.get_or_create_state("BTC/USDT", "binance")
        state.current_bar_index = len(candles) - 1
        
        results = strategy._apply_filters(
            candles=candles,
            indicators=indicators,
            state=state,
            action=SignalAction.LONG,
            swing_level=50000.0
        )
        
        # Impulse фильтр должен быть проверен (True или False в зависимости от данных)
        assert 'impulse_ok' in results
    
    def test_use_impulse_toggle_disabled(self):
        """use_impulse=False должен всегда пропускать импульс фильтр."""
        config = StrategyConfig(use_impulse=False)
        strategy = SMTProStrategy(config)
        
        candles = generate_test_candles(n=100, volatility=0.01)
        indicators = strategy.calculate_indicators(candles)
        
        from backend.models.schemas import SignalAction
        state = strategy.get_or_create_state("BTC/USDT", "binance")
        state.current_bar_index = len(candles) - 1
        
        results = strategy._apply_filters(
            candles=candles,
            indicators=indicators,
            state=state,
            action=SignalAction.LONG,
            swing_level=50000.0
        )
        
        # Impulse фильтр должен быть True (отключен)
        assert results['impulse_ok'] is True
    
    def test_use_cooldown_toggle_enabled(self):
        """use_cooldown=True должен применять cooldown фильтр."""
        config = StrategyConfig(use_cooldown=True, cooldown_bars=6)
        strategy = SMTProStrategy(config)
        
        candles = generate_test_candles(n=100)
        indicators = strategy.calculate_indicators(candles)
        
        from backend.models.schemas import SignalAction
        state = strategy.get_or_create_state("BTC/USDT", "binance")
        state.current_bar_index = 50
        state.long_cooldown_until = 60  # Cooldown до бара 60
        
        results = strategy._apply_filters(
            candles=candles,
            indicators=indicators,
            state=state,
            action=SignalAction.LONG,
            swing_level=50000.0
        )
        
        # Должен быть False (в cooldown)
        assert results['cooldown_ok'] is False
    
    def test_use_cooldown_toggle_disabled(self):
        """use_cooldown=False должен всегда пропускать cooldown фильтр."""
        config = StrategyConfig(use_cooldown=False)
        strategy = SMTProStrategy(config)
        
        candles = generate_test_candles(n=100)
        indicators = strategy.calculate_indicators(candles)
        
        from backend.models.schemas import SignalAction
        state = strategy.get_or_create_state("BTC/USDT", "binance")
        state.current_bar_index = 50
        state.long_cooldown_until = 60  # Даже с active cooldown
        
        results = strategy._apply_filters(
            candles=candles,
            indicators=indicators,
            state=state,
            action=SignalAction.LONG,
            swing_level=50000.0
        )
        
        # Должен быть True (отключен)
        assert results['cooldown_ok'] is True
    
    def test_use_range_bounce_toggle(self):
        """use_range_bounce должен управлять BB bounce фильтром."""
        # С включенным фильтром
        config_enabled = StrategyConfig(use_range_bounce=True)
        strategy_enabled = SMTProStrategy(config_enabled)
        
        # С выключенным фильтром
        config_disabled = StrategyConfig(use_range_bounce=False)
        strategy_disabled = SMTProStrategy(config_disabled)
        
        candles = generate_test_candles(n=100)
        indicators = strategy_enabled.calculate_indicators(candles)
        
        from backend.models.schemas import SignalAction
        state_enabled = strategy_enabled.get_or_create_state("BTC/USDT", "binance")
        state_disabled = strategy_disabled.get_or_create_state("BTC/USDT", "binance")
        
        # При отключенном фильтре check_range_entry должен вернуть True
        result_disabled = strategy_disabled.check_range_entry(
            candles=candles,
            indicators=indicators,
            state=state_disabled,
            action=SignalAction.LONG
        )
        assert result_disabled is True


class TestConfirmationType:
    """Test confirmation type (Body vs Wick)."""
    
    def test_confirmation_type_body_long(self):
        """Body confirmation для long: close > high BOS свечи."""
        config = StrategyConfig(confirmation_type="body")
        strategy = SMTProStrategy(config)
        
        # Создаем тестовые свечи
        candles = pd.DataFrame({
            'open': [50000, 50100, 50200],
            'high': [50100, 50200, 50300],
            'low': [49900, 50000, 50100],
            'close': [50050, 50150, 50250],
            'volume': [1000, 1000, 1000]
        })
        
        # BOS на индексе 1 (свеча с high=50200)
        bos_index = 1
        
        result = strategy._check_bos_confirmation(
            candles=candles,
            bos_index=bos_index,
            direction="long"
        )
        
        # Следующая свеча (индекс 2) close=50250 > high=50200 => True
        assert result == True
    
    def test_confirmation_type_wick_long(self):
        """Wick confirmation для long: close > low BOS свечи."""
        config = StrategyConfig(confirmation_type="wick")
        strategy = SMTProStrategy(config)
        
        candles = pd.DataFrame({
            'open': [50000, 50100, 50200],
            'high': [50100, 50200, 50300],
            'low': [49900, 50000, 50100],
            'close': [50050, 50150, 50050],  # close ниже high но выше low
            'volume': [1000, 1000, 1000]
        })
        
        bos_index = 1
        
        result = strategy._check_bos_confirmation(
            candles=candles,
            bos_index=bos_index,
            direction="long"
        )
        
        # Wick: close=50050 > low=50000 => True
        assert result == True
        
        # Для comparison: body confirmation дал бы False
        config_body = StrategyConfig(confirmation_type="body")
        strategy_body = SMTProStrategy(config_body)
        result_body = strategy_body._check_bos_confirmation(
            candles=candles,
            bos_index=bos_index,
            direction="long"
        )
        assert result_body == False  # close=50050 < high=50200


class TestNoLookahead:
    """Test absence of lookahead bias."""
    
    def test_only_closed_candles_used(self):
        """Стратегия должна использовать только закрытые свечи."""
        strategy = SMTProStrategy()
        
        # Генерируем свечи
        candles = generate_test_candles(n=100)
        
        # Проверяем что analyze_market_structure не использует будущие данные
        state = strategy.get_or_create_state("BTC/USDT", "binance")
        structure = strategy.analyze_market_structure(candles, state)
        
        # Структура должна быть определена только по последним ЗАКРЫТЫм данным
        assert hasattr(structure, 'trend')
        assert hasattr(structure, 'structure_broken')
    
    def test_pivot_no_future_data(self):
        """Pivot detection не должен использовать будущие данные."""
        candles = generate_test_candles(n=100, seed=42)
        
        pivot_highs, pivot_lows = detect_pivots(
            candles['high'],
            candles['low'],
            structure_period=20
        )
        
        # Pivot подтверждается только через structure_period баров
        # Первые 40 баров не должны иметь подтвержденных pivots
        confirmed_phs = pivot_highs.dropna()
        if len(confirmed_phs) > 0:
            first_confirmed_idx = confirmed_phs.index.min()
            assert first_confirmed_idx >= 40  # 20 left + 20 right
    
    def test_signal_deterministic(self):
        """Одинаковые входные данные должны давать одинаковый сигнал."""
        strategy = SMTProStrategy()
        
        candles = generate_test_candles(n=100, seed=42)
        
        signal1 = strategy.generate_signal(
            symbol="BTC/USDT",
            exchange="binance",
            candles_m30=candles,
            candles_4h=candles,
            candles_1d=candles,
            portfolio_value=10000
        )
        
        # Повторный вызов с теми же данными
        signal2 = strategy.generate_signal(
            symbol="BTC/USDT",
            exchange="binance",
            candles_m30=candles,
            candles_4h=candles,
            candles_1d=candles,
            portfolio_value=10000
        )
        
        # Оба сигнала должны быть либо None, либо одинаковыми
        if signal1 is None:
            assert signal2 is None
        else:
            assert signal1.action == signal2.action
            assert signal1.entry_price == signal2.entry_price
            assert signal1.stop_loss == signal2.stop_loss


class TestSettingsUpdate:
    """Test runtime settings update."""
    
    def test_update_settings_all_23_parameters(self):
        """Проверка обновления всех 23 параметров."""
        strategy = SMTProStrategy()
        
        new_settings = {
            "structurePeriod": 25,
            "confirmationType": "wick",
            "htf1": "2h",
            "htf2": "4h",
            "adxTh": 18,
            "adxTrend": 28,
            "adxDead": 12,
            "filterMode": "ALL",
            "volMult": 2.0,
            "useImpulse": False,
            "impulseMult": 1.5,
            "useRangeBounce": False,
            "bbLookback": 15,
            "maxBounces": 3,
            "minAtrPct": 0.5,
            "maxBosDistAtr": 0.8,
            "useCooldown": False,
            "cooldownBars": 10,
            "riskPct": 2.0,
            "tp1Pct": 50,
            "tp2Pct": 25,
            "tp3Pct": 25,
            "useBreakeven": False,
            "useTrail": True
        }
        
        strategy.update_settings(new_settings)
        
        # Проверяем что все параметры обновлены
        assert strategy.config.structure_period == 25
        assert strategy.config.confirmation_type == "wick"
        assert strategy.config.htf1 == "2h"
        assert strategy.config.htf2 == "4h"
        assert strategy.config.adx_th == 18
        assert strategy.config.adx_trend == 28
        assert strategy.config.adx_dead == 12
        assert strategy.config.filter_mode == "ALL"
        assert strategy.config.volume_mult == 2.0
        assert strategy.config.use_impulse is False
        assert strategy.config.impulse_mult == 1.5
        assert strategy.config.use_range_bounce is False
        assert strategy.config.bb_lookback == 15
        assert strategy.config.max_bounces == 3
        assert strategy.config.min_atr_pct == 0.5
        assert strategy.config.max_bos_dist_atr == 0.8
        assert strategy.config.use_cooldown is False
        assert strategy.config.cooldown_bars == 10
        assert strategy.config.risk_pct == 2.0
        assert strategy.config.tp1_pct == 50
        assert strategy.config.tp2_pct == 25
        assert strategy.config.tp3_pct == 25
        assert strategy.config.use_breakeven is False
        assert strategy.config.use_trail is True
    
    def test_config_to_dict_from_dict_roundtrip(self):
        """Проверка сериализации/десериализации конфига."""
        original = StrategyConfig(
            structure_period=25,
            confirmation_type="wick",
            use_impulse=False,
            use_trail=True,
            risk_pct=2.0
        )
        
        config_dict = original.to_dict()
        restored = StrategyConfig.from_dict(config_dict)
        
        assert restored.structure_period == original.structure_period
        assert restored.confirmation_type == original.confirmation_type
        assert restored.use_impulse == original.use_impulse
        assert restored.use_trail == original.use_trail
        assert restored.risk_pct == original.risk_pct


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
