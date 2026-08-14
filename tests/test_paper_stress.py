
import asyncio
from decimal import Decimal

from backend.core.execution.executor import PaperExecutor


def run(coro):
    return asyncio.run(coro)


def test_100_round_trips_have_no_balance_drift_without_fees():
    paper = PaperExecutor(10_000, fee_rate=0)
    for i in range(100):
        run(paper.execute_order("BTC/USDT", "buy", 1, price=100))
        run(paper.execute_order("BTC/USDT", "sell", 1, price=101))
    assert paper.virtual_balance == Decimal("10100")
    assert not paper.open_positions
    assert len(paper.closed_trades) == 100


def test_100_round_trips_apply_exact_fees():
    paper = PaperExecutor(10_000, fee_rate=Decimal("0.001"))
    for i in range(100):
        run(paper.execute_order("BTC/USDT", "buy", 1, price=100))
        run(paper.execute_order("BTC/USDT", "sell", 1, price=101))
    # Each round trip: +1 gross - 0.100 entry fee - 0.101 exit fee.
    assert paper.virtual_balance == Decimal("10079.900")
    assert len(paper.closed_trades) == 100


def test_partial_tp_then_recovery_snapshot_keeps_remaining_exposure():
    paper = PaperExecutor(10_000, fee_rate=0)
    run(paper.execute_order("BTC/USDT", "buy", 2, price=100))
    run(paper.execute_order("BTC/USDT", "sell", 0.75, price=110))
    snap = paper.snapshot()

    restored = PaperExecutor(1)
    restored.restore(snap)

    assert restored.snapshot() == snap
    assert restored.open_positions["BTC/USDT"]["quantity"] == Decimal("1.25")
    # 7.5 realized on the partial close plus 12.5 unrealized on 1.25 left.
    assert restored.mark_to_market("BTC/USDT", 110) == Decimal("10020")


def test_high_precision_snapshot_restore_preserves_open_order_and_counter():
    paper = PaperExecutor("1000.000000000000000001", fee_rate="0.000000123456789")
    run(
        paper.execute_order(
            "BTC/USDT", "buy", "0.123456789012345678", "limit",
            price="100.000000000000000001",
        )
    )
    first_snapshot = paper.snapshot()

    restored = PaperExecutor(1)
    restored.restore(first_snapshot)
    assert restored.snapshot() == first_snapshot

    run(
        restored.execute_order(
            "BTC/USDT", "buy", "0.000000000000000001", price="101"
        )
    )
    assert restored.snapshot()["open_orders"]
    assert restored.snapshot()["counter"] == 2
    assert restored.snapshot()["open_orders"]["paper_1"]["price"] == "100.000000000000000001"
    assert restored.snapshot()["virtual_balance"] == "1000.0000000000000000009596"


def test_sl_tp_price_stream_closes_position_without_duplicate_trade():
    paper = PaperExecutor(10_000, fee_rate=0)
    run(paper.execute_order("BTC/USDT", "buy", 1, price=100))
    run(paper.execute_order("BTC/USDT", "sell", 1, "take_profit", stop_price=110))
    filled = run(paper.process_price("BTC/USDT", 110))
    assert len(filled) == 1
    assert not paper.open_positions
    assert len(paper.closed_trades) == 1
    assert paper.virtual_balance == Decimal("10010")
