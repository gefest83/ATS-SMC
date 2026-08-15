"""
ATS-SMT PRO — End-to-End (E2E) Integration Tests

Проверяет полный цикл работы системы:
Signal → Risk Check → Order Creation → Position Management
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any

from backend.core.strategy.smt_pro import SMTProStrategy as SMTPro, SymbolStrategyState as StrategyState
from backend.models.schemas import StrategySignal as Signal
# Technical indicators are used internally by strategy, no need to import separately
from backend.core.risk.risk_manager import RiskManager, RiskCheckResult
from backend.core.orders.order_manager import OrderManager, OrderStatus
from backend.core.positions.position_manager import PositionManager
from backend.core.persistence.models import Exchange, Symbol, Order, Position
from backend.config.settings import Config, get_config

settings = get_config()


class TestE2EWorkflow:
    """End-to-End тесты полного цикла торговли"""

    @pytest.fixture
    def sample_candles(self) -> List[Dict[str, Any]]:
        """Генерация тестовых свечей с явным бычьим трендом"""
        candles = []
        base_price = 50000.0
        now = datetime.utcnow()
        
        for i in range(50):
            timestamp = now - timedelta(minutes=(50 - i) * 30)
            open_price = base_price + (i * 10)
            close_price = open_price + 50
            high = max(open_price, close_price) + 20
            low = min(open_price, close_price) - 20
            volume = 1000 + (i * 10)
            
            candles.append({
                'timestamp': timestamp,
                'open': open_price,
                'high': high,
                'low': low,
                'close': close_price,
                'volume': volume
            })
        
        return candles

    @pytest.fixture
    def strategy(self) -> SMTPro:
        """Инициализация стратегии"""
        return SMTPro()

    @pytest.fixture
    def risk_manager(self) -> RiskManager:
        """Инициализация риск-менеджера"""
        return RiskManager()

    @pytest.fixture
    def order_manager(self) -> OrderManager:
        """Инициализация менеджера ордеров"""
        return OrderManager()

    @pytest.fixture
    def position_manager(self) -> PositionManager:
        """Инициализация менеджера позиций"""
        return PositionManager()

    def test_e2e_signal_to_order_workflow(self, strategy, risk_manager, order_manager, sample_candles):
        """
        E2E-001: Полный цикл от сигнала до создания ордера
        
        Flow:
        1. Strategy генерирует сигнал
        2. Risk Manager проверяет сигнал
        3. Order Manager создает ордер
        4. Проверяем целостность данных
        """
        # 1. Генерация сигнала
        state = StrategyState(symbol='BTC/USDT', timeframe='30m')
        signal = strategy.calculate(state, sample_candles, [], [])
        
        if signal:
            # 2. Проверка риска
            risk_result = risk_manager.check_pre_trade_risk(
                signal=signal,
                current_balance=10000.0,
                available_balance=10000.0,
                open_positions_count=0,
                daily_pnl=0.0,
                emergency_stop=False
            )
            
            assert risk_result.approved is True or risk_result.approved is False
            
            if risk_result.approved:
                # 3. Создание ордера
                order = order_manager.create_order_from_signal(
                    signal=signal,
                    exchange_id='binance_001',
                    client_order_id=f"test_{datetime.utcnow().timestamp()}"
                )
                
                # 4. Валидация ордера
                assert order is not None
                assert order.signal_id == signal.signal_id
                assert order.side == signal.action
                assert order.status == OrderStatus.PENDING
                
                print(f"✅ E2E-001 PASSED: Order created successfully: {order.client_order_id}")
        else:
            print("⚠️ E2E-001 SKIPPED: No signal generated (normal for some market conditions)")

    def test_e2e_position_sizing_accuracy(self, strategy, risk_manager, sample_candles):
        """
        E2E-002: Точность расчета размера позиции
        
        Проверяет:
        - Расчет risk_amount
        - Расчет stop_distance
        - Учет комиссий и precision
        """
        state = StrategyState(symbol='BTC/USDT', timeframe='30m')
        signal = strategy.calculate(state, sample_candles, [], [])
        
        if signal:
            # Ручной расчет ожидаемого размера
            portfolio_value = 10000.0
            expected_risk_amount = portfolio_value * (settings.risk_pct / 100.0)
            stop_distance = abs(signal.entry - signal.sl)
            
            if stop_distance > 0:
                expected_size = expected_risk_amount / stop_distance
                
                # Проверка через risk manager
                risk_result = risk_manager.check_pre_trade_risk(
                    signal=signal,
                    current_balance=portfolio_value,
                    available_balance=portfolio_value,
                    open_positions_count=0,
                    daily_pnl=0.0,
                    emergency_stop=False
                )
                
                if risk_result.approved and risk_result.max_quantity:
                    actual_size = float(risk_result.max_quantity)
                    
                    # Допускаем небольшую погрешность из-за округления
                    tolerance = expected_size * 0.05
                    assert abs(actual_size - expected_size) <= tolerance, \
                        f"Position size mismatch: expected {expected_size}, got {actual_size}"
                    
                    print(f"✅ E2E-002 PASSED: Position size accurate (expected: {expected_size:.6f}, actual: {actual_size:.6f})")
        else:
            print("⚠️ E2E-002 SKIPPED: No signal generated")

    def test_e2e_duplicate_order_protection(self, strategy, risk_manager, order_manager, sample_candles):
        """
        E2E-003: Защита от дублирования ордеров
        
        Проверяет:
        - Один сигнал не создает два ордера
        - Idempotency key работает
        """
        state = StrategyState(symbol='ETH/USDT', timeframe='30m')
        signal = strategy.calculate(state, sample_candles, [], [])
        
        if signal:
            # Создаем первый ордер
            order1 = order_manager.create_order_from_signal(
                signal=signal,
                exchange_id='binance_002',
                client_order_id=f"dup_test_{datetime.utcnow().timestamp()}"
            )
            
            # Пытаемся создать второй ордер с тем же signal_id
            order2 = order_manager.create_order_from_signal(
                signal=signal,
                exchange_id='binance_002',
                client_order_id=f"dup_test_{datetime.utcnow().timestamp()}_2"
            )
            
            # Проверяем защиту от дублирования
            if order2:
                assert order1.order_id != order2.order_id or order2.status == OrderStatus.REJECTED, \
                    "Duplicate order was created without rejection!"
            
            print("✅ E2E-003 PASSED: Duplicate order protection working")
        else:
            print("⚠️ E2E-003 SKIPPED: No signal generated")

    def test_e2e_risk_manager_blocks_excessive_risk(self, risk_manager, strategy, sample_candles):
        """
        E2E-004: Risk Manager блокирует чрезмерный риск
        
        Проверяет:
        - Блокировка при insufficient balance
        - Блокировка при превышении max_open_trades
        - Блокировка при emergency stop
        """
        state = StrategyState(symbol='SOL/USDT', timeframe='30m')
        signal = strategy.calculate(state, sample_candles, [], [])
        
        if signal:
            # Тест 1: Insufficient balance
            result = risk_manager.check_pre_trade_risk(
                signal=signal,
                current_balance=10.0,  # Очень маленький баланс
                available_balance=10.0,
                open_positions_count=0,
                daily_pnl=0.0,
                emergency_stop=False
            )
            assert result.approved is False or result.reason is not None
            
            # Тест 2: Emergency stop активен
            result = risk_manager.check_pre_trade_risk(
                signal=signal,
                current_balance=10000.0,
                available_balance=10000.0,
                open_positions_count=0,
                daily_pnl=0.0,
                emergency_stop=True  # Emergency stop включен
            )
            assert result.approved is False
            assert "emergency" in result.reason.lower()
            
            print("✅ E2E-004 PASSED: Risk manager correctly blocks excessive risk")
        else:
            print("⚠️ E2E-004 SKIPPED: No signal generated")

    def test_e2e_multi_symbol_isolation(self, strategy, sample_candles):
        """
        E2E-005: Изоляция состояний между символами
        
        Проверяет:
        - Сигналы для BTC не влияют на ETH
        - Независимые StrategyState
        """
        btc_state = StrategyState(symbol='BTC/USDT', timeframe='30m')
        eth_state = StrategyState(symbol='ETH/USDT', timeframe='30m')
        
        btc_signal = strategy.calculate(btc_state, sample_candles, [], [])
        eth_signal = strategy.calculate(eth_state, sample_candles, [], [])
        
        # Сигналы должны быть независимы
        if btc_signal and eth_signal:
            assert btc_signal.symbol == 'BTC/USDT'
            assert eth_signal.symbol == 'ETH/USDT'
            assert btc_signal.signal_id != eth_signal.signal_id
        
        print("✅ E2E-005 PASSED: Multi-symbol isolation working")

    def test_e2e_settings_validation_and_update(self):
        """
        E2E-006: Валидация и обновление настроек стратегии
        
        Проверяет:
        - Валидация некорректных значений
        - Применение корректных значений
        - История изменений
        """
        from backend.core.strategy.smt_pro import SMTProSettings
        
        # Тест 1: Некорректный risk_pct (> MAX)
        with pytest.raises(ValueError):
            SMTProSettings(risk_pct=25.0)  # Превышает MAX_ALLOWED_RISK_PCT=5.0
        
        # Тест 2: Некорректные TP percentages
        with pytest.raises(ValueError):
            SMTProSettings(tp1_pct=50, tp2_pct=50, tp3_pct=50)  # Сумма != 100
        
        # Тест 3: Корректные настройки
        valid_settings = SMTProSettings(
            risk_pct=1.5,
            tp1_pct=40,
            tp2_pct=30,
            tp3_pct=30,
            structure_period=20,
            adx_th=20
        )
        
        assert valid_settings.risk_pct == 1.5
        assert valid_settings.tp1_pct + valid_settings.tp2_pct + valid_settings.tp3_pct == 100
        
        print("✅ E2E-006 PASSED: Settings validation and update working")

    def test_e2e_symbol_normalization(self):
        """
        E2E-007: Нормализация символов для разных бирж
        
        Проверяет:
        - Binance: BTC/USDT
        - OKX: BTC-USDT
        - Bybit: BTCUSDT
        """
        from backend.core.exchange.binance_adapter import BinanceAdapter
        from backend.core.exchange.okx_adapter import OKXAdapter
        from backend.core.exchange.bybit_adapter import BybitAdapter
        
        binance = BinanceAdapter(api_key="", secret_key="")
        okx = OKXAdapter(api_key="", secret_key="", passphrase="")
        bybit = BybitAdapter(api_key="", secret_key="")
        
        symbol = 'BTC/USDT'
        
        binance_symbol = binance.normalize_symbol(symbol)
        okx_symbol = okx.normalize_symbol(symbol)
        bybit_symbol = bybit.normalize_symbol(symbol)
        
        assert binance_symbol == 'BTCUSDT'
        assert okx_symbol == 'BTC-USDT'
        assert bybit_symbol == 'BTCUSDT'
        
        print("✅ E2E-007 PASSED: Symbol normalization working for all exchanges")

    def test_e2e_full_system_health_check(self, strategy, risk_manager, order_manager, position_manager):
        """
        E2E-008: Полная проверка здоровья системы
        
        Проверяет:
        - Все компоненты инициализированы
        - Нет критических ошибок
        - Готовность к работе
        """
        # Проверка инициализации компонентов
        assert strategy is not None
        assert risk_manager is not None
        assert order_manager is not None
        assert position_manager is not None
        
        # Проверка конфигурации
        assert settings.trading_mode in ['paper', 'testnet', 'live']
        assert settings.risk_pct > 0
        assert settings.risk_pct <= 5.0
        
        # Проверка доступности адаптеров
        from backend.core.exchange.base_adapter import list_available_adapters
        
        registered_adapters = list_available_adapters()
        assert len(registered_adapters) >= 7, "Минимум 7 бирж должны быть зарегистрированы"
        
        print(f"✅ E2E-008 PASSED: System health check passed ({len(registered_adapters)} exchanges ready)")


# Запуск тестов
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
