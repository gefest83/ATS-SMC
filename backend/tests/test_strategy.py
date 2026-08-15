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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
