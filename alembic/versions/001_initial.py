"""Initial database schema for ATS-SMT PRO."""

from alembic import op
import sqlalchemy as sa


revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create initial schema."""
    
    # Exchanges table
    op.create_table(
        "exchanges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), default=True),
        sa.Column("testnet_enabled", sa.Boolean(), default=False),
        sa.Column("api_key_encrypted", sa.Text()),
        sa.Column("api_secret_encrypted", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Symbols table
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exchange_id", sa.Integer(), sa.ForeignKey("exchanges.id")),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("base_asset", sa.String(20)),
        sa.Column("quote_asset", sa.String(20)),
        sa.Column("enabled", sa.Boolean(), default=True),
        sa.Column("min_qty", sa.Numeric(32, 8)),
        sa.Column("max_qty", sa.Numeric(32, 8)),
        sa.Column("qty_step", sa.Numeric(32, 8)),
        sa.Column("min_notional", sa.Numeric(32, 8)),
        sa.Column("price_precision", sa.Integer()),
        sa.Column("qty_precision", sa.Integer()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exchange_id", "symbol", name="uq_exchange_symbol"),
    )
    
    # Candles table
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id")),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("timestamp", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Numeric(32, 8)),
        sa.Column("high", sa.Numeric(32, 8)),
        sa.Column("low", sa.Numeric(32, 8)),
        sa.Column("close", sa.Numeric(32, 8)),
        sa.Column("volume", sa.Numeric(32, 8)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "timeframe", "timestamp", name="uq_candle_unique"),
    )
    
    # Signals table
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exchange_id", sa.Integer(), sa.ForeignKey("exchanges.id")),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id")),
        sa.Column("signal_id", sa.String(64), unique=True),
        sa.Column("action", sa.String(10)),
        sa.Column("entry", sa.Numeric(32, 8)),
        sa.Column("sl", sa.Numeric(32, 8)),
        sa.Column("tp1", sa.Numeric(32, 8)),
        sa.Column("tp2", sa.Numeric(32, 8)),
        sa.Column("tp3", sa.Numeric(32, 8)),
        sa.Column("regime", sa.String(20)),
        sa.Column("votes", sa.String(10)),
        sa.Column("htf_4h", sa.String(10)),
        sa.Column("htf_1d", sa.String(10)),
        sa.Column("adx", sa.Numeric(10, 4)),
        sa.Column("atr", sa.Numeric(32, 8)),
        sa.Column("processed", sa.Boolean(), default=False),
        sa.Column("order_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Orders table
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exchange_id", sa.Integer(), sa.ForeignKey("exchanges.id")),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id")),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id")),
        sa.Column("order_type", sa.String(20)),
        sa.Column("side", sa.String(10)),
        sa.Column("quantity", sa.Numeric(32, 8)),
        sa.Column("price", sa.Numeric(32, 8)),
        sa.Column("status", sa.String(20)),
        sa.Column("client_order_id", sa.String(64)),
        sa.Column("exchange_order_id", sa.String(64)),
        sa.Column("filled_qty", sa.Numeric(32, 8)),
        sa.Column("avg_fill_price", sa.Numeric(32, 8)),
        sa.Column("fees", sa.Numeric(32, 8)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Positions table
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exchange_id", sa.Integer(), sa.ForeignKey("exchanges.id")),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id")),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id")),
        sa.Column("side", sa.String(10)),
        sa.Column("quantity", sa.Numeric(32, 8)),
        sa.Column("remaining_qty", sa.Numeric(32, 8)),
        sa.Column("entry_price", sa.Numeric(32, 8)),
        sa.Column("sl", sa.Numeric(32, 8)),
        sa.Column("tp1", sa.Numeric(32, 8)),
        sa.Column("tp2", sa.Numeric(32, 8)),
        sa.Column("tp3", sa.Numeric(32, 8)),
        sa.Column("tp1_hit", sa.Boolean(), default=False),
        sa.Column("tp2_hit", sa.Boolean(), default=False),
        sa.Column("tp3_hit", sa.Boolean(), default=False),
        sa.Column("breakeven_active", sa.Boolean(), default=False),
        sa.Column("initial_risk", sa.Numeric(32, 8)),
        sa.Column("realized_pnl", sa.Numeric(32, 8)),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Strategy settings table
    op.create_table(
        "strategy_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), default=1),
        sa.Column("structure_period", sa.Integer(), default=20),
        sa.Column("confirmation_type", sa.String(10), default="Body"),
        sa.Column("htf1", sa.String(10), default="4H"),
        sa.Column("htf2", sa.String(10), default="1D"),
        sa.Column("adx_th", sa.Numeric(10, 4), default=20),
        sa.Column("adx_trend", sa.Numeric(10, 4), default=25),
        sa.Column("adx_dead", sa.Numeric(10, 4), default=15),
        sa.Column("filter_mode", sa.String(10), default="2of3"),
        sa.Column("vol_mult", sa.Numeric(10, 4), default=1.5),
        sa.Column("use_impulse", sa.Boolean(), default=True),
        sa.Column("impulse_mult", sa.Numeric(10, 4), default=1.0),
        sa.Column("use_range_bounce", sa.Boolean(), default=True),
        sa.Column("bb_lookback", sa.Integer(), default=10),
        sa.Column("max_bounces", sa.Integer(), default=2),
        sa.Column("min_atr_pct", sa.Numeric(10, 4), default=0.3),
        sa.Column("max_bos_dist_atr", sa.Numeric(10, 4), default=0.5),
        sa.Column("use_cooldown", sa.Boolean(), default=True),
        sa.Column("cooldown_bars", sa.Integer(), default=6),
        sa.Column("risk_pct", sa.Numeric(10, 4), default=1.0),
        sa.Column("tp1_pct", sa.Integer(), default=40),
        sa.Column("tp2_pct", sa.Integer(), default=30),
        sa.Column("tp3_pct", sa.Integer(), default=30),
        sa.Column("use_breakeven", sa.Boolean(), default=True),
        sa.Column("use_trail", sa.Boolean(), default=False),
        sa.Column("active", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Strategy settings history
    op.create_table(
        "strategy_settings_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("setting_id", sa.Integer(), sa.ForeignKey("strategy_settings.id")),
        sa.Column("parameter", sa.String(50)),
        sa.Column("old_value", sa.String(100)),
        sa.Column("new_value", sa.String(100)),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Risk events table
    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50)),
        sa.Column("symbol", sa.String(20)),
        sa.Column("details", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # System events table
    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50)),
        sa.Column("component", sa.String(50)),
        sa.Column("message", sa.Text()),
        sa.Column("severity", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Create indexes
    op.create_index("idx_candles_symbol_tf", "candles", ["symbol_id", "timeframe"])
    op.create_index("idx_candles_timestamp", "candles", ["timestamp"])
    op.create_index("idx_signals_created", "signals", ["created_at"])
    op.create_index("idx_orders_status", "orders", ["status"])
    op.create_index("idx_positions_open", "positions", ["opened_at"])


def downgrade():
    """Drop all tables."""
    op.drop_table("system_events")
    op.drop_table("risk_events")
    op.drop_table("strategy_settings_history")
    op.drop_table("strategy_settings")
    op.drop_table("positions")
    op.drop_table("orders")
    op.drop_table("signals")
    op.drop_table("candles")
    op.drop_table("symbols")
    op.drop_table("exchanges")
