import random

import pytest

from backend.core.analysis.market_analyzer import MarketAnalyzer
from backend.core.analysis.signal_generator import SignalGenerator
from backend.core.analysis.smc import SMCEngine
from backend.core.exchange.base import MarketData
from backend.core.strategy.strategies.smart_money import CandleBuilder


def make_ohlcv(n: int = 120, seed: int = 7) -> list:
    rng = random.Random(seed)
    price = 30_000.0
    candles = []
    for i in range(n):
        open_ = price
        close = open_ * (1 + rng.uniform(-0.01, 0.012))
        high = max(open_, close) * (1 + rng.uniform(0, 0.004))
        low = min(open_, close) * (1 - rng.uniform(0, 0.004))
        candles.append([1_600_000_000_000 + i * 900_000, open_, high, low, close, rng.uniform(1, 10)])
        price = close
    return candles


def test_analysis_produces_smc_structures():
    analysis = MarketAnalyzer("BTC/USDT", "15m").analyze(make_ohlcv())
    assert analysis is not None
    assert analysis["current_price"] > 0
    assert analysis["atr"] > 0
    assert set(analysis["structure"]) == {"bos", "choch", "trend"}
    assert isinstance(analysis["fvgs"], list)
    assert isinstance(analysis["order_blocks"], list)


def test_signal_levels_respect_min_rr():
    analysis = MarketAnalyzer("BTC/USDT", "15m").analyze(make_ohlcv())
    generator = SignalGenerator(min_rr=2.0)
    levels = generator.build_levels(analysis, "BUY")
    risk = levels["entry"] - levels["stop_loss"]
    reward = levels["take_profit"] - levels["entry"]
    assert reward / risk == pytest.approx(2.0)


def test_fvg_detection_on_synthetic_gap():
    import pandas as pd

    df = pd.DataFrame(
        [
            {"open": 100, "high": 101, "low": 99, "close": 100},
            {"open": 101, "high": 106, "low": 100, "close": 105},
            {"open": 105, "high": 108, "low": 103, "close": 107},
        ]
    )
    fvgs = SMCEngine.detect_fvg(df)
    assert fvgs and fvgs[0]["type"] == "BULLISH"


def test_candle_builder_closes_candles():
    builder = CandleBuilder(interval_seconds=60)
    closed = [
        builder.update(
            MarketData(
                symbol="BTC/USDT",
                timestamp=1_600_000_000_000 + i * 30_000,
                price=100 + i,
                volume=1,
                bid=99,
                ask=101,
                spread=2,
            )
        )
        for i in range(5)
    ]
    assert sum(closed) == 2
    assert len(builder.series()) == 3


def test_candle_builder_preserves_exact_decimal_ohlcv():
    from decimal import Decimal

    builder = CandleBuilder(interval_seconds=60)
    builder.update(MarketData(
        symbol="BTC/USDT", timestamp=0,
        price=Decimal("100.123456789012345678"),
        volume=Decimal("0.000000000000000123"),
    ))
    builder.update(MarketData(
        symbol="BTC/USDT", timestamp=30_000,
        price=Decimal("101.987654321098765432"),
        volume=Decimal("0.000000000000000456"),
    ))
    builder.update(MarketData(
        symbol="BTC/USDT", timestamp=60_000,
        price=Decimal("99.000000000000000001"),
        volume=Decimal("1.000000000000000001"),
    ))

    first = builder.series()[0]
    assert first[1:5] == [
        Decimal("100.123456789012345678"),
        Decimal("101.987654321098765432"),
        Decimal("100.123456789012345678"),
        Decimal("101.987654321098765432"),
    ]
    assert first[5] == Decimal("0.000000000000000579")
    assert all(isinstance(value, Decimal) for value in first[1:])


def test_smc_outputs_preserve_decimal_prices():
    from decimal import Decimal
    import pandas as pd

    df = pd.DataFrame([
        {"open": Decimal("10.000000000000000001"), "high": Decimal("10.100000000000000001"), "low": Decimal("9.900000000000000001"), "close": Decimal("10.000000000000000001")},
        {"open": Decimal("10.200000000000000001"), "high": Decimal("10.300000000000000001"), "low": Decimal("10.200000000000000001"), "close": Decimal("10.250000000000000001")},
        {"open": Decimal("10.400000000000000001"), "high": Decimal("10.500000000000000001"), "low": Decimal("10.400000000000000001"), "close": Decimal("10.450000000000000001")},
    ])
    fvgs = SMCEngine.detect_fvg(df)
    assert fvgs[0]["top"] == Decimal("10.400000000000000001")
    assert fvgs[0]["bottom"] == Decimal("10.100000000000000001")
    assert all(isinstance(fvg["top"], Decimal) for fvg in fvgs)

    swing_df = pd.DataFrame([
        {"high": Decimal("10"), "low": Decimal("9")},
        {"high": Decimal("12.123456789012345678"), "low": Decimal("8.123456789012345678")},
        {"high": Decimal("11"), "low": Decimal("8.5")},
    ])
    swings = SMCEngine.swing_points(swing_df, lookback=1)
    assert swings["highs"][0]["price"] == Decimal("12.123456789012345678")
    assert swings["lows"][0]["price"] == Decimal("8.123456789012345678")

    ob_df = pd.DataFrame([
        {"open": Decimal("10"), "high": Decimal("10.5"), "low": Decimal("9.5"), "close": Decimal("10.2")},
        {"open": Decimal("10.2"), "high": Decimal("10.4"), "low": Decimal("9.8"), "close": Decimal("10.0")},
        {"open": Decimal("10.0"), "high": Decimal("11.0"), "low": Decimal("9.9"), "close": Decimal("10.8")},
    ])
    order_blocks = SMCEngine.find_order_blocks(ob_df)
    assert order_blocks[0]["price"] == Decimal("9.8")
    assert order_blocks[0]["range"] == (Decimal("9.8"), Decimal("10.4"))


def test_analysis_exposes_decimal_current_price_and_indicator_boundary():
    from decimal import Decimal
    from backend.core.analysis.indicators import indicator_dataframe, ohlcv_to_dataframe

    ohlcv = [[
        1_600_000_000_000 + i * 900_000,
        Decimal("100.123456789012345678"),
        Decimal("101.123456789012345678"),
        Decimal("99.123456789012345678"),
        Decimal("100.987654321098765432"),
        Decimal("1.000000000000000001"),
    ] for i in range(20)]
    raw = ohlcv_to_dataframe(ohlcv)
    indicator = indicator_dataframe(raw)
    analysis = MarketAnalyzer("BTC/USDT", "15m").analyze(ohlcv)
    assert raw["close"].iloc[-1] == Decimal("100.987654321098765432")
    assert indicator["close"].iloc[-1] == pytest.approx(100.987654321098765432)
    assert isinstance(analysis["current_price"], Decimal)
    assert analysis["current_price"] == Decimal("100.987654321098765432")
    assert isinstance(analysis["atr"], Decimal)
    assert analysis["df"]["close"].dtype.kind == "f"


def test_staged_levels_are_decimal_ordered_and_honor_min_rr():
    from decimal import Decimal

    analysis = {"current_price": Decimal("100.000000000000000001"), "atr": Decimal("2.000000000000000001")}
    for side, increasing in (("BUY", True), ("SELL", False)):
        levels = SignalGenerator(min_rr=Decimal("4")).build_levels(analysis, side)
        values = [levels["tp1"], levels["tp2"], levels["tp3"]]
        assert all(isinstance(value, Decimal) for value in values)
        assert levels["take_profit"] is levels["tp3"]
        if increasing:
            assert levels["entry"] < values[0] < values[1] < values[2]
            assert (values[2] - levels["entry"]) / (levels["entry"] - levels["stop_loss"]) == Decimal("4")
        else:
            assert levels["entry"] > values[0] > values[1] > values[2]
            assert (levels["entry"] - values[2]) / (levels["stop_loss"] - levels["entry"]) == Decimal("4")


def test_smart_money_signal_returns_three_decimal_targets(monkeypatch):
    from decimal import Decimal

    from backend.core.strategy.strategies.smart_money import Strategy

    strategy = Strategy(parameters={"timeframe": "1m", "min_candles": 1})
    analysis = {
        "current_price": Decimal("100.123456789012345678"),
        "atr": Decimal("2.000000000000000001"),
        "fvgs": [], "order_blocks": [],
        "structure": {"bos": [], "choch": [], "trend": [{"type": "BULLISH"}]},
    }
    monkeypatch.setattr(strategy.analyzer, "analyze", lambda candles: analysis)
    monkeypatch.setattr(strategy.signal_gen, "generate_signal", lambda snapshot: "BUY")
    signal = strategy.on_market_data(MarketData(
        symbol="BTC/USDT", timestamp=0,
        price=Decimal("100.123456789012345678"), volume=Decimal("1"),
    ))
    assert signal is None
    signal = strategy.on_market_data(MarketData(
        symbol="BTC/USDT", timestamp=60_000,
        price=Decimal("100.123456789012345679"), volume=Decimal("1"),
    ))
    distance = analysis["atr"]
    assert signal["tp_prices"] == [
        analysis["current_price"] + distance * Decimal("2") / Decimal("3"),
        analysis["current_price"] + distance * Decimal("4") / Decimal("3"),
        analysis["current_price"] + distance * Decimal("2"),
    ]
    assert all(isinstance(value, Decimal) for value in signal["tp_prices"])


def test_trend_strategy_does_not_repeat_same_direction():
    from decimal import Decimal
    from backend.core.exchange.base import MarketData
    from backend.core.strategy.strategies.trend import Strategy

    strategy = Strategy()
    signal = None
    for i in range(60):
        signal = strategy.on_market_data(MarketData(
            symbol="BTC/USDT", timestamp=i * 1000, price=Decimal(str(100 + i)), volume=Decimal("1")
        )) or signal
    assert signal is not None
    repeated = strategy.on_market_data(MarketData(
        symbol="BTC/USDT", timestamp=61_000, price=Decimal("161"), volume=Decimal("1")
    ))
    assert repeated is None
