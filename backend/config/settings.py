"""
Configuration management for ATS-SMT Pro Trading Bot.

All configuration is loaded from environment variables with sensible defaults.
Critical trading parameters are validated at startup.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Optional
from enum import Enum


class TradingMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class FilterMode(str, Enum):
    TWO_OF_THREE = "2of3"
    ALL = "ALL"


class Config(BaseSettings):
    """
    Application configuration.
    
    Security Note: API keys are never logged or exposed in responses.
    Live trading requires explicit enablement.
    """
    
    # =========================================================================
    # TRADING MODE
    # =========================================================================
    
    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
        description="Trading mode: paper, testnet, or live"
    )
    
    # =========================================================================
    # EXCHANGES & SYMBOLS
    # =========================================================================
    
    exchanges: List[str] = Field(
        default=["binance"],
        description="List of exchanges to connect"
    )
    
    symbols: List[str] = Field(
        default=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        description="Trading symbols in canonical format"
    )
    
    # =========================================================================
    # TIMEFRAMES
    # =========================================================================
    
    timeframe: str = Field(default="30m", description="Working timeframe")
    htf1: str = Field(default="4h", description="Higher timeframe 1")
    htf2: str = Field(default="1d", description="Higher timeframe 2")
    
    # =========================================================================
    # RISK MANAGEMENT
    # =========================================================================
    
    risk_pct: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Risk percentage per trade"
    )
    
    max_open_trades: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum concurrent open positions"
    )
    
    max_daily_drawdown: float = Field(
        default=5.0,
        ge=1.0,
        le=50.0,
        description="Maximum daily drawdown percentage"
    )
    
    max_position_exposure: float = Field(
        default=20.0,
        ge=5.0,
        le=100.0,
        description="Maximum exposure per position percentage"
    )
    
    max_symbol_exposure: float = Field(
        default=10.0,
        ge=5.0,
        le=50.0,
        description="Maximum exposure per symbol percentage"
    )
    
    max_total_exposure: float = Field(
        default=50.0,
        ge=10.0,
        le=100.0,
        description="Maximum total exposure percentage"
    )
    
    # =========================================================================
    # STRATEGY PARAMETERS - DO NOT MODIFY WITHOUT UNDERSTANDING
    # =========================================================================
    
    structure_period: int = Field(
        default=20,
        description="Period for pivot high/low detection (N bars left/right)"
    )
    
    adx_period: int = Field(default=14, description="ADX calculation period")
    adx_dead: float = Field(default=15.0, description="ADX threshold for DEAD regime")
    adx_range: float = Field(default=25.0, description="ADX threshold for RANGE regime")
    adx_trend: float = Field(default=25.0, description="ADX threshold for TREND regime")
    adx_vote: float = Field(default=20.0, description="ADX threshold for voting")
    
    volume_sma: int = Field(default=20, description="Volume SMA period")
    volume_mult: float = Field(default=1.5, description="Volume multiplier for vote")
    
    atr_period: int = Field(default=14, description="ATR calculation period")
    min_atr_pct: float = Field(default=0.3, description="Minimum ATR percentage")
    max_bos_dist_atr: float = Field(default=0.5, description="Max BOS distance in ATR")
    
    cooldown_bars: int = Field(default=6, description="Cooldown bars after stop loss")
    impulse_mult: float = Field(default=1.0, description="Impulse filter multiplier")
    
    bb_period: int = Field(default=20, description="Bollinger Bands period")
    bb_stddev: float = Field(default=2.0, description="Bollinger Bands standard deviation")
    bb_lookback: int = Field(default=10, description="BB bounce lookback")
    max_bounces: int = Field(default=2, description="Maximum bounces for range entry")
    
    # =========================================================================
    # TAKE PROFIT / STOP LOSS
    # =========================================================================
    
    tp1_pct: float = Field(default=40.0, description="TP1 close percentage")
    tp2_pct: float = Field(default=30.0, description="TP2 close percentage")
    tp3_pct: float = Field(default=30.0, description="TP3 close percentage")
    
    use_breakeven: bool = Field(default=True, description="Enable breakeven")
    use_trailing: bool = Field(default=False, description="Enable trailing stop")
    trailing_offset: float = Field(default=0.25, description="Trailing stop offset in ATR")
    
    # =========================================================================
    # FILTER MODE
    # =========================================================================
    
    filter_mode: FilterMode = Field(
        default=FilterMode.TWO_OF_THREE,
        description="Filter mode: 2of3 or ALL"
    )
    
    # =========================================================================
    # ENGINE CONTROL
    # =========================================================================
    
    auto_start_engine: bool = Field(
        default=False,
        description="Auto-start engine on boot"
    )
    
    live_trading_enabled: bool = Field(
        default=False,
        description="MUST be true for live trading"
    )
    
    testnet_trading_enabled: bool = Field(
        default=True,
        description="Enable testnet trading"
    )
    
    # =========================================================================
    # DATABASE
    # =========================================================================
    
    database_url: str = Field(
        default="postgresql://ats_smt_user:ats_smt_pass@localhost:5432/ats_smt_db",
        description="PostgreSQL connection URL"
    )
    
    # =========================================================================
    # TELEGRAM
    # =========================================================================
    
    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_chat_id: Optional[str] = Field(default=None)
    telegram_enabled: bool = Field(default=False)
    
    # =========================================================================
    # EXCHANGE API KEYS
    # =========================================================================
    
    binance_api_key: Optional[str] = Field(default=None)
    binance_api_secret: Optional[str] = Field(default=None)
    binance_testnet: bool = Field(default=True)
    
    okx_api_key: Optional[str] = Field(default=None)
    okx_api_secret: Optional[str] = Field(default=None)
    okx_passphrase: Optional[str] = Field(default=None)
    okx_testnet: bool = Field(default=True)
    
    bybit_api_key: Optional[str] = Field(default=None)
    bybit_api_secret: Optional[str] = Field(default=None)
    bybit_testnet: bool = Field(default=True)
    
    mexc_api_key: Optional[str] = Field(default=None)
    mexc_api_secret: Optional[str] = Field(default=None)
    mexc_testnet: bool = Field(default=True)
    
    htx_api_key: Optional[str] = Field(default=None)
    htx_api_secret: Optional[str] = Field(default=None)
    htx_testnet: bool = Field(default=True)
    
    bingx_api_key: Optional[str] = Field(default=None)
    bingx_api_secret: Optional[str] = Field(default=None)
    bingx_testnet: bool = Field(default=True)
    
    bitget_api_key: Optional[str] = Field(default=None)
    bitget_api_secret: Optional[str] = Field(default=None)
    bitget_passphrase: Optional[str] = Field(default=None)
    bitget_testnet: bool = Field(default=True)
    
    gateio_api_key: Optional[str] = Field(default=None)
    gateio_api_secret: Optional[str] = Field(default=None)
    gateio_testnet: bool = Field(default=True)
    
    kucoin_api_key: Optional[str] = Field(default=None)
    kucoin_api_secret: Optional[str] = Field(default=None)
    kucoin_passphrase: Optional[str] = Field(default=None)
    kucoin_testnet: bool = Field(default=True)
    
    kraken_api_key: Optional[str] = Field(default=None)
    kraken_api_secret: Optional[str] = Field(default=None)
    kraken_testnet: bool = Field(default=False)
    
    # =========================================================================
    # SERVER
    # =========================================================================
    
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=False)
    
    # =========================================================================
    # LOGGING
    # =========================================================================
    
    log_level: str = Field(default="INFO")
    log_file: str = Field(default="/workspace/logs/ats_smt.log")
    
    # =========================================================================
    # VALIDATORS
    # =========================================================================
    
    @field_validator('exchanges', mode='before')
    @classmethod
    def parse_exchanges(cls, v):
        if isinstance(v, str):
            return [e.strip().lower() for e in v.split(',') if e.strip()]
        return v
    
    @field_validator('symbols', mode='before')
    @classmethod
    def parse_symbols(cls, v):
        if isinstance(v, str):
            return [s.strip().upper() for s in v.split(',') if s.strip()]
        return v
    
    @field_validator('trading_mode', mode='before')
    @classmethod
    def validate_trading_mode(cls, v):
        if isinstance(v, str):
            return TradingMode(v.lower())
        return v
    
    @field_validator('filter_mode', mode='before')
    @classmethod
    def validate_filter_mode(cls, v):
        if isinstance(v, str):
            return FilterMode(v)
        return v
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


# Global config instance
config = Config()


def get_config() -> Config:
    """Get global configuration instance."""
    return config


def is_live_trading_allowed() -> bool:
    """
    Check if live trading is explicitly enabled.
    
    This is a critical safety check that must pass before any live order.
    """
    return (
        config.trading_mode == TradingMode.LIVE and
        config.live_trading_enabled
    )


def is_testnet_enabled() -> bool:
    """Check if testnet trading is enabled."""
    return config.testnet_trading_enabled
