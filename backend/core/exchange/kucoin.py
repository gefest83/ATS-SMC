"""
KuCoin exchange adapter – implements the Exchange interface using CCXT.
"""
import asyncio
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import ccxt
import ccxt.pro as ccxtpro

from backend.config import settings
from backend.core.exchange.base import (
    Exchange,
    MarketData,
    OrderBookEntry,
    OrderRequest,
    OrderResponse,
    PositionData,
)

logger = logging.getLogger(__name__)


class KuCoinExchange(Exchange):
    def __init__(self, credentials: Dict[str, Any], sandbox: bool = False):
        self.credentials = credentials
        self.sandbox = sandbox
        self.exchange = ccxt.kucoin(
            {
                "apiKey": credentials.get("apiKey"),
                "secret": credentials.get("secret"),
                "password": credentials.get("password"),
                "sandbox": sandbox,
                "options": self.market_options(settings.EXCHANGE_MARKET_TYPE, "kucoin"),
                "enableRateLimit": True,
            }
        )
        # CCXT requires sandbox mode to be enabled before any API call.
        if self.sandbox:
            self.exchange.set_sandbox_mode(True)
        self.pro = None
        self._init_pro()

    def _init_pro(self):
        try:
            self.pro = ccxtpro.kucoin(
                {
                    "apiKey": self.credentials.get("apiKey"),
                    "secret": self.credentials.get("secret"),
                    "password": self.credentials.get("password"),
                    "sandbox": self.sandbox,
                    "options": self.market_options(settings.EXCHANGE_MARKET_TYPE, "kucoin"),
                }
            )
        except Exception as e:
            logger.warning("Failed to initialise ccxt.pro for KuCoin: %s", e)

    # REST implementations (similar to Binance)
    def fetch_balance(self) -> Dict[str, Decimal]:
        balance = self.exchange.fetch_balance()
        return self.normalize_balance(balance)

    def fetch_ticker(self, symbol: str) -> MarketData:
        ticker = self.exchange.fetch_ticker(self.normalize_symbol(symbol))
        return self.ticker_market_data(ticker, symbol)

    def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 200) -> list:
        return self.exchange.fetch_ohlcv(self.normalize_symbol(symbol), timeframe, limit=limit)

    def fetch_order_book(self, symbol: str, limit: int = 20) -> List[OrderBookEntry]:
        order_book = self.exchange.fetch_order_book(self.normalize_symbol(symbol), limit)
        bids = [OrderBookEntry(price=Decimal(str(p)), amount=Decimal(str(q))) for p, q in order_book["bids"][:limit]]
        asks = [OrderBookEntry(price=Decimal(str(p)), amount=Decimal(str(q))) for p, q in order_book["asks"][:limit]]
        return bids + asks

    def create_order(self, order: OrderRequest) -> OrderResponse:
        symbol, amount, normalized_price, params = self.prepare_order(order)
        try:
            result = self.exchange.create_order(
                symbol=symbol,
                side=order.side.lower(),
                type=self.normalize_trigger_order(order)[0],
                amount=amount,
                price=normalized_price,
                params=params,
            )
            return self.normalize_order_response(result, symbol=order.symbol, request=order)
        except Exception as e:
            logger.error("Order creation failed on KuCoin: %s", e)
            raise

    def fetch_order(self, symbol: str, order_id: str) -> OrderResponse:
        result = self.exchange.fetch_order(order_id, self.normalize_symbol(symbol))
        return self.normalize_order_response(result, symbol=symbol)

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            self.exchange.cancel_order(order_id, self.normalize_symbol(symbol))
            return True
        except ccxt.OrderNotFound:
            logger.warning("Order %s not found on KuCoin", order_id)
            return False
        except Exception as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return False

    def fetch_open_orders(self, symbol: str = None) -> List[OrderResponse]:
        try:
            orders = self.exchange.fetch_open_orders(self.normalize_symbol(symbol) if symbol else None)
            return [self.normalize_order_response(o, symbol=symbol) for o in orders]
        except Exception as e:
            logger.error("Failed to fetch open orders on KuCoin: %s", e)
            return []

    def fetch_positions(self, symbol: str = None) -> List[PositionData]:
        try:
            positions = self.exchange.fetch_positions(self.normalize_symbol(symbol) if symbol else None)
            result = []
            for pos in positions:
                normalized = self.normalize_position(pos)
                if normalized is not None:
                    result.append(normalized)
            return result
        except Exception as e:
            logger.error("Failed to fetch positions on KuCoin: %s", e)
            return []

    def get_wallet_balance(self, asset: str) -> Decimal:
        balance = self.exchange.fetch_balance()
        free = self.decimal_value(balance["free"].get(asset))
        used = self.decimal_value(balance["used"].get(asset))
        return free + used

    def get_exchange_name(self) -> str:
        return "kucoin"

    # WebSocket methods
    async def watch_ticker(self, symbol: str, callback=None) -> MarketData:
        if not self.pro:
            self._init_pro()
        if self.pro is None:
            raise RuntimeError("KuCoin WebSocket client is unavailable")
        normalized = self.normalize_symbol(symbol)
        ticker = await self.pro.watch_ticker(normalized)
        market_data = self.ticker_market_data(ticker, symbol)
        if callback is not None:
            await self._dispatch_callback(callback, market_data)
        return market_data

    async def watch_ohlcv(self, symbol: str, timeframe: str, callback=None):
        if not self.pro:
            self._init_pro()
        normalized = self.normalize_symbol(symbol)
        while True:
            try:
                ohlcv = await self.pro.watch_ohlcv(normalized, timeframe)
                await self._dispatch_callback(callback, ohlcv)
            except Exception as e:
                logger.error("WebSocket OHLCV error on KuCoin: %s", e)
                await asyncio.sleep(1)

    async def watch_order_book(self, symbol: str, callback=None):
        if not self.pro:
            self._init_pro()
        while True:
            try:
                order_book = await self.pro.watch_order_book(symbol)
                await self._dispatch_callback(callback, order_book)
            except Exception as e:
                logger.error("WebSocket order book error on KuCoin: %s", e)
                await asyncio.sleep(1)

    async def watch_positions(self, callback=None):
        if not self.pro:
            self._init_pro()
        while True:
            try:
                positions = await asyncio.to_thread(self.fetch_positions)
                await self._dispatch_callback(callback, positions)
                await asyncio.sleep(5)
            except Exception as e:
                logger.error("Position polling error on KuCoin: %s", e)
                await asyncio.sleep(5)

    async def watch_orders(self, callback=None):
        if not self.pro:
            self._init_pro()
        while True:
            try:
                orders = await asyncio.to_thread(self.fetch_open_orders)
                for order in orders:
                    await self._dispatch_callback(callback, order)
                await asyncio.sleep(2)
            except Exception as e:
                logger.error("Order polling error on KuCoin: %s", e)
                await asyncio.sleep(2)