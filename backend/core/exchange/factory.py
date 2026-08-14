"""Factory that builds exchange adapters from the central configuration."""

from typing import Dict, Optional, Type

from backend.config import settings
from backend.core.exchange.base import Exchange
from backend.core.exchange.binance import BinanceExchange
from backend.core.exchange.bitget import BitgetExchange
from backend.core.exchange.bybit import BybitExchange
from backend.core.exchange.gateio import GateIOExchange
from backend.core.exchange.kucoin import KuCoinExchange
from backend.core.exchange.mexc import MEXCExchange
from backend.core.exchange.okx import OKXExchange


ADAPTERS: Dict[str, Type[Exchange]] = {
    "binance": BinanceExchange,
    "bybit": BybitExchange,
    "okx": OKXExchange,
    "bitget": BitgetExchange,
    "mexc": MEXCExchange,
    "kucoin": KuCoinExchange,
    "gateio": GateIOExchange,
}


def create_exchange(
    name: Optional[str] = None,
    sandbox: Optional[bool] = None,
) -> Exchange:
    """
    Create an exchange adapter using the central ATS configuration.

    EXCHANGE_MODE is the authoritative source for sandbox/live mode.

    - EXCHANGE_MODE=testnet -> sandbox=True
    - EXCHANGE_MODE=live    -> sandbox=False

    Explicit sandbox values that contradict EXCHANGE_MODE are rejected.

    Credential validation is performed for the built-in exchange adapters.
    This keeps the factory extensible for custom/test adapters while preserving
    credential checks for all production exchanges.
    """
    exchange_name = (name or settings.EXCHANGE).lower().strip()

    if exchange_name not in ADAPTERS:
        raise ValueError(
            f"Unsupported exchange: {exchange_name}. "
            f"Available: {sorted(ADAPTERS)}"
        )

    exchange_mode = settings.EXCHANGE_MODE.lower().strip()

    if exchange_mode not in {"testnet", "live"}:
        raise ValueError(
            f"EXCHANGE_MODE must be 'testnet' or 'live', got "
            f"{settings.EXCHANGE_MODE!r}"
        )

    expected_sandbox = exchange_mode == "testnet"

    if sandbox is None:
        sandbox = expected_sandbox
    else:
        sandbox = bool(sandbox)

        if sandbox != expected_sandbox:
            raise ValueError(
                f"sandbox={sandbox} conflicts with "
                f"EXCHANGE_MODE={exchange_mode}; "
                f"expected sandbox={expected_sandbox}"
            )

    trading_mode = settings.TRADING_MODE.lower().strip()

    credentials = settings.get_exchange_credentials(exchange_name)

    # Only built-in production adapters require credentials here.
    # Custom adapters may be injected into ADAPTERS by tests or applications.
    if exchange_name in {
        "binance",
        "bybit",
        "okx",
        "bitget",
        "mexc",
        "kucoin",
        "gateio",
    }:
        if trading_mode in {"testnet", "live"}:
            if not credentials.get("apiKey") or not credentials.get("secret"):
                raise ValueError(
                    f"TRADING_MODE={trading_mode} requires API key and secret "
                    f"for EXCHANGE={exchange_name}"
                )

            if exchange_name in {"okx", "bitget", "kucoin"}:
                extra = (
                    credentials.get("passphrase")
                    or credentials.get("password")
                )

                if not extra:
                    raise ValueError(
                        f"TRADING_MODE={trading_mode} requires "
                        f"passphrase/password for EXCHANGE={exchange_name}"
                    )

    return ADAPTERS[exchange_name](
        credentials,
        sandbox=bool(sandbox),
    )