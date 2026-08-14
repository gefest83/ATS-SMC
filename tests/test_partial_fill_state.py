import pytest
from decimal import Decimal
from backend.core.position_manager import PositionManager
from backend.core.exchange.base import OrderResponse

@pytest.mark.asyncio
async def test_repeated_cumulative_fill_is_idempotent():
    manager = PositionManager(exchange_name="binance")
    pid = await manager.create_position(
        symbol="BTC/USDT:USDT", side="buy", quantity=Decimal("1"),
        entry_price=Decimal("100"), position_id="00000000-0000-0000-0000-000000000101"
    )
    first = OrderResponse(order_id="tp-1", status="open", filled_quantity=Decimal("0.4"),
                          fill_delta=Decimal("0.4"), avg_price=Decimal("110"),
                          fee_cost=Decimal("0.04"), fee_currency="USDT")
    second = OrderResponse(order_id="tp-1", status="closed", filled_quantity=Decimal("0.4"),
                           fill_delta=Decimal("0"), avg_price=Decimal("110"),
                           fee_cost=Decimal("0.04"), fee_currency="USDT")
    assert await manager.record_fill(pid, first, "exit") == Decimal("0.4")
    assert await manager.record_fill(pid, second, "exit") == Decimal("0")
    pos = manager.positions[pid]
    assert pos.exit_quantity == Decimal("0.4")
    assert pos.quantity == Decimal("1")
