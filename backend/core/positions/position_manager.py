"""
Position Manager for ATS-SMT Pro Trading Bot.

Manages active positions, TP/SL execution, breakeven, and position lifecycle.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.persistence.models import (
    Position,
    PositionSide,
    Order,
    OrderStatus,
    Symbol as SymbolModel,
)
from backend.core.persistence.database import AsyncSessionLocal
from backend.config.settings import config

logger = logging.getLogger(__name__)


class PositionAction(Enum):
    OPEN = "OPEN"
    CLOSE_PARTIAL = "CLOSE_PARTIAL"
    CLOSE_FULL = "CLOSE_FULL"
    UPDATE_SL = "UPDATE_SL"
    UPDATE_TP = "UPDATE_TP"
    ACTIVATE_BE = "ACTIVATE_BE"
    ACTIVATE_TRAIL = "ACTIVATE_TRAIL"


@dataclass
class PositionUpdate:
    """Result of position update operation."""
    success: bool
    message: str
    position_id: Optional[str] = None
    action: Optional[PositionAction] = None
    details: Dict[str, Any] = field(default_factory=dict)


class PositionManager:
    """
    Manages trading positions throughout their lifecycle.
    
    Responsibilities:
    - Create new positions from filled orders
    - Monitor TP/SL levels
    - Execute partial closes (TP1, TP2, TP3)
    - Manage breakeven activation
    - Handle trailing stops
    - Track position PnL
    - Close positions on structure breaks (CHoCH/BOS)
    """
    
    def __init__(self):
        self._position_cache: Dict[str, Position] = {}
    
    async def create_position(
        self,
        exchange_id: int,
        symbol_id: int,
        side: str,
        quantity: float,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        tp3_price: float,
        signal_entry: float,
        initial_risk: float,
        regime: str,
        votes: int,
        strategy_params: Dict[str, Any],
        session: AsyncSession
    ) -> PositionUpdate:
        """Create a new position from a filled order."""
        
        position_id = str(uuid.uuid4())
        position_side = PositionSide.LONG if side == "BUY" else PositionSide.SHORT
        
        # Calculate TP quantities
        tp1_qty = quantity * (config.tp1_pct / 100.0)
        tp2_qty = quantity * (config.tp2_pct / 100.0)
        tp3_qty = quantity * (config.tp3_pct / 100.0)
        
        position = Position(
            position_id=position_id,
            exchange_id=exchange_id,
            symbol_id=symbol_id,
            side=position_side,
            quantity=quantity,
            remaining_quantity=quantity,
            signal_entry=signal_entry,
            actual_entry=entry_price,
            initial_risk=initial_risk,
            sl_price=sl_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            tp3_price=tp3_price,
            tp1_hit=False,
            tp2_hit=False,
            tp3_hit=False,
            tp1_closed_qty=0.0,
            tp2_closed_qty=0.0,
            tp3_closed_qty=0.0,
            breakeven_active=False,
            trailing_active=False,
            strategy="SMT_PRO_V2",
            regime=regime,
            votes=votes,
            strategy_params_snapshot=strategy_params,
            is_open=True,
        )
        
        session.add(position)
        await session.flush()
        
        self._position_cache[position_id] = position
        
        logger.info(
            f"Position created: {position_id} | {side} {quantity} @ {entry_price} | "
            f"SL: {sl_price} | TP1: {tp1_price} | TP2: {tp2_price} | TP3: {tp3_price}"
        )
        
        return PositionUpdate(
            success=True,
            message=f"Position {position_id} created successfully",
            position_id=position_id,
            action=PositionAction.OPEN,
            details={
                "position_id": position_id,
                "side": side,
                "quantity": quantity,
                "entry": entry_price,
                "sl": sl_price,
                "tp1": tp1_price,
                "tp2": tp2_price,
                "tp3": tp3_price,
            }
        )
    
    async def check_tp_levels(
        self,
        current_price: float,
        session: AsyncSession
    ) -> List[PositionUpdate]:
        """Check all open positions against TP levels."""
        updates = []
        
        stmt = select(Position).where(
            and_(
                Position.is_open == True,
                Position.remaining_quantity > 0
            )
        )
        result = await session.execute(stmt)
        positions = result.scalars().all()
        
        for position in positions:
            update = await self._check_single_tp(position, current_price, session)
            if update:
                updates.append(update)
        
        return updates
    
    async def _check_single_tp(
        self,
        position: Position,
        current_price: float,
        session: AsyncSession
    ) -> Optional[PositionUpdate]:
        """Check TP levels for a single position."""
        
        if not position.is_open or position.remaining_quantity <= 0:
            return None
        
        is_long = position.side == PositionSide.LONG
        
        # Check TP1
        if not position.tp1_hit:
            tp1_reached = (
                current_price >= position.tp1_price if is_long
                else current_price <= position.tp1_price
            )
            if tp1_reached and position.tp1_price:
                return await self._close_partial(
                    position, position.tp1_price, config.tp1_pct, "TP1", session
                )
        
        # Check TP2
        if not position.tp2_hit:
            tp2_reached = (
                current_price >= position.tp2_price if is_long
                else current_price <= position.tp2_price
            )
            if tp2_reached and position.tp2_price:
                return await self._close_partial(
                    position, position.tp2_price, config.tp2_pct, "TP2", session
                )
        
        # Check TP3
        if not position.tp3_hit:
            tp3_reached = (
                current_price >= position.tp3_price if is_long
                else current_price <= position.tp3_price
            )
            if tp3_reached and position.tp3_price:
                return await self._close_partial(
                    position, position.tp3_price, config.tp3_pct, "TP3", session
                )
        
        return None
    
    async def _close_partial(
        self,
        position: Position,
        close_price: float,
        close_pct: int,
        reason: str,
        session: AsyncSession
    ) -> PositionUpdate:
        """Execute partial close at TP level."""
        
        close_qty = position.quantity * (close_pct / 100.0)
        
        if close_qty > position.remaining_quantity:
            close_qty = position.remaining_quantity
        
        # Update position
        position.remaining_quantity -= close_qty
        position.unrealized_pnl = self._calculate_pnl(position, close_price)
        
        # Mark TP as hit
        if reason == "TP1":
            position.tp1_hit = True
            position.tp1_closed_qty = close_qty
        elif reason == "TP2":
            position.tp2_hit = True
            position.tp2_closed_qty = close_qty
        elif reason == "TP3":
            position.tp3_hit = True
            position.tp3_closed_qty = close_qty
        
        # Check if fully closed
        if position.remaining_quantity <= 0:
            position.is_open = False
            position.closed_at = datetime.utcnow()
            position.exit_reason = reason
            position.realized_pnl = position.unrealized_pnl
        
        await session.flush()
        
        pnl = self._calculate_pnl(position, close_price)
        
        logger.info(
            f"Partial close {reason}: {position.position_id} | "
            f"Closed {close_qty} @ {close_price} | PnL: {pnl:.2f}"
        )
        
        return PositionUpdate(
            success=True,
            message=f"{reason} reached - closed {close_pct}%",
            position_id=position.position_id,
            action=PositionAction.CLOSE_PARTIAL,
            details={
                "reason": reason,
                "close_price": close_price,
                "close_qty": close_qty,
                "close_pct": close_pct,
                "remaining_qty": position.remaining_quantity,
                "pnl": pnl,
            }
        )
    
    async def check_sl_level(
        self,
        current_price: float,
        session: AsyncSession
    ) -> List[PositionUpdate]:
        """Check all open positions against SL level."""
        updates = []
        
        stmt = select(Position).where(
            and_(
                Position.is_open == True,
                Position.remaining_quantity > 0
            )
        )
        result = await session.execute(stmt)
        positions = result.scalars().all()
        
        for position in positions:
            update = await self._check_single_sl(position, current_price, session)
            if update:
                updates.append(update)
        
        return updates
    
    async def _check_single_sl(
        self,
        position: Position,
        current_price: float,
        session: AsyncSession
    ) -> Optional[PositionUpdate]:
        """Check SL level for a single position."""
        
        if not position.is_open or position.remaining_quantity <= 0:
            return None
        
        is_long = position.side == PositionSide.LONG
        
        sl_reached = (
            current_price <= position.sl_price if is_long
            else current_price >= position.sl_price
        )
        
        if sl_reached:
            return await self._close_full(position, current_price, "SL", session)
        
        return None
    
    async def check_breakeven(
        self,
        current_price: float,
        session: AsyncSession
    ) -> List[PositionUpdate]:
        """Check and activate breakeven for eligible positions."""
        updates = []
        
        if not config.use_breakeven:
            return updates
        
        stmt = select(Position).where(
            and_(
                Position.is_open == True,
                Position.remaining_quantity > 0,
                Position.breakeven_active == False
            )
        )
        result = await session.execute(stmt)
        positions = result.scalars().all()
        
        for position in positions:
            update = await self._check_single_be(position, current_price, session)
            if update:
                updates.append(update)
        
        return updates
    
    async def _check_single_be(
        self,
        position: Position,
        current_price: float,
        session: AsyncSession
    ) -> Optional[PositionUpdate]:
        """Check breakeven activation for a single position."""
        
        is_long = position.side == PositionSide.LONG
        initial_risk = position.initial_risk
        
        # Check if price moved +1R in favor
        be_trigger = (
            position.actual_entry + initial_risk if is_long
            else position.actual_entry - initial_risk
        )
        
        be_triggered = (
            current_price >= be_trigger if is_long
            else current_price <= be_trigger
        )
        
        if be_triggered:
            # Move SL to entry
            position.sl_price = position.actual_entry
            position.breakeven_active = True
            
            await session.flush()
            
            logger.info(
                f"Breakeven activated: {position.position_id} | "
                f"New SL: {position.sl_price}"
            )
            
            return PositionUpdate(
                success=True,
                message="Breakeven activated",
                position_id=position.position_id,
                action=PositionAction.ACTIVATE_BE,
                details={
                    "new_sl": position.sl_price,
                    "entry": position.actual_entry,
                }
            )
        
        return None
    
    async def close_position_on_structure(
        self,
        position_id: str,
        current_price: float,
        reason: str,
        session: AsyncSession
    ) -> PositionUpdate:
        """Close position due to structure break (CHoCH/BOS)."""
        
        stmt = select(Position).where(Position.position_id == position_id)
        result = await session.execute(stmt)
        position = result.scalar_one_or_none()
        
        if not position:
            return PositionUpdate(
                success=False,
                message=f"Position {position_id} not found"
            )
        
        return await self._close_full(position, current_price, reason, session)
    
    async def _close_full(
        self,
        position: Position,
        close_price: float,
        reason: str,
        session: AsyncSession
    ) -> PositionUpdate:
        """Close entire position."""
        
        pnl = self._calculate_pnl(position, close_price)
        
        position.is_open = False
        position.remaining_quantity = 0
        position.closed_at = datetime.utcnow()
        position.exit_reason = reason
        position.realized_pnl = pnl
        position.unrealized_pnl = pnl
        
        await session.flush()
        
        logger.info(
            f"Position closed: {position.position_id} | "
            f"Reason: {reason} | Price: {close_price} | PnL: {pnl:.2f}"
        )
        
        return PositionUpdate(
            success=True,
            message=f"Position closed: {reason}",
            position_id=position.position_id,
            action=PositionAction.CLOSE_FULL,
            details={
                "reason": reason,
                "close_price": close_price,
                "pnl": pnl,
                "entry": position.actual_entry,
            }
        )
    
    def _calculate_pnl(self, position: Position, current_price: float) -> float:
        """Calculate unrealized PnL for a position."""
        
        if position.side == PositionSide.LONG:
            pnl_pct = ((current_price - position.actual_entry) / position.actual_entry) * 100
        else:
            pnl_pct = ((position.actual_entry - current_price) / position.actual_entry) * 100
        
        # Approximate PnL in quote currency
        notional = position.remaining_quantity * current_price
        pnl = notional * (pnl_pct / 100)
        
        return pnl
    
    async def get_open_positions(self, session: AsyncSession) -> List[Position]:
        """Get all open positions."""
        stmt = select(Position).where(
            and_(
                Position.is_open == True,
                Position.remaining_quantity > 0
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_position_by_id(
        self,
        position_id: str,
        session: AsyncSession
    ) -> Optional[Position]:
        """Get position by ID."""
        stmt = select(Position).where(Position.position_id == position_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    
    def get_position_status_summary(self, positions: List[Position]) -> Dict[str, Any]:
        """Get summary of position statuses."""
        
        total_pnl = sum(p.realized_pnl + p.unrealized_pnl for p in positions)
        long_count = sum(1 for p in positions if p.side == PositionSide.LONG)
        short_count = sum(1 for p in positions if p.side == PositionSide.SHORT)
        
        return {
            "total_positions": len(positions),
            "long_positions": long_count,
            "short_positions": short_count,
            "total_pnl": total_pnl,
            "breakeven_active": sum(1 for p in positions if p.breakeven_active),
            "tp1_hit": sum(1 for p in positions if p.tp1_hit),
            "tp2_hit": sum(1 for p in positions if p.tp2_hit),
            "tp3_hit": sum(1 for p in positions if p.tp3_hit),
        }

    async def check_trailing_stop(
        self,
        position: Position,
        current_high: float,
        current_low: float,
        atr: float,
        session: AsyncSession
    ) -> PositionUpdate:
        """
        Проверка и обновление Trailing Stop.
        
        LONG: trailStop = lastSwingLow - ATR * 0.25
        SHORT: trailStop = lastSwingHigh + ATR * 0.25
        
        Trailing stop никогда не движется назад.
        """
        if not position.trailing_active or not config.use_trail:
            return PositionUpdate(
                success=False,
                message="Trailing stop not active",
                position_id=position.position_id
            )
        
        new_sl = None
        
        if position.side == PositionSide.LONG:
            # Для LONG используем swing low - ATR * 0.25
            potential_sl = current_low - (atr * 0.25)
            
            # Trailing stop только повышается (для LONG)
            if potential_sl > position.sl_price:
                new_sl = potential_sl
                logger.info(
                    f"Trailing stop update LONG: {position.position_id} | "
                    f"Old SL: {position.sl_price} | New SL: {new_sl:.4f}"
                )
        else:
            # Для SHORT используем swing high + ATR * 0.25
            potential_sl = current_high + (atr * 0.25)
            
            # Trailing stop только понижается (для SHORT)
            if potential_sl < position.sl_price:
                new_sl = potential_sl
                logger.info(
                    f"Trailing stop update SHORT: {position.position_id} | "
                    f"Old SL: {position.sl_price} | New SL: {new_sl:.4f}"
                )
        
        if new_sl:
            position.sl_price = new_sl
            
            # Сохраняем обновление в БД
            stmt = update(Position).where(
                Position.position_id == position.position_id
            ).values(sl_price=new_sl)
            
            await session.execute(stmt)
            await session.flush()
            
            return PositionUpdate(
                success=True,
                message=f"Trailing stop updated to {new_sl:.4f}",
                position_id=position.position_id,
                details={"new_sl": new_sl, "old_sl": position.sl_price}
            )
        
        return PositionUpdate(
            success=False,
            message="No trailing stop update needed",
            position_id=position.position_id
        )

    async def sync_position_with_exchange(
        self,
        position: Position,
        exchange_position_data: Dict[str, Any],
        session: AsyncSession
    ) -> PositionUpdate:
        """
        Принудительная синхронизация состояния позиции с биржей.
        
        Exchange state имеет приоритет для фактического состояния позиции.
        """
        try:
            exchange_qty = float(exchange_position_data.get('quantity', 0))
            exchange_side = exchange_position_data.get('side', '')
            exchange_entry = float(exchange_position_data.get('entry_price', 0))
            
            # Проверяем расхождения
            qty_diff = abs(position.remaining_quantity - exchange_qty)
            
            if qty_diff > 0.0001:  # Допустимая погрешность
                logger.warning(
                    f"Position sync: {position.position_id} | "
                    f"Local qty: {position.remaining_quantity} | "
                    f"Exchange qty: {exchange_qty} | Diff: {qty_diff}"
                )
                
                # Обновляем локальное состояние
                position.remaining_quantity = exchange_qty
                
                if exchange_qty <= 0:
                    # Позиция закрыта на бирже
                    position.is_open = False
                    position.closed_at = datetime.utcnow()
                    position.exit_reason = "EXCHANGE_SYNC_CLOSE"
                
                await session.flush()
                
                return PositionUpdate(
                    success=True,
                    message=f"Position synced with exchange. New qty: {exchange_qty}",
                    position_id=position.position_id,
                    details={
                        "old_qty": position.remaining_quantity,
                        "new_qty": exchange_qty,
                        "is_open": position.is_open
                    }
                )
            
            return PositionUpdate(
                success=True,
                message="Position already synced",
                position_id=position.position_id
            )
            
        except Exception as e:
            return PositionUpdate(
                success=False,
                message=f"Sync error: {e}",
                position_id=position.position_id,
                details={"error": str(e)}
            )


# Global position manager instance
position_manager = PositionManager()


def get_position_manager() -> PositionManager:
    """Get global position manager instance."""
    return position_manager
