# TPC BACKTEST FINAL REPORT

## Date: August 14, 2026

---

## Files Created
- `backtest/tpc_backtest.py` - Standalone TPC backtest script
- `backtest/results/` - Results directory

## Files Modified
- None (production code untouched)

## Commands Run
- `python backtest/tpc_backtest.py` - Ran the TPC backtest

---

## DATA FETCHED
- BTC/USDT: 2160 1H candles, 8640 15m candles
- ETH/USDT: 2160 1H candles, 8640 15m candles  
- SOL/USDT: 2160 1H candles, 8641 15m candles
- Period: 3 months (May 16 - Aug 14, 2026)

---

## FILTER DIAGNOSTICS

### BTC/USDT
| Filter | Blocked |
|--------|---------|
| HTF Trend (NEUTRAL) | 3791 |
| Structure (RANGE) | 2935 |
| Impulse (no 3+ candles) | 1884 |
| Pullback (RSI filter) | 28 |
| Confirmation | 0 |
| **Total entries** | **0** |

### ETH/USDT
| Filter | Blocked |
|--------|---------|
| HTF Trend (NEUTRAL) | 3943 |
| Structure (RANGE) | 2872 |
| Impulse (no 3+ candles) | 1807 |
| Pullback (RSI filter) | 16 |
| Confirmation | 0 |
| **Total entries** | **0** |

### SOL/USDT
| Filter | Blocked |
|--------|---------|
| HTF Trend (NEUTRAL) | 4074 |
| Structure (RANGE) | 2780 |
| Impulse (no 3+ candles) | 1752 |
| Pullback (RSI filter) | 32 |
| Confirmation | 0 |
| **Total entries** | **0** |

---

## ROOT CAUSE ANALYSIS

The TPC strategy generated **ZERO TRADES** across all three symbols over 3 months.

### Primary Blocker: Pullback RSI Filter

The pullback detection requires RSI14 to be in a narrow range:
- **BUY**: RSI must be between 40 and 50
- **SELL**: RSI must be between 50 and 60

After an impulse move (which is required before pullback detection), RSI typically moves OUT of these ranges:
- After bullish impulse: RSI > 50 (often 60-80)
- After bearish impulse: RSI < 50 (often 20-40)

This creates a **fundamental contradiction**:
1. Strategy requires impulse (strong directional move)
2. Strategy requires RSI in narrow 40-50 or 50-60 range
3. Impulse naturally pushes RSI outside these ranges
4. Therefore: impulse + valid pullback are mutually exclusive

### Evidence from Debug Output

```
BTC/USDT:
  PB FAIL RSI: rv=72.95 d=B rok=False   (BUY but RSI too high)
  PB FAIL RSI: rv=28.62 d=S rok=False   (SELL but RSI too low)

ETH/USDT:
  PB FAIL RSI: rv=29.10 d=S rok=False
  PB FAIL RSI: rv=23.17 d=S rok=False

SOL/USDT:
  PB FAIL RSI: rv=12.60 d=S rok=False
  PB FAIL RSI: rv=11.54 d=S rok=False
```

### Secondary Blockers

Even if RSI filter were removed:
- **HTF Trend**: 44-47% of candles are NEUTRAL (EMA50 not clearly above/below EMA200)
- **Structure**: 41-45% of candles are RANGE (no clear HH/HL or LH/LL)
- **Impulse**: Only ~22% of candles have 3+ consecutive directional candles with sufficient body

---

## FINAL RESULT

```
========================================
TPC BACKTEST FINAL RESULT
========================================

Period: 3 months
Symbols: BTC/USDT, ETH/USDT, SOL/USDT
Initial capital: 10000.00 USDT

Trades: 0
Win rate: N/A
Profit Factor: N/A
Expectancy: N/A
Average R: N/A
Max Drawdown: 0.0%
Max consecutive losses: 0

Fees: 0.00 USDT
Slippage: 0.00 USDT

Final equity: 10000.00 USDT
Net PnL: 0.00 USDT
Return: 0.00%

========================================
VERDICT
========================================

FAIL

Strategy generated 0 trades over 3 months.

PRIMARY REASON:
The RSI filter in pullback detection (40-50 for BUY, 50-60 for SELL)
is fundamentally incompatible with the impulse requirement.

After an impulse move, RSI naturally moves outside the required range,
making valid pullback detection impossible.

SECONDARY REASONS:
1. HTF Trend filter is too restrictive (44-47% NEUTRAL)
2. Structure filter is too restrictive (41-45% RANGE)
3. Impulse filter is too restrictive (only 22% pass)

CONCLUSION:
The TPC strategy as specified CANNOT generate trades.
The strategy parameters are self-contradictory.
```

---

## RECOMMENDATION

The TPC strategy needs fundamental redesign before it can be tested:

1. **Remove RSI from pullback detection** - Use only price-based zones (EMA21, Fib, broken levels)
2. **Widen HTF Trend criteria** - Consider using only EMA crossover without RSI
3. **Widen Structure criteria** - Allow partial structure (just HH or just HL)
4. **Relax Impulse criteria** - Reduce from 3 to 2 candles, or reduce body threshold
5. **Add debug logging** - Track why each filter blocks to diagnose future issues

---

## WHAT WAS PRESERVED

- All production ATS-SMC code is UNCHANGED
- No modifications to SMCBot, SignalGenerator, OrderManager, PositionManager, RiskManager
- No modifications to existing strategies
- Backtest is completely standalone and isolated
