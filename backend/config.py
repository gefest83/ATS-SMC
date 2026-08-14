"""
Central application configuration.

Settings are loaded from environment variables and .env.
Trading safety rules are enforced here.
"""

from functools import lru_cache
from typing import List

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "ATS"
    DEBUG: bool = Field(default=False, alias="DEBUG")
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://ats:ats_secret@postgres:5432/ats",
        alias="DATABASE_URL",
    )

    # Exchange credentials
    BINANCE_API_KEY: str = Field(default="", alias="BINANCE_API_KEY")
    BINANCE_API_SECRET: str = Field(default="", alias="BINANCE_API_SECRET")

    BYBIT_API_KEY: str = Field(default="", alias="BYBIT_API_KEY")
    BYBIT_API_SECRET: str = Field(default="", alias="BYBIT_API_SECRET")

    OKX_API_KEY: str = Field(default="", alias="OKX_API_KEY")
    OKX_API_SECRET: str = Field(default="", alias="OKX_API_SECRET")
    OKX_PASSPHRASE: str = Field(default="", alias="OKX_PASSPHRASE")

    BITGET_API_KEY: str = Field(default="", alias="BITGET_API_KEY")
    BITGET_API_SECRET: str = Field(default="", alias="BITGET_API_SECRET")
    BITGET_API_PASSPHRASE: str = Field(default="", alias="BITGET_API_PASSPHRASE")

    MEXC_API_KEY: str = Field(default="", alias="MEXC_API_KEY")
    MEXC_API_SECRET: str = Field(default="", alias="MEXC_API_SECRET")

    KUCOIN_API_KEY: str = Field(default="", alias="KUCOIN_API_KEY")
    KUCOIN_API_SECRET: str = Field(default="", alias="KUCOIN_API_SECRET")
    KUCOIN_API_PASSPHRASE: str = Field(default="", alias="KUCOIN_API_PASSPHRASE")

    GATEIO_API_KEY: str = Field(default="", alias="GATEIO_API_KEY")
    GATEIO_API_SECRET: str = Field(default="", alias="GATEIO_API_SECRET")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # Strategy
    STRATEGIES_DIR: str = Field(
        default="backend/core/strategy/strategies",
        alias="STRATEGIES_DIR",
    )
    MAX_STRATEGIES: int = 20

    # Trading
    TRADING_MODE: str = Field(
        default="paper",
        alias="TRADING_MODE",
    )
    EXCHANGE: str = Field(
        default="binance",
        alias="EXCHANGE",
    )
    EXCHANGE_MODE: str = Field(
        default="testnet",
        alias="EXCHANGE_MODE",
    )
    EXCHANGE_MARKET_TYPE: str = Field(
        default="spot",
        alias="EXCHANGE_MARKET_TYPE",
    )
    FUTURES_SETTLE_ASSET: str = Field(
        default="USDT",
        alias="FUTURES_SETTLE_ASSET",
    )

    SYMBOL: str = "BTC/USDT"
    SYMBOLS: str = Field(default="", alias="SYMBOLS")
    TIMEFRAME: str = "15m"
    INITIAL_EQUITY: float = 10_000.0

    @property
    def symbols_list(self) -> List[str]:
        """Return list of symbols from SYMBOLS or fallback to [SYMBOL]."""
        if self.SYMBOLS and self.SYMBOLS.strip():
            return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]
        return [self.SYMBOL]

    AUTO_START_ENGINE: bool = Field(
        default=False,
        alias="AUTO_START_ENGINE",
    )

    LIVE_TRADING_ENABLED: bool = Field(
        default=False,
        alias="LIVE_TRADING_ENABLED",
    )

    TESTNET_TRADING_ENABLED: bool = Field(
        default=False,
        alias="TESTNET_TRADING_ENABLED",
    )

    CORS_ORIGINS: str = Field(
        default="http://localhost:5173",
        alias="CORS_ORIGINS",
    )

    API_AUTH_ENABLED: bool = Field(
        default=False,
        alias="API_AUTH_ENABLED",
    )

    API_ACCESS_TOKEN: str = Field(
        default="",
        alias="API_ACCESS_TOKEN",
    )

    # Risk / Portfolio
    MAX_DAILY_DRAWDOWN_PCT: float = 0.05
    MAX_OPEN_TRADES: int = 3
    RISK_PER_TRADE_PCT: float = 0.01
    MIN_RR_RATIO: float = 2.0

    # Misc
    ORDER_RETRY_ATTEMPTS: int = 3
    ORDER_RETRY_DELAY_SECONDS: int = 2
    STUCK_ORDER_TIMEOUT_SECONDS: int = 300
    API_RATE_LIMIT_PER_MINUTE: int = 120
    LOG_LEVEL: str = Field(default="INFO", alias="LOG_LEVEL")

    @model_validator(mode="after")
    def validate_trading_safety(self):
        mode = self.TRADING_MODE.lower().strip()
        exchange_mode = self.EXCHANGE_MODE.lower().strip()
        market_type = self.EXCHANGE_MARKET_TYPE.lower().strip()

        if mode not in {"paper", "testnet", "live"}:
            raise ValueError(
                "TRADING_MODE must be 'paper', 'testnet' or 'live'"
            )

        if exchange_mode not in {"testnet", "live"}:
            raise ValueError(
                "EXCHANGE_MODE must be 'testnet' or 'live'"
            )

        if market_type not in {"spot", "futures"}:
            raise ValueError(
                "EXCHANGE_MARKET_TYPE must be 'spot' or 'futures'"
            )

        if not 0 < self.RISK_PER_TRADE_PCT <= 0.05:
            raise ValueError(
                "RISK_PER_TRADE_PCT must be > 0 and <= 0.05"
            )

        if not 0 < self.MAX_DAILY_DRAWDOWN_PCT <= 1:
            raise ValueError(
                "MAX_DAILY_DRAWDOWN_PCT must be > 0 and <= 1"
            )

        if self.MAX_OPEN_TRADES < 1:
            raise ValueError(
                "MAX_OPEN_TRADES must be >= 1"
            )

        if self.INITIAL_EQUITY <= 0:
            raise ValueError(
                "INITIAL_EQUITY must be > 0"
            )

        if self.MIN_RR_RATIO <= 0:
            raise ValueError(
                "MIN_RR_RATIO must be > 0"
            )

        # PAPER mode is completely independent from exchange testnet/live
        # trading flags. This is important both for normal operation and
        # for isolated configuration tests.
        if mode == "paper":
            return self

        # TESTNET mode
        if mode == "testnet":
            if not self.TESTNET_TRADING_ENABLED:
                raise ValueError(
                    "TRADING_MODE=testnet requires "
                    "TESTNET_TRADING_ENABLED=true"
                )

            if exchange_mode != "testnet":
                raise ValueError(
                    "TRADING_MODE=testnet requires "
                    "EXCHANGE_MODE=testnet"
                )

            credentials = self.get_exchange_credentials(self.EXCHANGE)

            if not credentials.get("apiKey") or not credentials.get("secret"):
                raise ValueError(
                    f"TRADING_MODE=testnet requires API key and secret "
                    f"for EXCHANGE={self.EXCHANGE}"
                )

            if self.EXCHANGE.lower() in {"okx", "bitget", "kucoin"}:
                password = (
                    credentials.get("passphrase")
                    or credentials.get("password")
                )

                if not password:
                    raise ValueError(
                        f"TRADING_MODE=testnet requires the additional "
                        f"password/passphrase for EXCHANGE={self.EXCHANGE}"
                    )

            return self

        # LIVE mode
        if not self.LIVE_TRADING_ENABLED:
            raise ValueError(
                "TRADING_MODE=live requires LIVE_TRADING_ENABLED=true"
            )

        if exchange_mode != "live":
            raise ValueError(
                "TRADING_MODE=live requires EXCHANGE_MODE=live; "
                "use TRADING_MODE=testnet for exchange testnet"
            )

        if not self.API_AUTH_ENABLED or not self.API_ACCESS_TOKEN.strip():
            raise ValueError(
                "Live trading requires API_AUTH_ENABLED=true "
                "and a non-empty API_ACCESS_TOKEN"
            )

        credentials = self.get_exchange_credentials(self.EXCHANGE)

        if not credentials.get("apiKey") or not credentials.get("secret"):
            raise ValueError(
                f"TRADING_MODE=live requires API key and secret "
                f"for EXCHANGE={self.EXCHANGE}"
            )

        if self.EXCHANGE.lower() in {"okx", "bitget", "kucoin"}:
            password = (
                credentials.get("passphrase")
                or credentials.get("password")
            )

            if not password:
                raise ValueError(
                    f"TRADING_MODE=live requires the additional "
                    f"password/passphrase for EXCHANGE={self.EXCHANGE}"
                )

        return self

    def get_exchange_credentials(self, exchange_name: str) -> dict:
        mapping = {
            "binance": {
                "apiKey": self.BINANCE_API_KEY,
                "secret": self.BINANCE_API_SECRET,
            },
            "bybit": {
                "apiKey": self.BYBIT_API_KEY,
                "secret": self.BYBIT_API_SECRET,
            },
            "okx": {
                "apiKey": self.OKX_API_KEY,
                "secret": self.OKX_API_SECRET,
                "passphrase": self.OKX_PASSPHRASE,
            },
            "bitget": {
                "apiKey": self.BITGET_API_KEY,
                "secret": self.BITGET_API_SECRET,
                "passphrase": self.BITGET_API_PASSPHRASE,
            },
            "mexc": {
                "apiKey": self.MEXC_API_KEY,
                "secret": self.MEXC_API_SECRET,
            },
            "kucoin": {
                "apiKey": self.KUCOIN_API_KEY,
                "secret": self.KUCOIN_API_SECRET,
                "password": self.KUCOIN_API_PASSPHRASE,
            },
            "gateio": {
                "apiKey": self.GATEIO_API_KEY,
                "secret": self.GATEIO_API_SECRET,
            },
        }

        return mapping.get(exchange_name.lower(), {})


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()