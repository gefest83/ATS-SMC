"""
Тесты для Backtest Engine.
"""

import pytest
from datetime import datetime, timedelta
from backend.backtest.backtest_engine import BacktestEngine, BacktestMetrics


class TestBacktestEngine:
    """Тесты для движка бэктестирования."""
    
    def test_initialization(self):
        """Тест инициализации движка."""
        engine = BacktestEngine(
            initial_capital=10000.0,
            fee_rate=0.001,
            slippage_rate=0.0005,
            risk_pct=1.0,
        )
        
        assert engine.initial_capital == 10000.0
        assert engine.capital == 10000.0
        assert engine.fee_rate == 0.001
        assert engine.slippage_rate == 0.0005
        assert engine.risk_pct == 1.0
        assert len(engine.positions) == 0
        assert len(engine.trades) == 0
    
    def test_slippage_calculation(self):
        """Тест расчета проскальзывания."""
        engine = BacktestEngine(slippage_rate=0.001)
        
        # LONG - цена должна увеличиться
        long_price = engine._apply_slippage(100.0, "LONG")
        assert long_price > 100.0
        assert long_price == 100.1
        
        # SHORT - цена должна уменьшиться
        short_price = engine._apply_slippage(100.0, "SHORT")
        assert short_price < 100.0
        assert short_price == 99.9
    
    def test_fee_calculation(self):
        """Тест расчета комиссий."""
        engine = BacktestEngine(fee_rate=0.001)
        
        fees = engine._calculate_fees(quantity=1.0, price=100.0)
        assert fees == 0.1
        
        fees = engine._calculate_fees(quantity=10.0, price=50.0)
        assert fees == 0.5
    
    def test_position_size_calculation(self):
        """Тест расчета размера позиции."""
        engine = BacktestEngine(risk_pct=1.0)
        
        # Капитал 10000, риск 1% = 100
        # Entry 100, SL 95, расстояние = 5
        # Размер = 100 / 5 = 20
        
        size = engine._calculate_position_size(
            entry_price=100.0,
            stop_loss=95.0,
            capital=10000.0,
            risk_pct=1.0,
        )
        
        assert size == 20.0
    
    def test_position_size_zero_stop_distance(self):
        """Тест с нулевым расстоянием до SL."""
        engine = BacktestEngine()
        
        size = engine._calculate_position_size(
            entry_price=100.0,
            stop_loss=100.0,
            capital=10000.0,
            risk_pct=1.0,
        )
        
        assert size == 0.0
    
    def test_empty_candles_error(self):
        """Тест ошибки при пустых свечах."""
        engine = BacktestEngine()
        
        with pytest.raises(ValueError, match="No M30 candles provided"):
            engine.run(candles_m30={}, candles_4h={}, candles_1d={})
    
    def test_metrics_initialization(self):
        """Тест инициализации метрик."""
        metrics = BacktestMetrics()
        
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0
        assert metrics.total_pnl == 0.0
        assert metrics.profit_factor == 0.0
        assert metrics.max_drawdown == 0.0
    
    def test_no_trades_metrics(self):
        """Тест метрик без сделок."""
        engine = BacktestEngine()
        engine._calculate_metrics()
        
        assert engine.metrics.total_trades == 0
    
    def test_find_candle_at(self):
        """Тест поиска свечи по timestamp."""
        engine = BacktestEngine()
        
        candles = [
            {'timestamp': 1000, 'open': 100, 'high': 105, 'low': 95, 'close': 102},
            {'timestamp': 2000, 'open': 102, 'high': 108, 'low': 100, 'close': 106},
            {'timestamp': 3000, 'open': 106, 'high': 110, 'low': 104, 'close': 108},
        ]
        
        candle = engine._find_candle_at(candles, 2000)
        assert candle is not None
        assert candle['close'] == 106
        
        candle = engine._find_candle_at(candles, 9999)
        assert candle is None
    
    def test_get_history_until(self):
        """Тест получения истории."""
        engine = BacktestEngine()
        
        candles = [
            {'timestamp': 1000, 'close': 100},
            {'timestamp': 2000, 'close': 102},
            {'timestamp': 3000, 'close': 104},
            {'timestamp': 4000, 'close': 106},
            {'timestamp': 5000, 'close': 108},
        ]
        
        history = engine._get_history_until(candles, until_ts=4000, max_bars=10)
        assert len(history) == 4
        assert history[-1]['timestamp'] == 4000
        
        history = engine._get_history_until(candles, until_ts=4000, max_bars=2)
        assert len(history) == 2
        assert history[0]['timestamp'] == 3000
        assert history[1]['timestamp'] == 4000
    
    def test_trade_statistics_calculation(self):
        """Тест расчета статистики сделок."""
        from backend.backtest.backtest_engine import BacktestTrade
        from datetime import datetime
        
        engine = BacktestEngine(initial_capital=10000.0)
        
        # Добавить тестовые сделки
        base_time = datetime(2024, 1, 1, 12, 0)
        
        engine.trades.append(BacktestTrade(
            entry_time=base_time,
            entry_price=100.0,
            exit_time=base_time + timedelta(hours=2),
            exit_price=110.0,
            side="LONG",
            quantity=1.0,
            pnl=10.0,
            pnl_pct=10.0,
            r_multiple=2.0,
            exit_reason="TP1",
            fees=0.1,
            max_drawdown=0,
            max_profit=0,
        ))
        
        engine.trades.append(BacktestTrade(
            entry_time=base_time,
            entry_price=100.0,
            exit_time=base_time + timedelta(hours=3),
            exit_price=95.0,
            side="LONG",
            quantity=1.0,
            pnl=-5.0,
            pnl_pct=-5.0,
            r_multiple=-1.0,
            exit_reason="SL",
            fees=0.1,
            max_drawdown=0,
            max_profit=0,
        ))
        
        engine._calculate_metrics()
        
        assert engine.metrics.total_trades == 2
        assert engine.metrics.winning_trades == 1
        assert engine.metrics.losing_trades == 1
        assert engine.metrics.win_rate == 0.5
        assert engine.metrics.total_pnl == 5.0
        assert engine.metrics.gross_profit == 10.0
        assert engine.metrics.gross_loss == 5.0
        assert engine.metrics.profit_factor == 2.0
    
    def test_exit_reason_statistics(self):
        """Тест статистики по причинам выхода."""
        from backend.backtest.backtest_engine import BacktestTrade
        from datetime import datetime
        
        engine = BacktestEngine()
        
        base_time = datetime(2024, 1, 1, 12, 0)
        
        # Добавить сделки с разными причинами выхода
        reasons = ["TP1", "TP2", "TP3", "SL", "SL", "CHoCH"]
        
        for i, reason in enumerate(reasons):
            pnl = 10.0 if reason.startswith("TP") else -5.0
            engine.trades.append(BacktestTrade(
                entry_time=base_time,
                entry_price=100.0,
                exit_time=base_time + timedelta(hours=i+1),
                exit_price=110.0 if reason.startswith("TP") else 95.0,
                side="LONG",
                quantity=1.0,
                pnl=pnl,
                pnl_pct=abs(pnl),
                r_multiple=abs(pnl) / 5,
                exit_reason=reason,
                fees=0.1,
                max_drawdown=0,
                max_profit=0,
            ))
        
        engine._calculate_metrics()
        
        assert engine.metrics.tp1_hits == 1
        assert engine.metrics.tp2_hits == 1
        assert engine.metrics.tp3_hits == 1
        assert engine.metrics.sl_hits == 2
        assert engine.metrics.choch_exits == 1
    
    def test_consecutive_wins_losses(self):
        """Тест серий побед и поражений."""
        from backend.backtest.backtest_engine import BacktestTrade
        from datetime import datetime
        
        engine = BacktestEngine()
        
        base_time = datetime(2024, 1, 1, 12, 0)
        
        # W-W-L-W-L-L-L
        pnls = [10, 5, -3, 7, -2, -4, -1]
        
        for i, pnl in enumerate(pnls):
            engine.trades.append(BacktestTrade(
                entry_time=base_time,
                entry_price=100.0,
                exit_time=base_time + timedelta(hours=i+1),
                exit_price=110.0 if pnl > 0 else 95.0,
                side="LONG",
                quantity=1.0,
                pnl=pnl,
                pnl_pct=abs(pnl),
                r_multiple=abs(pnl) / 5,
                exit_reason="TP1" if pnl > 0 else "SL",
                fees=0.1,
                max_drawdown=0,
                max_profit=0,
            ))
        
        engine._calculate_metrics()
        
        assert engine.metrics.max_consecutive_wins == 2  # W-W
        assert engine.metrics.max_consecutive_losses == 3  # L-L-L
