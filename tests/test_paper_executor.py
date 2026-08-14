from decimal import Decimal

import pytest

from backend.core.execution.executor import PaperExecutor


@pytest.mark.asyncio
async def test_paper_market_round_trip_tracks_pnl_and_fee():
    paper = PaperExecutor(1000, fee_rate=0.001)
    entry = await paper.execute_order("BTC/USDT", "buy", 1, price=100)
    assert entry["status"] == "closed"
    assert paper.open_positions["BTC/USDT"]["quantity"] == Decimal("1")

    exit_order = await paper.execute_order("BTC/USDT", "sell", 1, price=110)
    assert exit_order["status"] == "closed"
    assert "BTC/USDT" not in paper.open_positions
    # +10 gross - 0.1 entry fee - 0.11 exit fee
    assert paper.virtual_balance == Decimal("1009.79")
    assert len(paper.closed_trades) == 1


@pytest.mark.asyncio
async def test_paper_limit_and_trigger_orders_fill_from_price_stream():
    paper = PaperExecutor(1000)
    order = await paper.execute_order("BTC/USDT", "buy", 1, "limit", price=95)
    assert order["status"] == "open"
    filled = await paper.process_price("BTC/USDT", 100)
    assert filled == []
    filled = await paper.process_price("BTC/USDT", 95)
    assert len(filled) == 1
    assert paper.open_positions["BTC/USDT"]["entry_price"] == Decimal("95")


@pytest.mark.asyncio
async def test_paper_partial_close_preserves_remaining_position():
    paper = PaperExecutor(1000, fee_rate=0)
    await paper.execute_order("BTC/USDT", "buy", 2, price=100)
    await paper.execute_order("BTC/USDT", "sell", 0.75, price=110)
    pos = paper.open_positions["BTC/USDT"]
    assert pos["quantity"] == Decimal("1.25")
    assert paper.virtual_balance == Decimal("1007.5")
    assert len(paper.closed_trades) == 1


def test_paper_mark_to_market_and_snapshot():
    import asyncio
    paper = PaperExecutor(1000, fee_rate=0)
    asyncio.run(paper.execute_order("BTC/USDT", "buy", 2, price=100))
    assert paper.mark_to_market("BTC/USDT", 105) == Decimal("1010")
    snap = paper.snapshot()
    assert snap["virtual_balance"] == "1000"
    assert snap["open_positions"]["BTC/USDT"]["quantity"] == "2"
