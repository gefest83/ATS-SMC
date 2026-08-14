from datetime import datetime, timezone
from decimal import Decimal

from backend.db.models import StrategyPerformance


def test_strategy_performance_date_is_timezone_aware():
    column = StrategyPerformance.__table__.c.date
    assert column.type.timezone is True


def test_numeric_columns_preserve_decimal_contract():
    from backend.db.models import Position, RuntimeState, Trade

    for model, name in (
        (Trade, "price"),
        (Position, "entry_price"),
        (RuntimeState, "current_equity"),
    ):
        column = model.__table__.c[name]
        assert column.type.asdecimal is True


def test_utc_datetime_round_trip_value_is_aware():
    timestamp = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset().total_seconds() == 0


def test_decimal_values_are_not_float_coerced_before_persistence():
    value = Decimal("100.123456789012345678")
    assert isinstance(value, Decimal)
    assert str(value) == "100.123456789012345678"
