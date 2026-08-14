"""Exchange WebSocket/REST market-data multiplexer."""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Awaitable, Callable, Dict, List, Optional

from backend.config import settings
from backend.core.exchange.base import Exchange, MarketData
from backend.core.exchange.factory import create_exchange

logger = logging.getLogger(__name__)

MarketDataCallback = Callable[[MarketData], Awaitable[None] | None]


class MarketStream:
    """Own market-data polling, reconnects, ordering, and resource cleanup."""

    WS_BACKOFF_INITIAL = 1.0
    WS_BACKOFF_MAX = 8.0
    REST_POLL_SECONDS = 1.0
    GAP_WARNING_MS = 60_000

    def __init__(
        self,
        symbols: List[str],
        callback: Optional[MarketDataCallback] = None,
        queue_size: int = 1000,
    ):
        self.symbols = list(dict.fromkeys(symbols))
        self.queue: asyncio.Queue[MarketData] = asyncio.Queue(maxsize=queue_size)
        self.exchanges: Dict[str, Exchange] = {}
        self.tasks: List[asyncio.Task] = []
        self.running = False
        self.callback = callback
        self._lifecycle_lock = asyncio.Lock()
        self._last_timestamps: Dict[str, int] = {}
        self._last_snapshots: Dict[str, tuple] = {}
        self.health: Dict[str, Dict[str, object]] = {
            symbol: self._new_health() for symbol in self.symbols
        }

    @staticmethod
    def _new_health() -> Dict[str, object]:
        return {
            "connected": False,
            "last_tick": None,
            "last_error": None,
            "fallback_mode": False,
            "reconnect_count": 0,
            "dropped_ticks": 0,
        }

    async def start(self):
        async with self._lifecycle_lock:
            if self.running:
                return
            configured = settings.EXCHANGE.lower().strip()
            exchange = create_exchange(configured)
            self.exchanges = {configured: exchange}
            self.running = True
            self.tasks = [
                asyncio.create_task(
                    self._ticker_worker(configured, exchange, symbol),
                    name=f"market-stream:{configured}:{symbol}",
                )
                for symbol in self.symbols
            ]

    @staticmethod
    def _has_ws(exchange: Exchange) -> bool:
        method = getattr(exchange, "watch_ticker", None)
        if not callable(method):
            return False
        implementation = getattr(type(exchange), "watch_ticker", None)
        return implementation is not Exchange.watch_ticker

    async def _ticker_worker(self, exchange_name: str, exchange: Exchange, symbol: str):
        ws_supported = self._has_ws(exchange)
        backoff = self.WS_BACKOFF_INITIAL
        self._set_health(symbol, connected=False, fallback_mode=not ws_supported)

        while self.running:
            if ws_supported:
                try:
                    # Do not pass the stream callback here: passing a callback
                    # and consuming the returned snapshot would enqueue twice.
                    data = await exchange.watch_ticker(symbol)
                    if isinstance(data, MarketData):
                        await self._publish(self._with_exchange(data, exchange_name))
                        self._set_health(
                            symbol,
                            connected=True,
                            fallback_mode=False,
                            last_error=None,
                        )
                        backoff = self.WS_BACKOFF_INITIAL
                        continue
                    logger.warning(
                        "Unexpected ticker payload from %s/%s: %r",
                        exchange_name,
                        symbol,
                        type(data),
                    )
                except asyncio.CancelledError:
                    raise
                except NotImplementedError as exc:
                    ws_supported = False
                    self._set_health(
                        symbol,
                        connected=False,
                        fallback_mode=True,
                        last_error=str(exc) or "WebSocket is not implemented",
                    )
                except Exception as exc:
                    self._set_health(
                        symbol,
                        connected=False,
                        fallback_mode=True,
                        last_error=str(exc),
                        reconnect_count=int(self.health[symbol]["reconnect_count"]) + 1,
                    )
                    logger.warning(
                        "WebSocket unavailable for %s/%s: %s; using REST fallback",
                        exchange_name,
                        symbol,
                        exc,
                    )

            if not self.running:
                break

            try:
                result = await asyncio.to_thread(exchange.fetch_ticker, symbol)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, MarketData):
                    await self._publish(self._with_exchange(result, exchange_name))
                else:
                    logger.warning(
                        "Unexpected REST ticker payload from %s/%s: %r",
                        exchange_name,
                        symbol,
                        type(result),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set_health(
                    symbol,
                    connected=False,
                    fallback_mode=True,
                    last_error=str(exc),
                )
                logger.error("REST market stream error %s/%s: %s", exchange_name, symbol, exc)

            delay = backoff if ws_supported else self.REST_POLL_SECONDS
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            if ws_supported:
                backoff = min(self.WS_BACKOFF_MAX, backoff * 2)

    def _set_health(self, symbol: str, **values) -> None:
        state = self.health.setdefault(symbol, self._new_health())
        state.update(values)

    def health_snapshot(self) -> Dict[str, Dict[str, object]]:
        return {symbol: dict(state) for symbol, state in self.health.items()}

    def _make_callback(self, exchange_name: str) -> MarketDataCallback:
        """Compatibility callback for older adapter integrations."""
        async def callback(data: MarketData):
            if isinstance(data, MarketData):
                await self._publish(self._with_exchange(data, exchange_name))
        return callback

    def _with_exchange(self, data: MarketData, exchange_name: str) -> MarketData:
        if not data.exchange:
            return data.model_copy(update={"exchange": exchange_name})
        return data

    @staticmethod
    def _snapshot_key(data: MarketData) -> tuple:
        return (data.timestamp, data.price, data.volume, data.bid, data.ask)

    async def _publish(self, market_data: MarketData) -> bool:
        """Filter stale snapshots, enqueue once, then invoke compatibility callback."""
        symbol = market_data.symbol
        timestamp = int(market_data.timestamp or 0)
        previous_timestamp = self._last_timestamps.get(symbol)
        snapshot_key = self._snapshot_key(market_data)
        if snapshot_key == self._last_snapshots.get(symbol):
            return False
        if timestamp and previous_timestamp is not None and timestamp <= previous_timestamp:
            logger.debug(
                "Dropping stale market tick %s timestamp=%s <= %s",
                symbol,
                timestamp,
                previous_timestamp,
            )
            return False
        if timestamp and previous_timestamp is not None and timestamp - previous_timestamp > self.GAP_WARNING_MS:
            logger.warning(
                "Market-data gap for %s: %sms since previous tick; no candle fabricated",
                symbol,
                timestamp - previous_timestamp,
            )

        self._last_snapshots[symbol] = snapshot_key
        if timestamp:
            self._last_timestamps[symbol] = timestamp
        self._set_health(symbol, last_tick=timestamp or None)
        await self._enqueue(market_data)

        if self.callback is not None:
            try:
                result = self.callback(market_data)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Market stream callback failed for %s", symbol)
        return True

    async def _enqueue(self, market_data: MarketData):
        try:
            self.queue.put_nowait(market_data)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                return
            try:
                self.queue.put_nowait(market_data)
            except asyncio.QueueFull:
                self._set_health(
                    market_data.symbol,
                    dropped_ticks=int(self.health[market_data.symbol]["dropped_ticks"]) + 1,
                )
                logger.warning("MarketStream queue remained full; dropping newest tick")

    async def stop(self):
        async with self._lifecycle_lock:
            self.running = False
            tasks = list(self.tasks)
            self.tasks.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            exchanges = list(self.exchanges.values())
            self.exchanges.clear()
            for exchange in exchanges:
                close = getattr(exchange, "close", None)
                if not callable(close):
                    continue
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("Failed to close market stream exchange")

    async def consume(self, consumer_coro):
        while self.running:
            try:
                data = await self.queue.get()
                try:
                    await consumer_coro(data)
                finally:
                    self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Market stream consumer error")
