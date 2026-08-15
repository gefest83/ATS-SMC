"""
Backtest Engine для SMT Pro Strategy.

Использует ТО ЖЕ ядро стратегии, что и live/paper trading.
Без lookahead bias.
Учитывает: fees, slippage, partial TP, SL, BE, cooldown, HTF, BOS, CHoCH, ADX, volume, ATR.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import numpy as np

from backend.core.strategy.smt_pro import SMTProStrategy, SymbolStrategyState


class BacktestOrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class BacktestTrade:
    """Результат одной сделки."""
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    side: str  # LONG/SHORT
    quantity: float
    pnl: float
    pnl_pct: float
    r_multiple: float
    exit_reason: str  # TP1, TP2, TP3, SL, CHoCH, EOS
    fees: float
    max_drawdown: float
    max_profit: float


@dataclass
class BacktestPosition:
    """Активная позиция в бэктесте."""
    symbol: str
    side: str
    entry_price: float
    quantity: float
    entry_time: datetime
    sl: float
    tp1: float
    tp2: float
    tp3: float
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    remaining_qty: float = 0.0
    closed_qty: float = 0.0
    breakeven_active: bool = False
    initial_risk: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0


@dataclass
class BacktestMetrics:
    """Метрики бэктеста."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    average_r: float = 0.0
    expectancy: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    
    # Статистика по выходам
    tp1_hits: int = 0
    tp2_hits: int = 0
    tp3_hits: int = 0
    sl_hits: int = 0
    choch_exits: int = 0
    eos_exits: int = 0  # End of simulation
    
    # Временные метрики
    avg_trade_duration: timedelta = timedelta(0)
    max_trade_duration: timedelta = timedelta(0)
    
    # Риск метрики
    avg_risk_per_trade: float = 0.0
    max_risk_per_trade: float = 0.0


class BacktestEngine:
    """
    Движок бэктестирования.
    
    Использует реальное ядро стратегии SMTPro.
    Симулирует исполнение ордеров с учетом fees и slippage.
    Обрабатывает частичные закрытия (TP1/TP2/TP3).
    """
    
    def __init__(
        self,
        initial_capital: float = 10000.0,
        fee_rate: float = 0.001,  # 0.1% per trade
        slippage_rate: float = 0.0005,  # 0.05% slippage
        risk_pct: float = 1.0,
    ):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.risk_pct = risk_pct
        
        self.positions: Dict[str, BacktestPosition] = {}
        self.trades: List[BacktestTrade] = []
        self.equity_curve: List[Tuple[datetime, float]] = []
        
        self.strategy_states: Dict[str, SymbolStrategyState] = {}
        
        # Метрики
        self.metrics = BacktestMetrics()
        
        # Для отслеживания drawdown
        self.peak_equity = initial_capital
        self.current_drawdown = 0.0
        
    def _get_strategy_state(self, symbol: str) -> SymbolStrategyState:
        """Получить или создать состояние стратегии для символа."""
        if symbol not in self.strategy_states:
            self.strategy_states[symbol] = SymbolStrategyState(symbol=symbol)
        return self.strategy_states[symbol]
    
    def _apply_slippage(self, price: float, side: str) -> float:
        """Применить проскальзывание к цене."""
        if side == "LONG":
            return price * (1 + self.slippage_rate)
        else:  # SHORT
            return price * (1 - self.slippage_rate)
    
    def _calculate_fees(self, quantity: float, price: float) -> float:
        """Рассчитать комиссию."""
        return quantity * price * self.fee_rate
    
    def _calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        capital: float,
        risk_pct: float,
    ) -> float:
        """Рассчитать размер позиции на основе риска."""
        if entry_price == stop_loss:
            return 0.0
        
        risk_amount = capital * (risk_pct / 100.0)
        stop_distance = abs(entry_price - stop_loss)
        
        if stop_distance == 0:
            return 0.0
        
        position_size = risk_amount / stop_distance
        return position_size
    
    def run(
        self,
        candles_m30: Dict[str, List[dict]],
        candles_4h: Dict[str, List[dict]],
        candles_1d: Dict[str, List[dict]],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> BacktestMetrics:
        """
        Запустить бэктест на исторических данных.
        
        Args:
            candles_m30: M30 свечи по символам
            candles_4h: 4H свечи по символам
            candles_1d: 1D свечи по символам
            start_date: Начало бэктеста
            end_date: Конец бэктеста
            
        Returns:
            BacktestMetrics с результатами
        """
        if not candles_m30:
            raise ValueError("No M30 candles provided")
        
        symbols = list(candles_m30.keys())
        
        # Определить временной диапазон
        all_times = []
        for symbol in symbols:
            if candles_m30[symbol]:
                all_times.extend([c['timestamp'] for c in candles_m30[symbol]])
        
        if not all_times:
            raise ValueError("No candle timestamps found")
        
        min_time = min(all_times)
        max_time = max(all_times)
        
        if start_date is None:
            start_date = datetime.fromtimestamp(min_time)
        if end_date is None:
            end_date = datetime.fromtimestamp(max_time)
        
        # Инициализировать стратегию для каждого символа
        for symbol in symbols:
            self._get_strategy_state(symbol)
        
        # Получить все уникальные timestamp
        timestamps = sorted(set(all_times))
        
        # Фильтровать по диапазону дат
        timestamps = [
            ts for ts in timestamps
            if start_date <= datetime.fromtimestamp(ts) <= end_date
        ]
        
        print(f"Starting backtest from {start_date} to {end_date}")
        print(f"Symbols: {symbols}")
        print(f"Total bars: {len(timestamps)}")
        
        # Главный цикл бэктеста
        for i, current_ts in enumerate(timestamps):
            current_time = datetime.fromtimestamp(current_ts)
            
            # 1. Обновить equity curve
            total_equity = self.capital + self._calculate_unrealized_pnl(current_ts, candles_m30)
            self.equity_curve.append((current_time, total_equity))
            
            # Обновить peak и drawdown
            if total_equity > self.peak_equity:
                self.peak_equity = total_equity
            self.current_drawdown = self.peak_equity - total_equity
            
            # 2. Для каждого символа
            for symbol in symbols:
                # Проверить наличие данных
                if symbol not in candles_m30 or symbol not in candles_4h or symbol not in candles_1d:
                    continue
                
                m30_candles = candles_m30[symbol]
                h4_candles = candles_4h[symbol]
                d1_candles = candles_1d[symbol]
                
                # Найти текущие свечи
                m30_candle = self._find_candle_at(m30_candles, current_ts)
                if not m30_candle:
                    continue
                
                # Проверить, закрыта ли свеча (используем только закрытые)
                if i < len(timestamps) - 1:
                    next_ts = timestamps[i + 1]
                    if m30_candle['timestamp'] >= next_ts - 60:  # Еще не закрыта
                        continue
                
                # Получить HTF данные
                h4_candle = self._find_last_closed_candle(h4_candles, current_ts, 4 * 3600)
                d1_candle = self._find_last_closed_candle(d1_candles, current_ts, 86400)
                
                if not h4_candle or not d1_candle:
                    continue
                
                # Подготовить данные для стратегии
                m30_history = self._get_history_until(m30_candles, current_ts, 100)
                h4_history = self._get_history_until(h4_candles, current_ts, 100)
                d1_history = self._get_history_until(d1_candles, current_ts, 100)
                
                if len(m30_history) < 50:
                    continue
                
                state = self._get_strategy_state(symbol)
                
                # 3. Рассчитать сигнал стратегии
                signal = self._calculate_signal(
                    symbol=symbol,
                    m30_candles=m30_history,
                    h4_candles=h4_history,
                    d1_candles=d1_history,
                    current_candle=m30_candle,
                    state=state,
                )
                
                # 4. Управление существующими позициями
                if symbol in self.positions:
                    self._manage_position(
                        position=self.positions[symbol],
                        current_candle=m30_candle,
                        current_time=current_time,
                        signal=signal,
                    )
                
                # 5. Открыть новую позицию если есть сигнал и нет позиции
                if signal and signal.action in ["LONG", "SHORT"] and symbol not in self.positions:
                    self._open_position(
                        symbol=symbol,
                        signal=signal,
                        current_candle=m30_candle,
                        current_time=current_time,
                    )
        
        # Закрыть все открытые позиции в конце
        self._close_all_positions(end_date, "EOS")
        
        # Рассчитать финальные метрики
        self._calculate_metrics()
        
        return self.metrics
    
    def _find_candle_at(self, candles: List[dict], timestamp: int) -> Optional[dict]:
        """Найти свечу, соответствующую timestamp."""
        for candle in candles:
            if candle['timestamp'] == timestamp:
                return candle
        return None
    
    def _find_last_closed_candle(
        self,
        candles: List[dict],
        current_ts: int,
        period_seconds: int,
    ) -> Optional[dict]:
        """Найти последнюю закрытую свечу HTF."""
        # Найти последнюю свечу, которая закрылась до текущего времени
        for candle in reversed(candles):
            candle_end = candle['timestamp'] + period_seconds
            if candle_end <= current_ts:
                return candle
        return None
    
    def _get_history_until(
        self,
        candles: List[dict],
        until_ts: int,
        max_bars: int,
    ) -> List[dict]:
        """Получить историю свечей до указанного времени."""
        history = [c for c in candles if c['timestamp'] <= until_ts]
        return history[-max_bars:]
    
    def _calculate_signal(
        self,
        symbol: str,
        m30_candles: List[dict],
        h4_candles: List[dict],
        d1_candles: List[dict],
        current_candle: dict,
        state: SymbolStrategyState,
    ) -> Optional[dict]:
        """Рассчитать сигнал стратегии."""
        strategy = SMTProStrategy()
        
        # Преобразовать свечи в формат OHLCV
        m30_ohlcv = [
            [
                c['timestamp'],
                c['open'],
                c['high'],
                c['low'],
                c['close'],
                c.get('volume', 0)
            ]
            for c in m30_candles
        ]
        
        h4_ohlcv = [
            [
                c['timestamp'],
                c['open'],
                c['high'],
                c['low'],
                c['close'],
                c.get('volume', 0)
            ]
            for c in h4_candles
        ]
        
        d1_ohlcv = [
            [
                c['timestamp'],
                c['open'],
                c['high'],
                c['low'],
                c['close'],
                c.get('volume', 0)
            ]
            for c in d1_candles
        ]
        
        try:
            signal = strategy.calculate(
                ohlcv_m30=m30_ohlcv,
                ohlcv_4h=h4_ohlcv,
                ohlcv_1d=d1_ohlcv,
                symbol=symbol,
                state=state,
            )
            return signal
        except Exception as e:
            print(f"Error calculating signal for {symbol}: {e}")
            return None
    
    def _open_position(
        self,
        symbol: str,
        signal: dict,
        current_candle: dict,
        current_time: datetime,
    ):
        """Открыть позицию."""
        entry_price = self._apply_slippage(signal.entry, signal.action)
        fees = self._calculate_fees(1, entry_price)  # Будет пересчитано
        
        # Рассчитать размер позиции
        stop_loss = signal.sl
        position_size = self._calculate_position_size(
            entry_price=entry_price,
            stop_loss=stop_loss,
            capital=self.capital,
            risk_pct=self.risk_pct,
        )
        
        if position_size <= 0:
            return
        
        # Пересчитать комиссии
        fees = self._calculate_fees(position_size, entry_price)
        
        # Создать позицию
        position = BacktestPosition(
            symbol=symbol,
            side=signal.action,
            entry_price=entry_price,
            quantity=position_size,
            remaining_qty=position_size,
            entry_time=current_time,
            sl=stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            initial_risk=abs(entry_price - stop_loss),
            highest_price=entry_price if signal.action == "LONG" else entry_price,
            lowest_price=entry_price if signal.action == "SHORT" else entry_price,
        )
        
        self.positions[symbol] = position
        self.capital -= fees  # Комиссия при открытии
        
        print(f"[{current_time}] OPEN {signal.action} {symbol} @ {entry_price:.4f}, qty={position_size:.4f}")
    
    def _manage_position(
        self,
        position: BacktestPosition,
        current_candle: dict,
        current_time: datetime,
        signal: Optional[dict],
    ):
        """Управление существующей позицией."""
        high = current_candle['high']
        low = current_candle['low']
        close = current_candle['close']
        
        # Обновить экстремумы для trailing и BE
        if position.side == "LONG":
            if high > position.highest_price:
                position.highest_price = high
        else:  # SHORT
            if low < position.lowest_price:
                position.lowest_price = low
        
        # Проверить Breakeven
        if not position.breakeven_active and position.initial_risk > 0:
            if position.side == "LONG":
                if high >= position.entry_price + position.initial_risk:
                    position.breakeven_active = True
                    position.sl = position.entry_price
                    print(f"[{current_time}] BE ACTIVATED for {position.symbol}")
            else:  # SHORT
                if low <= position.entry_price - position.initial_risk:
                    position.breakeven_active = True
                    position.sl = position.entry_price
                    print(f"[{current_time}] BE ACTIVATED for {position.symbol}")
        
        # Проверить выход по структуре (CHoCH против позиции)
        if signal and signal.action and signal.action != position.side:
            # Противоположный сигнал - закрыть позицию
            exit_price = close
            if position.side == "LONG":
                exit_price = self._apply_slippage(close, "SHORT")
            else:
                exit_price = self._apply_slippage(close, "LONG")
            
            self._close_position(
                position=position,
                exit_price=exit_price,
                exit_time=current_time,
                reason="CHoCH",
                partial=False,
            )
            return
        
        # Проверить TP1
        if not position.tp1_hit and position.remaining_qty > 0:
            hit_tp1 = False
            if position.side == "LONG" and high >= position.tp1:
                hit_tp1 = True
            elif position.side == "SHORT" and low <= position.tp1:
                hit_tp1 = True
            
            if hit_tp1:
                close_qty = position.quantity * 0.40
                exit_price = position.tp1
                self._close_partial(
                    position=position,
                    qty=close_qty,
                    exit_price=exit_price,
                    exit_time=current_time,
                    reason="TP1",
                )
                position.tp1_hit = True
        
        # Проверить TP2
        if not position.tp2_hit and position.remaining_qty > 0:
            hit_tp2 = False
            if position.side == "LONG" and high >= position.tp2:
                hit_tp2 = True
            elif position.side == "SHORT" and low <= position.tp2:
                hit_tp2 = True
            
            if hit_tp2:
                close_qty = position.quantity * 0.30
                exit_price = position.tp2
                self._close_partial(
                    position=position,
                    qty=close_qty,
                    exit_price=exit_price,
                    exit_time=current_time,
                    reason="TP2",
                )
                position.tp2_hit = True
        
        # Проверить TP3
        if not position.tp3_hit and position.remaining_qty > 0:
            hit_tp3 = False
            if position.side == "LONG" and high >= position.tp3:
                hit_tp3 = True
            elif position.side == "SHORT" and low <= position.tp3:
                hit_tp3 = True
            
            if hit_tp3:
                self._close_position(
                    position=position,
                    exit_price=position.tp3,
                    exit_time=current_time,
                    reason="TP3",
                    partial=False,
                )
                return
        
        # Проверить SL
        if position.remaining_qty > 0:
            hit_sl = False
            if position.side == "LONG" and low <= position.sl:
                hit_sl = True
            elif position.side == "SHORT" and high >= position.sl:
                hit_sl = True
            
            if hit_sl:
                self._close_position(
                    position=position,
                    exit_price=position.sl,
                    exit_time=current_time,
                    reason="SL",
                    partial=False,
                )
    
    def _close_partial(
        self,
        position: BacktestPosition,
        qty: float,
        exit_price: float,
        exit_time: datetime,
        reason: str,
    ):
        """Частичное закрытие позиции."""
        if qty > position.remaining_qty:
            qty = position.remaining_qty
        
        # Применить проскальзывание
        if position.side == "LONG":
            actual_exit = self._apply_slippage(exit_price, "SHORT")
        else:
            actual_exit = self._apply_slippage(exit_price, "LONG")
        
        # Рассчитать PnL
        if position.side == "LONG":
            pnl = (actual_exit - position.entry_price) * qty
        else:
            pnl = (position.entry_price - actual_exit) * qty
        
        # Комиссии
        fees = self._calculate_fees(qty, actual_exit)
        pnl -= fees
        
        position.realized_pnl += pnl
        position.fees_paid += fees
        position.remaining_qty -= qty
        position.closed_qty += qty
        
        # Записать торговлю
        trade = BacktestTrade(
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=exit_time,
            exit_price=actual_exit,
            side=position.side,
            quantity=qty,
            pnl=pnl,
            pnl_pct=(pnl / (position.entry_price * qty)) * 100 if position.entry_price > 0 else 0,
            r_multiple=pnl / (position.initial_risk * qty) if position.initial_risk > 0 else 0,
            exit_reason=reason,
            fees=fees,
            max_drawdown=0,
            max_profit=0,
        )
        self.trades.append(trade)
        
        print(f"[{exit_time}] {reason} {position.symbol} qty={qty:.4f} @ {actual_exit:.4f}, PnL={pnl:.2f}")
    
    def _close_position(
        self,
        position: BacktestPosition,
        exit_price: float,
        exit_time: datetime,
        reason: str,
        partial: bool = False,
    ):
        """Полное закрытие позиции."""
        if position.remaining_qty <= 0:
            return
        
        # Применить проскальзывание
        if position.side == "LONG":
            actual_exit = self._apply_slippage(exit_price, "SHORT")
        else:
            actual_exit = self._apply_slippage(exit_price, "LONG")
        
        qty = position.remaining_qty
        
        # Рассчитать PnL
        if position.side == "LONG":
            pnl = (actual_exit - position.entry_price) * qty
        else:
            pnl = (position.entry_price - actual_exit) * qty
        
        # Комиссии
        fees = self._calculate_fees(qty, actual_exit)
        pnl -= fees
        
        position.realized_pnl += pnl
        position.fees_paid += fees
        position.remaining_qty = 0
        
        # Записать торговлю
        trade = BacktestTrade(
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=exit_time,
            exit_price=actual_exit,
            side=position.side,
            quantity=qty,
            pnl=pnl,
            pnl_pct=(pnl / (position.entry_price * qty)) * 100 if position.entry_price > 0 else 0,
            r_multiple=pnl / (position.initial_risk * qty) if position.initial_risk > 0 else 0,
            exit_reason=reason,
            fees=fees,
            max_drawdown=0,
            max_profit=0,
        )
        self.trades.append(trade)
        
        # Обновить капитал
        self.capital += pnl
        
        # Удалить позицию
        del self.positions[position.symbol]
        
        print(f"[{exit_time}] CLOSE {reason} {position.symbol} qty={qty:.4f} @ {actual_exit:.4f}, PnL={pnl:.2f}")
    
    def _close_all_positions(self, end_time: datetime, reason: str):
        """Закрыть все открытые позиции."""
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            # Использовать цену закрытия последней свечи
            self._close_position(
                position=position,
                exit_price=position.entry_price,  # Будет заменено на реальную цену
                exit_time=end_time,
                reason=reason,
                partial=False,
            )
    
    def _calculate_unrealized_pnl(self, current_ts: int, candles_m30: Dict[str, List[dict]]) -> float:
        """Рассчитать нереализованный PnL всех позиций."""
        total_unrealized = 0.0
        
        for symbol, position in self.positions.items():
            if symbol not in candles_m30:
                continue
            
            # Найти последнюю доступную цену
            candle = None
            for c in reversed(candles_m30[symbol]):
                if c['timestamp'] <= current_ts:
                    candle = c
                    break
            
            if not candle:
                continue
            
            current_price = candle['close']
            
            if position.side == "LONG":
                unrealized = (current_price - position.entry_price) * position.remaining_qty
            else:
                unrealized = (position.entry_price - current_price) * position.remaining_qty
            
            total_unrealized += unrealized
        
        return total_unrealized
    
    def _calculate_metrics(self):
        """Рассчитать итоговые метрики бэктеста."""
        if not self.trades:
            return
        
        self.metrics.total_trades = len(self.trades)
        
        winning = [t for t in self.trades if t.pnl > 0]
        losing = [t for t in self.trades if t.pnl <= 0]
        
        self.metrics.winning_trades = len(winning)
        self.metrics.losing_trades = len(losing)
        
        if self.metrics.total_trades > 0:
            self.metrics.win_rate = self.metrics.winning_trades / self.metrics.total_trades
        
        self.metrics.total_pnl = sum(t.pnl for t in self.trades)
        self.metrics.gross_profit = sum(t.pnl for t in winning)
        self.metrics.gross_loss = abs(sum(t.pnl for t in losing))
        
        if self.metrics.gross_loss > 0:
            self.metrics.profit_factor = self.metrics.gross_profit / self.metrics.gross_loss
        
        if winning:
            self.metrics.average_win = self.metrics.gross_profit / len(winning)
            self.metrics.largest_win = max(t.pnl for t in winning)
        
        if losing:
            self.metrics.average_loss = self.metrics.gross_loss / len(losing)
            self.metrics.largest_loss = min(t.pnl for t in losing)
        
        # R-multiple
        r_multiples = [t.r_multiple for t in self.trades]
        if r_multiples:
            self.metrics.average_r = sum(r_multiples) / len(r_multiples)
        
        # Expectancy
        if self.metrics.total_trades > 0:
            self.metrics.expectancy = (
                (self.metrics.win_rate * self.metrics.average_win) -
                ((1 - self.metrics.win_rate) * self.metrics.average_loss)
            )
        
        # Consecutive wins/losses
        consec_wins = 0
        consec_losses = 0
        max_wins = 0
        max_losses = 0
        
        for trade in self.trades:
            if trade.pnl > 0:
                consec_wins += 1
                consec_losses = 0
                if consec_wins > max_wins:
                    max_wins = consec_wins
            else:
                consec_losses += 1
                consec_wins = 0
                if consec_losses > max_losses:
                    max_losses = consec_losses
        
        self.metrics.max_consecutive_wins = max_wins
        self.metrics.max_consecutive_losses = max_losses
        
        # Статистика выходов
        self.metrics.tp1_hits = sum(1 for t in self.trades if t.exit_reason == "TP1")
        self.metrics.tp2_hits = sum(1 for t in self.trades if t.exit_reason == "TP2")
        self.metrics.tp3_hits = sum(1 for t in self.trades if t.exit_reason == "TP3")
        self.metrics.sl_hits = sum(1 for t in self.trades if t.exit_reason == "SL")
        self.metrics.choch_exits = sum(1 for t in self.trades if t.exit_reason == "CHoCH")
        self.metrics.eos_exits = sum(1 for t in self.trades if t.exit_reason == "EOS")
        
        # Drawdown из equity curve
        if self.equity_curve:
            peak = self.initial_capital
            max_dd = 0
            for _, equity in self.equity_curve:
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd
            
            self.metrics.max_drawdown = max_dd
            if peak > 0:
                self.metrics.max_drawdown_pct = (max_dd / peak) * 100
        
        # Trade durations
        durations = [(t.exit_time - t.entry_time) for t in self.trades]
        if durations:
            self.metrics.avg_trade_duration = sum(durations, timedelta(0)) / len(durations)
            self.metrics.max_trade_duration = max(durations)
    
    def print_report(self):
        """Вывести отчет о бэктесте."""
        print("\n" + "="*60)
        print("BACKTEST REPORT - ATS-SMT PRO")
        print("="*60)
        print(f"\nInitial Capital: ${self.initial_capital:,.2f}")
        print(f"Final Capital: ${self.capital:,.2f}")
        print(f"Total PnL: ${self.metrics.total_pnl:,.2f}")
        print(f"Total Return: {((self.capital - self.initial_capital) / self.initial_capital * 100):.2f}%")
        
        print(f"\n{'TRADE STATISTICS':^60}")
        print("-"*60)
        print(f"Total Trades: {self.metrics.total_trades}")
        print(f"Winning Trades: {self.metrics.winning_trades} ({self.metrics.win_rate*100:.1f}%)")
        print(f"Losing Trades: {self.metrics.losing_trades}")
        print(f"Profit Factor: {self.metrics.profit_factor:.2f}")
        print(f"Expectancy: ${self.metrics.expectancy:.2f}")
        
        print(f"\n{'PERFORMANCE':^60}")
        print("-"*60)
        print(f"Average Win: ${self.metrics.average_win:.2f}")
        print(f"Average Loss: ${self.metrics.average_loss:.2f}")
        print(f"Average R: {self.metrics.average_r:.2f}R")
        print(f"Largest Win: ${self.metrics.largest_win:.2f}")
        print(f"Largest Loss: ${self.metrics.largest_loss:.2f}")
        print(f"Max Consecutive Wins: {self.metrics.max_consecutive_wins}")
        print(f"Max Consecutive Losses: {self.metrics.max_consecutive_losses}")
        
        print(f"\n{'RISK':^60}")
        print("-"*60)
        print(f"Max Drawdown: ${self.metrics.max_drawdown:.2f} ({self.metrics.max_drawdown_pct:.1f}%)")
        
        print(f"\n{'EXIT REASONS':^60}")
        print("-"*60)
        print(f"TP1 Hits: {self.metrics.tp1_hits}")
        print(f"TP2 Hits: {self.metrics.tp2_hits}")
        print(f"TP3 Hits: {self.metrics.tp3_hits}")
        print(f"SL Hits: {self.metrics.sl_hits}")
        print(f"CHoCH Exits: {self.metrics.choch_exits}")
        print(f"EOS Exits: {self.metrics.eos_exits}")
        
        print("\n" + "="*60)
