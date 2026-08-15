"""
Order Manager for ATS-SMT Pro Trading Bot.

Manages order lifecycle, state machine, and idempotency.
Ensures one signal never creates duplicate orders.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.persistence.models import (
    Order,
    OrderStatus,
    Signal,
    Position,
    Symbol as SymbolModel,
)
from backend.core.persistence.database import AsyncSessionLocal
from backend.config.settings import config

logger = logging.getLogger(__name__)


class OrderAction(Enum):
    CREATE = "CREATE"
    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    FILL = "FILL"
    REJECT = "REJECT"


@dataclass
class OrderResult:
    """Result of order operation."""
    success: bool
    message: str
    order_id: Optional[str] = None
    order: Optional[Order] = None
    action: Optional[OrderAction] = None
    details: Dict[str, Any] = field(default_factory=dict)


class OrderManager:
    """
    Manages trading orders throughout their lifecycle.
    
    Responsibilities:
    - Create orders from validated signals
    - Track order state machine
    - Ensure idempotency (no duplicate orders per signal)
    - Update order status from exchange responses
    - Link orders to positions
    - Handle partial fills
    - Cancel orders
    """
    
    def __init__(self):
        self._order_cache: Dict[str, Order] = {}
        self._signal_order_map: Dict[int, str] = {}  # signal_id -> order_id
    
    async def create_order_from_signal(
        self,
        signal: Signal,
        exchange_id: int,
        symbol_id: int,
        side: str,
        order_type: str,
        quantity: float,
        session: AsyncSession,
        price: Optional[float] = None,
    ) -> OrderResult:
        """Create an order from a validated signal."""
        
        # Check for existing order for this signal
        if signal.id in self._signal_order_map:
            existing_order_id = self._signal_order_map[signal.id]
            stmt = select(Order).where(Order.order_id == existing_order_id)
            result = await session.execute(stmt)
            existing_order = result.scalar_one_or_none()
            
            if existing_order:
                return OrderResult(
                    success=False,
                    message=f"Order already exists for signal {signal.signal_id}",
                    order_id=existing_order_id,
                    order=existing_order,
                    details={"duplicate": True}
                )
        
        # Check database for existing order
        stmt = select(Order).where(Order.signal_id == signal.id)
        result = await session.execute(stmt)
        existing_db_order = result.scalar_one_or_none()
        
        if existing_db_order:
            self._signal_order_map[signal.id] = existing_db_order.order_id
            return OrderResult(
                success=False,
                message=f"Order already exists in database for signal {signal.signal_id}",
                order_id=existing_db_order.order_id,
                order=existing_db_order,
                details={"duplicate": True, "from_db": True}
            )
        
        # Create new order
        order_id = str(uuid.uuid4())
        client_order_id = f"SMT_{signal.signal_id[:20]}"
        
        order = Order(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol_id=symbol_id,
            exchange_id=exchange_id,
            signal_id=signal.id,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            remaining_quantity=quantity,
            status=OrderStatus.ORDER_PENDING,
            strategy="SMT_PRO_V2",
        )
        
        session.add(order)
        await session.flush()
        
        # Update signal with order reference
        signal.order_id = order.id
        signal.status = "processed"
        signal.processed = True
        
        # Cache order
        self._order_cache[order_id] = order
        self._signal_order_map[signal.id] = order_id
        
        logger.info(
            f"Order created: {order_id} | {side} {quantity} @ {price or 'MARKET'} | "
            f"Signal: {signal.signal_id}"
        )
        
        return OrderResult(
            success=True,
            message=f"Order {order_id} created successfully",
            order_id=order_id,
            order=order,
            action=OrderAction.CREATE,
            details={
                "order_id": order_id,
                "client_order_id": client_order_id,
                "side": side,
                "quantity": quantity,
                "price": price,
                "signal_id": signal.signal_id,
            }
        )
    
    async def submit_order_to_exchange(
        self,
        order: Order,
        exchange_adapter,
        session: AsyncSession
    ) -> OrderResult:
        """Submit order to exchange."""
        
        if order.status not in [OrderStatus.ORDER_PENDING]:
            return OrderResult(
                success=False,
                message=f"Order cannot be submitted: status={order.status}",
                order_id=order.order_id,
                order=order
            )
        
        try:
            # Submit via exchange adapter
            exchange_response = await exchange_adapter.create_order(
                symbol=order.symbol.exchange_symbol if hasattr(order.symbol, 'exchange_symbol') else order.symbol.canonical_symbol,
                side=order.side.lower(),
                order_type=order.order_type,
                quantity=order.quantity,
                price=order.price,
                client_order_id=order.client_order_id,
            )
            
            # Update order with exchange response
            order.status = OrderStatus.ORDER_SUBMITTED
            order.submitted_at = datetime.utcnow()
            
            if exchange_response.get('id'):
                order.exchange_order_id = exchange_response['id']
            
            if exchange_response.get('status') == 'filled':
                order.status = OrderStatus.ORDER_FILLED
                order.filled_at = datetime.utcnow()
                order.filled_quantity = order.quantity
                order.remaining_quantity = 0
                
                if exchange_response.get('average'):
                    order.avg_fill_price = exchange_response['average']
                
                if exchange_response.get('fee'):
                    order.fees = exchange_response['fee'].get('cost', 0)
                    order.fee_currency = exchange_response['fee'].get('currency', 'USDT')
            
            elif exchange_response.get('status') == 'closed':
                order.status = OrderStatus.ORDER_FILLED
            
            elif exchange_response.get('status') == 'canceled':
                order.status = OrderStatus.ORDER_CANCELLED
                order.cancelled_at = datetime.utcnow()
            
            elif exchange_response.get('status') == 'rejected':
                order.status = OrderStatus.ORDER_REJECTED
                order.reject_reason = str(exchange_response.get('info', 'Unknown rejection'))
            
            await session.flush()
            
            logger.info(
                f"Order submitted: {order.order_id} | Exchange ID: {order.exchange_order_id} | "
                f"Status: {order.status.value}"
            )
            
            return OrderResult(
                success=True,
                message=f"Order submitted to exchange",
                order_id=order.order_id,
                order=order,
                action=OrderAction.SUBMIT,
                details={
                    "exchange_order_id": order.exchange_order_id,
                    "status": order.status.value,
                    "response": exchange_response,
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to submit order {order.order_id}: {e}")
            order.status = OrderStatus.ORDER_REJECTED
            order.reject_reason = str(e)
            await session.flush()
            
            return OrderResult(
                success=False,
                message=f"Failed to submit order: {e}",
                order_id=order.order_id,
                order=order,
                action=OrderAction.REJECT,
                details={"error": str(e)}
            )
    
    async def update_order_status(
        self,
        order_id: str,
        new_status: OrderStatus,
        session: AsyncSession,
        filled_qty: Optional[float] = None,
        avg_price: Optional[float] = None,
        fees: Optional[float] = None,
        exchange_order_id: Optional[str] = None,
    ) -> OrderResult:
        """Update order status from exchange feedback."""
        
        stmt = select(Order).where(Order.order_id == order_id)
        result = await session.execute(stmt)
        order = result.scalar_one_or_none()
        
        if not order:
            return OrderResult(
                success=False,
                message=f"Order {order_id} not found"
            )
        
        old_status = order.status
        order.status = new_status
        
        if filled_qty is not None:
            order.filled_quantity = filled_qty
            order.remaining_quantity = order.quantity - filled_qty
        
        if avg_price is not None:
            order.avg_fill_price = avg_price
        
        if fees is not None:
            order.fees = fees
        
        if exchange_order_id is not None:
            order.exchange_order_id = exchange_order_id
        
        if new_status == OrderStatus.ORDER_FILLED:
            order.filled_at = datetime.utcnow()
        elif new_status == OrderStatus.ORDER_CANCELLED:
            order.cancelled_at = datetime.utcnow()
        
        await session.flush()
        
        logger.info(
            f"Order status updated: {order_id} | {old_status.value} -> {new_status.value}"
        )
        
        return OrderResult(
            success=True,
            message=f"Order status updated to {new_status.value}",
            order_id=order_id,
            order=order,
            details={
                "old_status": old_status.value,
                "new_status": new_status.value,
                "filled_qty": order.filled_quantity,
            }
        )
    
    async def cancel_order(
        self,
        order_id: str,
        exchange_adapter,
        session: AsyncSession
    ) -> OrderResult:
        """Cancel an open order."""
        
        stmt = select(Order).where(Order.order_id == order_id)
        result = await session.execute(stmt)
        order = result.scalar_one_or_none()
        
        if not order:
            return OrderResult(
                success=False,
                message=f"Order {order_id} not found"
            )
        
        if order.status not in [OrderStatus.ORDER_PENDING, OrderStatus.ORDER_SUBMITTED]:
            return OrderResult(
                success=False,
                message=f"Order cannot be cancelled: status={order.status.value}",
                order_id=order_id,
                order=order
            )
        
        try:
            if order.exchange_order_id:
                await exchange_adapter.cancel_order(
                    symbol=order.symbol.exchange_symbol if hasattr(order.symbol, 'exchange_symbol') else order.symbol.canonical_symbol,
                    order_id=order.exchange_order_id,
                )
            
            order.status = OrderStatus.ORDER_CANCELLED
            order.cancelled_at = datetime.utcnow()
            await session.flush()
            
            logger.info(f"Order cancelled: {order_id}")
            
            return OrderResult(
                success=True,
                message="Order cancelled successfully",
                order_id=order_id,
                order=order,
                action=OrderAction.CANCEL
            )
            
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return OrderResult(
                success=False,
                message=f"Failed to cancel order: {e}",
                order_id=order_id,
                details={"error": str(e)}
            )
    
    async def get_order_by_id(self, order_id: str, session: AsyncSession) -> Optional[Order]:
        """Get order by ID."""
        stmt = select(Order).where(Order.order_id == order_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_orders_by_signal(
        self,
        signal_id: int,
        session: AsyncSession
    ) -> List[Order]:
        """Get all orders for a signal."""
        stmt = select(Order).where(Order.signal_id == signal_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_open_orders(
        self,
        exchange_id: Optional[int] = None,
        symbol_id: Optional[int] = None,
        session: AsyncSession = None
    ) -> List[Order]:
        """Get all open orders."""
        conditions = [
            Order.status.in_([
                OrderStatus.ORDER_PENDING,
                OrderStatus.ORDER_SUBMITTED,
                OrderStatus.ORDER_PARTIALLY_FILLED
            ])
        ]
        
        if exchange_id:
            conditions.append(Order.exchange_id == exchange_id)
        
        if symbol_id:
            conditions.append(Order.symbol_id == symbol_id)
        
        stmt = select(Order).where(and_(*conditions))
        result = await session.execute(stmt)
        return list(result.scalars().all())
    
    def get_order_status_summary(self, orders: List[Order]) -> Dict[str, Any]:
        """Get summary of order statuses."""
        
        status_counts = {}
        for order in orders:
            status = order.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
        
        total_fees = sum(o.fees or 0 for o in orders)
        
        return {
            "total_orders": len(orders),
            "by_status": status_counts,
            "total_fees": total_fees,
        }


# Global order manager instance
order_manager = OrderManager()


def get_order_manager() -> OrderManager:
    """Get global order manager instance."""
    return order_manager
