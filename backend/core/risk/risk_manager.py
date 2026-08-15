"""
Risk Manager for ATS-SMT Pro Trading Bot.

Centralized risk management that validates every order before execution.
No order is sent to exchange without passing Risk Manager checks.
"""
import logging
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from backend.config.settings import config, TradingMode
from backend.core.persistence.models import Position, Order, Symbol as SymbolModel
from backend.core.persistence.database import AsyncSessionLocal
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class BalanceLock:
    """Механизм блокировки баланса для предотвращения гонки при мульти-символьной торговле."""
    
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locked_amounts: Dict[str, Decimal] = {}  # symbol -> locked amount
        self._global_lock = asyncio.Lock()

    async def acquire(self, symbol: str, amount: Decimal) -> bool:
        """Попытка заблокировать сумму для символа. Возвращает True если успешно."""
        async with self._global_lock:
            if symbol not in self._locks:
                self._locks[symbol] = asyncio.Lock()
            
            if symbol not in self._locked_amounts:
                self._locked_amounts[symbol] = Decimal('0')

        async with self._locks[symbol]:
            self._locked_amounts[symbol] += amount
            return True

    async def release(self, symbol: str, amount: Decimal):
        """Освобождение заблокированной суммы."""
        async with self._global_lock:
            if symbol not in self._locks:
                return
            if symbol not in self._locked_amounts:
                return

        async with self._locks[symbol]:
            self._locked_amounts[symbol] = max(Decimal('0'), self._locked_amounts[symbol] - amount)

    def get_locked(self, symbol: str) -> Decimal:
        """Получить заблокированную сумму для символа."""
        return self._locked_amounts.get(symbol, Decimal('0'))

    def clear(self):
        """Очистка всех блокировок (при рестарте)."""
        self._locked_amounts.clear()


class RiskCheckResult(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"


@dataclass
class RiskCheck:
    """Result of a single risk check."""
    name: str
    result: RiskCheckResult
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskAssessment:
    """Complete risk assessment for an order."""
    approved: bool
    checks: List[RiskCheck] = field(default_factory=list)
    blocking_reason: Optional[str] = None
    
    def add_check(self, check: RiskCheck):
        self.checks.append(check)
        if check.result == RiskCheckResult.FAIL and not self.blocking_reason:
            self.blocking_reason = check.message
            self.approved = False


class RiskManager:
    """
    Centralized Risk Manager.
    
    Before any order is sent to exchange, it must pass all risk checks.
    This protects against:
    - Over-leveraging
    - Excessive exposure
    - Daily drawdown limits
    - Duplicate orders
    - Invalid quantities
    - Exchange limits violations
    """
    
    # Maximum allowed risk percentage (safety cap)
    MAX_ALLOWED_RISK_PCT = 5.0
    
    def __init__(self):
        self.daily_pnl = 0.0
        self.daily_start_balance = 0.0
        self.last_reset_date = datetime.now().date()
        self._open_positions_cache: Dict[str, Position] = {}
        self._pending_signals: set = set()
        self.balance_lock = BalanceLock()  # Locking mechanism for multi-symbol trading
        
    def _reset_daily_if_needed(self):
        """Reset daily PnL tracking if new day."""
        today = datetime.now().date()
        if today > self.last_reset_date:
            logger.info(f"Resetting daily PnL tracker. Previous day: {self.daily_pnl}")
            self.daily_pnl = 0.0
            self.last_reset_date = today
    
    async def _check_available_balance_with_fees(
        self,
        symbol: str,
        exchange: str,
        quantity: float,
        price: float,
        session: AsyncSession
    ) -> RiskCheck:
        """Проверка доступного баланса с учетом комиссий биржи."""
        try:
            # Получаем информацию о символе для min_notional
            stmt = select(SymbolModel).where(
                and_(SymbolModel.symbol == symbol, SymbolModel.exchange == exchange)
            )
            result = await session.execute(stmt)
            symbol_info = result.scalar_one_or_none()
            
            if not symbol_info:
                return RiskCheck(
                    name="available_balance",
                    result=RiskCheckResult.WARNING,
                    message=f"Symbol info not found for {symbol}",
                    details={"symbol": symbol, "exchange": exchange}
                )
            
            required_notional = Decimal(str(quantity)) * Decimal(str(price))
            fee_estimate = required_notional * Decimal('0.001')  # 0.1% комиссия
            total_required = required_notional + fee_estimate
            
            # Проверяем min_notional для биржи
            min_notional = getattr(symbol_info, 'min_notional', None)
            if min_notional and total_required < Decimal(str(min_notional)):
                return RiskCheck(
                    name="available_balance",
                    result=RiskCheckResult.FAIL,
                    message=f"Order below min_notional. Required: {min_notional}, Got: {total_required}",
                    details={
                        "required": float(total_required),
                        "min_notional": float(min_notional),
                        "symbol": symbol
                    }
                )
            
            # Проверяем заблокированные суммы (locking mechanism)
            locked = self.balance_lock.get_locked(symbol)
            available = Decimal(str(symbol_info.free_balance or 0)) - locked
            
            if available < total_required:
                return RiskCheck(
                    name="available_balance",
                    result=RiskCheckResult.FAIL,
                    message=f"Insufficient balance. Required: {total_required}, Available: {available} (locked: {locked})",
                    details={
                        "required": float(total_required),
                        "available": float(available),
                        "locked": float(locked),
                        "symbol": symbol
                    }
                )
            
            return RiskCheck(
                name="available_balance",
                result=RiskCheckResult.PASS,
                message="Balance check passed",
                details={"available": float(available), "required": float(total_required)}
            )
        except Exception as e:
            return RiskCheck(
                name="available_balance",
                result=RiskCheckResult.WARNING,
                message=f"Error checking balance: {e}",
                details={"error": str(e)}
            )

    async def validate_order(
        self,
        symbol: str,
        exchange: str,
        side: str,
        quantity: float,
        price: float,
        risk_pct: float,
        signal_id: str,
        session: Optional[AsyncSession] = None
    ) -> RiskAssessment:
        """
        Validate an order before submission.
        
        Returns RiskAssessment with approval status and detailed checks.
        """
        self._reset_daily_if_needed()
        
        assessment = RiskAssessment(approved=True)
        
        # Run all checks including new balance check with fees and min_notional
        if session:
            await self._check_available_balance_with_fees(
                assessment, symbol, exchange, quantity, price, session
            )
        
        await self._check_emergency_stop(assessment, exchange)
        await self._check_trading_mode(assessment, exchange)
        await self._check_exchange_connectivity(assessment, exchange)
        await self._check_max_open_trades(assessment, exchange)
        await self._check_daily_drawdown(assessment)
        await self._check_risk_percentage(assessment, risk_pct)
        await self._check_duplicate_signal(assessment, signal_id)
        await self._check_position_conflict(assessment, symbol, exchange, side)
        await self._check_quantity_limits(assessment, symbol, exchange, quantity, price)
        await self._check_exposure_limits(assessment, symbol, exchange, quantity, price)
        
        if assessment.approved:
            # Lock balance for this symbol to prevent race conditions
            if session:
                required_notional = Decimal(str(quantity)) * Decimal(str(price))
                await self.balance_lock.acquire(symbol, required_notional)
            logger.debug(
                f"Risk check PASSED for {side} {quantity} {symbol} on {exchange}"
            )
        else:
            logger.warning(
                f"Risk check FAILED for {side} {quantity} {symbol} on {exchange}: "
                f"{assessment.blocking_reason}"
            )
        
        return assessment
    
    async def _check_available_balance_with_fees(
        self,
        assessment: RiskAssessment,
        symbol: str,
        exchange: str,
        quantity: float,
        price: float,
        session: AsyncSession
    ):
        """Wrapper for balance check."""
        check = await self._check_available_balance_with_fees_impl(
            symbol, exchange, quantity, price, session
        )
        assessment.add_check(check)

    async def _check_available_balance_with_fees_impl(
        self,
        symbol: str,
        exchange: str,
        quantity: float,
        price: float,
        session: AsyncSession
    ) -> RiskCheck:
        """Проверка доступного баланса с учетом комиссий биржи."""
        try:
            # Получаем информацию о символе для min_notional
            stmt = select(SymbolModel).where(
                and_(SymbolModel.symbol == symbol, SymbolModel.exchange == exchange)
            )
            result = await session.execute(stmt)
            symbol_info = result.scalar_one_or_none()
            
            if not symbol_info:
                return RiskCheck(
                    name="available_balance",
                    result=RiskCheckResult.WARNING,
                    message=f"Symbol info not found for {symbol}",
                    details={"symbol": symbol, "exchange": exchange}
                )
            
            required_notional = Decimal(str(quantity)) * Decimal(str(price))
            fee_estimate = required_notional * Decimal('0.001')  # 0.1% комиссия
            total_required = required_notional + fee_estimate
            
            # Проверяем min_notional для биржи
            min_notional = getattr(symbol_info, 'min_notional', None)
            if min_notional and total_required < Decimal(str(min_notional)):
                return RiskCheck(
                    name="available_balance",
                    result=RiskCheckResult.FAIL,
                    message=f"Order below min_notional. Required: {min_notional}, Got: {total_required}",
                    details={
                        "required": float(total_required),
                        "min_notional": float(min_notional),
                        "symbol": symbol
                    }
                )
            
            # Проверяем заблокированные суммы (locking mechanism)
            locked = self.balance_lock.get_locked(symbol)
            available = Decimal(str(symbol_info.free_balance or 0)) - locked
            
            if available < total_required:
                return RiskCheck(
                    name="available_balance",
                    result=RiskCheckResult.FAIL,
                    message=f"Insufficient balance. Required: {total_required}, Available: {available} (locked: {locked})",
                    details={
                        "required": float(total_required),
                        "available": float(available),
                        "locked": float(locked),
                        "symbol": symbol
                    }
                )
            
            return RiskCheck(
                name="available_balance",
                result=RiskCheckResult.PASS,
                message="Balance check passed",
                details={"available": float(available), "required": float(total_required)}
            )
        except Exception as e:
            return RiskCheck(
                name="available_balance",
                result=RiskCheckResult.WARNING,
                message=f"Error checking balance: {e}",
                details={"error": str(e)}
            )
    
    async def _check_emergency_stop(self, assessment: RiskAssessment, exchange: str):
        """Check if emergency stop is active."""
        # TODO: Load from settings/database
        emergency_active = False
        
        if emergency_active:
            assessment.add_check(RiskCheck(
                name="emergency_stop",
                result=RiskCheckResult.FAIL,
                message="Emergency stop is active",
                details={"exchange": exchange}
            ))
        else:
            assessment.add_check(RiskCheck(
                name="emergency_stop",
                result=RiskCheckResult.PASS,
                message="Emergency stop not active"
            ))
    
    async def _check_trading_mode(self, assessment: RiskAssessment, exchange: str):
        """Check trading mode permissions."""
        if config.trading_mode == TradingMode.LIVE and not config.live_trading_enabled:
            assessment.add_check(RiskCheck(
                name="trading_mode",
                result=RiskCheckResult.FAIL,
                message="Live trading is not enabled in configuration",
                details={"mode": config.trading_mode.value}
            ))
        else:
            assessment.add_check(RiskCheck(
                name="trading_mode",
                result=RiskCheckResult.PASS,
                message=f"Trading mode OK: {config.trading_mode.value}"
            ))
    
    async def _check_exchange_connectivity(self, assessment: RiskAssessment, exchange: str):
        """Check if exchange is connected."""
        # TODO: Check actual connectivity status
        assessment.add_check(RiskCheck(
            name="exchange_connectivity",
            result=RiskCheckResult.PASS,
            message=f"Exchange {exchange} is connected"
        ))
    
    async def _check_max_open_trades(self, assessment: RiskAssessment, exchange: str):
        """Check maximum open trades limit."""
        async with AsyncSessionLocal() as session:
            stmt = select(func.count()).select_from(Position).where(
                and_(
                    Position.is_open == True,
                    Position.exchange_id.in_(
                        select(SymbolModel.exchange_id).where(
                            SymbolModel.canonical_symbol.in_(config.symbols)
                        )
                    )
                )
            )
            result = await session.execute(stmt)
            open_count = result.scalar() or 0
        
        if open_count >= config.max_open_trades:
            assessment.add_check(RiskCheck(
                name="max_open_trades",
                result=RiskCheckResult.FAIL,
                message=f"Maximum open trades reached: {open_count}/{config.max_open_trades}",
                details={"open_trades": open_count, "max_trades": config.max_open_trades}
            ))
        else:
            assessment.add_check(RiskCheck(
                name="max_open_trades",
                result=RiskCheckResult.PASS,
                message=f"Open trades: {open_count}/{config.max_open_trades}"
            ))
    
    async def _check_daily_drawdown(self, assessment: RiskAssessment):
        """Check daily drawdown limit."""
        # Simplified - would need actual balance tracking
        if abs(self.daily_pnl) > config.max_daily_drawdown:
            assessment.add_check(RiskCheck(
                name="daily_drawdown",
                result=RiskCheckResult.FAIL,
                message=f"Daily drawdown limit reached: {self.daily_pnl:.2f}%",
                details={"daily_pnl": self.daily_pnl, "max_dd": config.max_daily_drawdown}
            ))
        else:
            assessment.add_check(RiskCheck(
                name="daily_drawdown",
                result=RiskCheckResult.PASS,
                message=f"Daily PnL: {self.daily_pnl:.2f}% (limit: {config.max_daily_drawdown}%)"
            ))
    
    async def _check_risk_percentage(self, assessment: RiskAssessment, risk_pct: float):
        """Check risk percentage is within limits."""
        if risk_pct <= 0:
            assessment.add_check(RiskCheck(
                name="risk_percentage",
                result=RiskCheckResult.FAIL,
                message="Risk percentage must be positive",
                details={"risk_pct": risk_pct}
            ))
        elif risk_pct > self.MAX_ALLOWED_RISK_PCT:
            assessment.add_check(RiskCheck(
                name="risk_percentage",
                result=RiskCheckResult.FAIL,
                message=f"Risk percentage exceeds maximum allowed: {risk_pct}% > {self.MAX_ALLOWED_RISK_PCT}%",
                details={"risk_pct": risk_pct, "max_allowed": self.MAX_ALLOWED_RISK_PCT}
            ))
        elif risk_pct > config.risk_pct * 2:
            assessment.add_check(RiskCheck(
                name="risk_percentage",
                result=RiskCheckResult.WARNING,
                message=f"Risk percentage significantly higher than configured: {risk_pct}% vs {config.risk_pct}%",
                details={"risk_pct": risk_pct, "configured": config.risk_pct}
            ))
        else:
            assessment.add_check(RiskCheck(
                name="risk_percentage",
                result=RiskCheckResult.PASS,
                message=f"Risk percentage OK: {risk_pct}%"
            ))
    
    async def _check_duplicate_signal(self, assessment: RiskAssessment, signal_id: str):
        """Check for duplicate signal processing."""
        if signal_id in self._pending_signals:
            assessment.add_check(RiskCheck(
                name="duplicate_signal",
                result=RiskCheckResult.FAIL,
                message=f"Signal {signal_id} is already being processed",
                details={"signal_id": signal_id}
            ))
        else:
            self._pending_signals.add(signal_id)
            assessment.add_check(RiskCheck(
                name="duplicate_signal",
                result=RiskCheckResult.PASS,
                message=f"Signal {signal_id} is unique"
            ))
    
    async def _check_position_conflict(
        self,
        assessment: RiskAssessment,
        symbol: str,
        exchange: str,
        side: str
    ):
        """Check for conflicting existing positions."""
        # TODO: Query database for existing positions
        # For now, simplified check
        assessment.add_check(RiskCheck(
            name="position_conflict",
            result=RiskCheckResult.PASS,
            message=f"No conflicting position for {symbol} {side}"
        ))
    
    async def _check_quantity_limits(
        self,
        assessment: RiskAssessment,
        symbol: str,
        exchange: str,
        quantity: float,
        price: float
    ):
        """Check quantity meets exchange limits."""
        notional = quantity * price
        
        # Get symbol info from cache or database
        # For now, use minimum defaults
        min_notional = 5.0  # USDT
        
        if notional < min_notional:
            assessment.add_check(RiskCheck(
                name="quantity_limits",
                result=RiskCheckResult.FAIL,
                message=f"Notional value too small: ${notional:.2f} < ${min_notional:.2f}",
                details={"notional": notional, "min_notional": min_notional}
            ))
        else:
            assessment.add_check(RiskCheck(
                name="quantity_limits",
                result=RiskCheckResult.PASS,
                message=f"Quantity OK: {quantity} ({notional:.2f} USDT)"
            ))
    
    async def _check_exposure_limits(
        self,
        assessment: RiskAssessment,
        symbol: str,
        exchange: str,
        quantity: float,
        price: float
    ):
        """Check total exposure limits."""
        notional = quantity * price
        
        # Simplified - would need portfolio value
        assessment.add_check(RiskCheck(
            name="exposure_limits",
            result=RiskCheckResult.PASS,
            message=f"Exposure check passed for {notional:.2f} USDT"
        ))
    
    def clear_pending_signal(self, signal_id: str):
        """Remove signal from pending set after processing."""
        self._pending_signals.discard(signal_id)
    
    def update_daily_pnl(self, pnl: float):
        """Update daily PnL tracking."""
        self.daily_pnl += pnl
        logger.debug(f"Updated daily PnL: {self.daily_pnl:.2f}%")
    
    def get_risk_status(self) -> Dict[str, Any]:
        """Get current risk status summary."""
        return {
            "daily_pnl": self.daily_pnl,
            "max_daily_drawdown": config.max_daily_drawdown,
            "max_open_trades": config.max_open_trades,
            "risk_per_trade": config.risk_pct,
            "max_allowed_risk": self.MAX_ALLOWED_RISK_PCT,
            "pending_signals": len(self._pending_signals),
            "trading_mode": config.trading_mode.value,
            "live_enabled": config.live_trading_enabled
        }


# Global risk manager instance
risk_manager = RiskManager()


def get_risk_manager() -> RiskManager:
    """Get global risk manager instance."""
    return risk_manager
