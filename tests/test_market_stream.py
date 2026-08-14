import asyncio
from decimal import Decimal

import pytest

from backend.core.exchange.base import MarketData
from backend.core.websocket.market_stream import MarketStream


class DummyExchange:
    def __init__(self, websocket_values=None, rest_values=None):
        self.websocket_values = list(websocket_values or [])
        self.rest_values = list(rest_values or [])
        self.closed = False
        self.watch_calls = 0
        self.fetch_calls = 0

    async def watch_ticker(self, symbol):
        self.watch_calls += 1
        value = self.websocket_values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_ticker(self, symbol):
        self.fetch_calls += 1
        if not self.rest_values:
            raise RuntimeError("no REST fixture")
        value = self.rest_values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self):
        self.closed = True


def tick(timestamp, price):
    return MarketData(
        symbol="BTC/USDT",
        timestamp=timestamp,
        price=Decimal(str(price)),
    )


@pytest.mark.asyncio
async def test_publish_filters_duplicates_and_out_of_order_ticks():
    stream = MarketStream(["BTC/USDT"])
    assert await stream._publish(tick(100, "100"))
    assert not await stream._publish(tick(100, "100"))
    assert not await stream._publish(tick(99, "99"))
    assert await stream._publish(tick(101, "101"))
    assert [stream.queue.get_nowait().timestamp, stream.queue.get_nowait().timestamp] == [100, 101]


@pytest.mark.asyncio
async def test_worker_uses_rest_after_websocket_failure(monkeypatch):
    exchange = DummyExchange(
        websocket_values=[RuntimeError("socket down")],
        rest_values=[tick(200, "200")],
    )
    stream = MarketStream(["BTC/USDT"])
    stream.running = True
    stream.exchanges["dummy"] = exchange
    monkeypatch.setattr(stream, "WS_BACKOFF_INITIAL", 0)
    monkeypatch.setattr(stream, "REST_POLL_SECONDS", 0)

    worker = asyncio.create_task(stream._ticker_worker("dummy", exchange, "BTC/USDT"))
    for _ in range(20):
        if stream.queue.qsize():
            break
        await asyncio.sleep(0)
    await stream.stop()

    assert exchange.watch_calls == 1
    assert exchange.fetch_calls >= 1
    assert stream.health_snapshot()["BTC/USDT"]["fallback_mode"] is True
    assert exchange.closed is True


@pytest.mark.asyncio
async def test_callback_is_called_once_for_returned_snapshot():
    seen = []

    async def callback(data):
        seen.append(data.timestamp)

    stream = MarketStream(["BTC/USDT"], callback=callback)
    assert await stream._publish(tick(300, "300"))
    assert seen == [300]
    assert stream.queue.qsize() == 1


@pytest.mark.asyncio
async def test_stop_cancels_worker_and_closes_exchange():
    exchange = DummyExchange(websocket_values=[asyncio.CancelledError()])
    stream = MarketStream(["BTC/USDT"])
    stream.running = True
    stream.exchanges["dummy"] = exchange
    task = asyncio.create_task(stream._ticker_worker("dummy", exchange, "BTC/USDT"))
    stream.tasks = [task]
    await asyncio.sleep(0)
    await stream.stop()
    assert task.done()
    assert exchange.closed is True
    assert stream.running is False
