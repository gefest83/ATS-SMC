"""Initial schema — matches the existing Base.metadata.create_all() definition.

This migration is designed to be safe for fresh databases AND for databases
that were already created by init_db.py / create_all().  The downgrade()
drops all tables created by this project.

Revision ID: 0001_initial
Revises: None
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ENUM types (PostgreSQL-specific) ---
    exchange_enum = postgresql.ENUM(
        "binance", "bybit", "okx", "bitget", "mexc", "kucoin", "gateio",
        name="exchangeenum", create_type=False,
    )
    order_status_enum = postgresql.ENUM(
        "open", "closed", "canceled", "expired", "partial", "stuck", "rejected",
        name="orderstatusenum", create_type=False,
    )
    position_status_enum = postgresql.ENUM(
        "open", "closed", "liquidated", "breakeven",
        name="positionstatusenum", create_type=False,
    )

    # Create ENUM types if they do not already exist.
    # This makes the migration idempotent for databases that already have
    # the enums from a prior create_all() run.
    for e in (exchange_enum, order_status_enum, position_status_enum):
        e.create(op.get_bind(), checkfirst=True)

    # --- positions ---
    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("exchange", exchange_enum, nullable=False),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("side", sa.String, nullable=False),
        sa.Column("entry_price", sa.Numeric(28, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 18), nullable=False),
        sa.Column("sl_price", sa.Numeric(28, 18), nullable=True),
        sa.Column("tp1_price", sa.Numeric(28, 18), nullable=True),
        sa.Column("tp2_price", sa.Numeric(28, 18), nullable=True),
        sa.Column("tp3_price", sa.Numeric(28, 18), nullable=True),
        sa.Column("entry_time", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", position_status_enum, default="open"),
        sa.Column("trailing_enabled", sa.Boolean, default=False),
        sa.Column("breakeven_enabled", sa.Boolean, default=False),
        sa.Column("trail_stop_price", sa.Numeric(28, 18), nullable=True),
        sa.Column("current_pnl", sa.Numeric(28, 18), nullable=True),
        sa.Column("current_rr", sa.Numeric(5, 4), nullable=True),
        sa.Column("risk_percent", sa.Numeric(5, 4), nullable=True),
        sa.Column("strategy", sa.String, nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
    )

    # --- trades ---
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("exchange", exchange_enum, nullable=False),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("side", sa.String, nullable=False),
        sa.Column("order_type", sa.String, nullable=False),
        sa.Column("quantity", sa.Numeric(28, 18), nullable=False),
        sa.Column("price", sa.Numeric(28, 18), nullable=False),
        sa.Column("cost", sa.Numeric(28, 18), nullable=False),
        sa.Column("fee", sa.Numeric(28, 18), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("strategy", sa.String, nullable=True),
        sa.Column("signal_reason", sa.Text, nullable=True),
        sa.Column("exit_reason", sa.String, nullable=True),
        sa.Column("pnl", sa.Numeric(28, 18), nullable=True),
        sa.Column("pnl_percent", sa.Numeric(5, 4), nullable=True),
        sa.Column("sl_pipped", sa.Numeric(28, 18), nullable=True),
        sa.Column("tp_pipped", sa.Numeric(28, 18), nullable=True),
        sa.Column("rr", sa.Numeric(5, 4), nullable=True),
        sa.Column("hold_time_seconds", sa.Integer, nullable=True),
        sa.Column("screenshot_path", sa.String, nullable=True),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id"),
            nullable=True,
            unique=True,
        ),
    )

    # --- orders ---
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("exchange", exchange_enum, nullable=False),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("order_id", sa.String, nullable=False),
        sa.Column("client_order_id", sa.String, nullable=True),
        sa.Column("side", sa.String, nullable=False),
        sa.Column("order_type", sa.String, nullable=False),
        sa.Column("quantity", sa.Numeric(28, 18), nullable=False),
        sa.Column("price", sa.Numeric(28, 18), nullable=True),
        sa.Column("status", order_status_enum, default="open"),
        sa.Column("filled_quantity", sa.Numeric(28, 18), default=0),
        sa.Column("avg_price", sa.Numeric(28, 18), nullable=True),
        sa.Column("fee_cost", sa.Numeric(28, 18), nullable=True),
        sa.Column("fee_currency", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_orders_exchange_order_id",
        "orders",
        ["exchange", "order_id"],
    )

    # --- runtime_state ---
    op.create_table(
        "runtime_state",
        sa.Column("scope", sa.String, primary_key=True),
        sa.Column("current_equity", sa.Numeric(28, 18), nullable=False),
        sa.Column("daily_start_equity", sa.Numeric(28, 18), nullable=False),
        sa.Column("daily_loss", sa.Numeric(28, 18), nullable=False, server_default="0"),
        sa.Column("open_trades", sa.Integer, nullable=False, server_default="0"),
        sa.Column("equity_day", sa.String, nullable=False),
        sa.Column("paper_position_json", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- logs ---
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("level", sa.String, nullable=False),
        sa.Column("module", sa.String, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("extra_json", sa.Text, nullable=True),
    )

    # --- analytics ---
    op.create_table(
        "analytics",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("metric_name", sa.String, nullable=False),
        sa.Column("metric_value", sa.Float, nullable=False),
        sa.Column("tags_json", sa.Text, nullable=True),
    )

    # --- strategy_performance ---
    op.create_table(
        "strategy_performance",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_name", sa.String, nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_trades", sa.Integer, default=0),
        sa.Column("winning_trades", sa.Integer, default=0),
        sa.Column("losing_trades", sa.Integer, default=0),
        sa.Column("total_pnl", sa.Numeric(28, 18), default=0),
        sa.Column("max_drawdown", sa.Numeric(28, 18), default=0),
        sa.Column("avg_rr", sa.Numeric(5, 4), default=0),
        sa.Column("sharpe_ratio", sa.Numeric(5, 4), default=0),
    )


def downgrade() -> None:
    op.drop_table("strategy_performance")
    op.drop_table("analytics")
    op.drop_table("logs")
    op.drop_table("runtime_state")
    op.drop_constraint("uq_orders_exchange_order_id", "orders", type_="unique")
    op.drop_table("orders")
    op.drop_table("trades")
    op.drop_table("positions")

    # Drop ENUM types (must be done after tables that reference them).
    for name in ("positionstatusenum", "orderstatusenum", "exchangeenum"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
