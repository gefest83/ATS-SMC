"""
SQLAlchemy models for the ATS.
Includes tables for trades, positions, orders, logs, analytics, and strategy performance.
"""
import uuid
from enum import Enum as PyEnum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Numeric,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    UniqueConstraint,
    Integer,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class ExchangeEnum(str, PyEnum):
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    BITGET = "bitget"
    MEXC = "mexc"
    KUCOIN = "kucoin"
    GATEIO = "gateio"


class OrderStatusEnum(str, PyEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    PARTIAL = "partial"
    STUCK = "stuck"
    REJECTED = "rejected"


class PositionStatusEnum(str, PyEnum):
    OPEN = "open"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"
    BREAKEVEN = "breakeven"


class Trade(Base):
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(Enum(ExchangeEnum, values_callable=lambda enum_cls: [item.value for item in enum_cls]), nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)  # "buy" or "sell"
    order_type = Column(String, nullable=False)
    quantity = Column(Numeric(28, 18), nullable=False)
    price = Column(Numeric(28, 18), nullable=False)
    cost = Column(Numeric(28, 18), nullable=False)
    fee = Column(Numeric(28, 18), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    strategy = Column(String, nullable=True)
    signal_reason = Column(Text, nullable=True)
    exit_reason = Column(String, nullable=True)
    pnl = Column(Numeric(28, 18), nullable=True)
    pnl_percent = Column(Numeric(5, 4), nullable=True)
    sl_pipped = Column(Numeric(28, 18), nullable=True)
    tp_pipped = Column(Numeric(28, 18), nullable=True)
    rr = Column(Numeric(5, 4), nullable=True)
    hold_time_seconds = Column(Integer, nullable=True)
    screenshot_path = Column(String, nullable=True)
    # One aggregate Trade record is created when a Position is closed.
    # Keeping the position id makes close/recovery idempotent across restarts.
    position_id = Column(UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True, unique=True)


class Position(Base):
    __tablename__ = "positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(Enum(ExchangeEnum, values_callable=lambda enum_cls: [item.value for item in enum_cls]), nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    entry_price = Column(Numeric(28, 18), nullable=False)
    quantity = Column(Numeric(28, 18), nullable=False)
    sl_price = Column(Numeric(28, 18), nullable=True)
    tp1_price = Column(Numeric(28, 18), nullable=True)
    tp2_price = Column(Numeric(28, 18), nullable=True)
    tp3_price = Column(Numeric(28, 18), nullable=True)
    entry_time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status = Column(Enum(PositionStatusEnum), default=PositionStatusEnum.OPEN)
    trailing_enabled = Column(Boolean, default=False)
    breakeven_enabled = Column(Boolean, default=False)
    trail_stop_price = Column(Numeric(28, 18), nullable=True)
    current_pnl = Column(Numeric(28, 18), nullable=True)
    current_rr = Column(Numeric(5, 4), nullable=True)
    risk_percent = Column(Numeric(5, 4), nullable=True)
    strategy = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)  # flexible extra fields


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("exchange", "order_id", name="uq_orders_exchange_order_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(Enum(ExchangeEnum, values_callable=lambda enum_cls: [item.value for item in enum_cls]), nullable=False)
    symbol = Column(String, nullable=False)
    order_id = Column(String, nullable=False)
    client_order_id = Column(String, nullable=True)
    side = Column(String, nullable=False)
    order_type = Column(String, nullable=False)
    quantity = Column(Numeric(28, 18), nullable=False)
    price = Column(Numeric(28, 18), nullable=True)
    status = Column(Enum(OrderStatusEnum), default=OrderStatusEnum.OPEN)
    filled_quantity = Column(Numeric(28, 18), default=0)
    avg_price = Column(Numeric(28, 18), nullable=True)
    fee_cost = Column(Numeric(28, 18), nullable=True)
    fee_currency = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    position_id = Column(UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True)
    position = relationship("Position", backref="orders")


class RuntimeState(Base):
    """Durable runtime state needed to survive process restarts."""

    __tablename__ = "runtime_state"

    scope = Column(String, primary_key=True)
    current_equity = Column(Numeric(28, 18), nullable=False)
    daily_start_equity = Column(Numeric(28, 18), nullable=False)
    daily_loss = Column(Numeric(28, 18), nullable=False, default=0)
    open_trades = Column(Integer, nullable=False, default=0)
    equity_day = Column(String, nullable=False)
    paper_position_json = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    level = Column(String, nullable=False)
    module = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    extra_json = Column(Text, nullable=True)


class Analytics(Base):
    __tablename__ = "analytics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    metric_name = Column(String, nullable=False)
    metric_value = Column(Float, nullable=False)
    tags_json = Column(Text, nullable=True)  # e.g., {"strategy": "ict"}


class StrategyPerformance(Base):
    __tablename__ = "strategy_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String, nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    total_pnl = Column(Numeric(28, 18), default=0)
    max_drawdown = Column(Numeric(28, 18), default=0)
    avg_rr = Column(Numeric(5, 4), default=0)
    sharpe_ratio = Column(Numeric(5, 4), default=0)


# Event listener to auto‑populate created_at / updated_at
@event.listens_for(Base, "before_insert")
def receive_before_insert(mapper, connection, target):
    if hasattr(target, "created_at") and target.created_at is None:
        target.created_at = datetime.now(timezone.utc)
    if hasattr(target, "updated_at") and target.updated_at is None:
        target.updated_at = datetime.now(timezone.utc)


@event.listens_for(Base, "before_update")
def receive_before_update(mapper, connection, target):
    if hasattr(target, "updated_at"):
        target.updated_at = datetime.now(timezone.utc)