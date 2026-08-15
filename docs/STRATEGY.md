# STRATEGY DOCUMENTATION

## Smart Money Trades Pro v2 (SMT Pro)

### Overview

SMT Pro is a trend-following strategy based on Smart Money Concepts (SMC) and ICT principles. It identifies market structure breaks, confirms with higher timeframe alignment, and enters with defined risk parameters.

---

## Core Parameters (DO NOT MODIFY WITHOUT UNDERSTANDING)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `structurePeriod` | 20 | Pivot confirmation period |
| `ADX Period` | 14 | ADX calculation period |
| `ATR Period` | 14 | ATR calculation period |
| `Min ATR %` | 0.3% | Minimum volatility filter |
| `Max BOS Dist` | 0.5 ATR | Maximum chase distance |
| `Cooldown` | 6 bars | Cooldown after stop loss |
| `Risk Per Trade` | 1% | Portfolio risk per trade |
| `TP1` | 40% | First take profit portion |
| `TP2` | 30% | Second take profit portion |
| `TP3` | 30% | Final take profit portion |

---

## Timeframes

| TF | Purpose |
|----|---------|
| M30 | Working timeframe for entries |
| 4H | Higher timeframe trend |
| 1D | Higher timeframe trend |

---

## Module 1: Market Structure

### Pivot Detection

**Pivot High:**
- Bar with highest high among N bars left and N bars right
- Confirmed only after `structurePeriod` bars pass
- N = 20 (structurePeriod)

**Pivot Low:**
- Bar with lowest low among N bars left and N bars right
- Confirmed only after `structurePeriod` bars pass

```python
# CRITICAL: No lookahead bias
# Pivots are confirmed with delay
pivot_highs = detect_pivots(high, low, structure_period=20)
# Shift by 20 to ensure confirmation
pivot_highs = pivot_highs.shift(20)
```

### BOS (Break of Structure)

**Bullish BOS:**
```
close > lastSwingHigh
```

**Bearish BOS:**
```
close < lastSwingLow
```

**Confirmation Type:** Body (CLOSE) - default
- Wick confirmation can be enabled but body is primary

After BOS:
- Break state resets
- Next BOS requires new structural level

### CHoCH (Change of Character)

**Bullish CHoCH:**
```
prevTrend == -1 AND currentTrend == +1
```

**Bearish CHoCH:**
```
prevTrend == +1 AND currentTrend == -1
```

Usage:
1. Reversal signal
2. Required for RANGE entries
3. Exit condition against position

---

## Module 2: HTF Trend

### Calculation

```python
trend_4h = calculate_structural_trend(candles_4h)  # -1, 0, +1
trend_1d = calculate_structural_trend(candles_1d)  # -1, 0, +1

hSum = trend_4h + trend_1d  # Range: -2 to +2
```

### Interpretation

| hSum | Meaning | LONG Allowed | SHORT Allowed |
|------|---------|--------------|---------------|
| +2 | Strong Bullish | ✓ | ✗ |
| +1 | Bullish | ✓ | ✗ |
| 0 | Neutral | ✗ | ✗ |
| -1 | Bearish | ✗ | ✓ |
| -2 | Strong Bearish | ✗ | ✓ |

**Requirements:**
- LONG: hSum > 0
- SHORT: hSum < 0

---

## Module 3: Market Regime

### ADX Classification

| ADX Value | Regime | Entry Rules |
|-----------|--------|-------------|
| < 15 | DEAD | No entries allowed |
| 15-25 | RANGE | BB bounce + CHoCH only |
| ≥ 25 | TREND | Normal BOS entries |

```python
if adx < 15:
    regime = "DEAD"      # No trading
elif adx < 25:
    regime = "RANGE"     # Restricted entries
else:
    regime = "TREND"     # Full entries
```

---

## Module 4: Voting System

### Votes (Maximum 3)

**For LONG:**
1. **HTF Vote:** hSum > 0 (bullish alignment)
2. **ADX Vote:** ADX ≥ 20
3. **Volume Vote:** volume > SMA20(volume) × 1.5

**For SHORT:**
1. **HTF Vote:** hSum < 0 (bearish alignment)
2. **ADX Vote:** ADX ≥ 20
3. **Volume Vote:** volume > SMA20(volume) × 1.5

### Filter Mode

**2-of-3 (Default):**
- Requires minimum 2 out of 3 votes
- More flexible, more signals

**ALL:**
- Requires all 3 votes
- Stricter, fewer signals

```python
votes_required = 2 if filter_mode == "2of3" else 3
```

---

## Filters

### 1. Impulse Filter

**Purpose:** Ensure sufficient momentum

```python
impulse = |close - open| + |prevClose - prevOpen|
impulseOk = impulse >= ATR × impulseMult
```

Default: `impulseMult = 1.0`

### 2. Min ATR Filter

**Purpose:** Avoid low-volatility environments

```python
atrPct = (ATR / close) × 100
atrOk = atrPct >= 0.3%
```

### 3. BOS Chase Filter

**Purpose:** Avoid entering too far from structure

```python
# For LONG
bosDistL = |close - swingHigh|
bosOkL = bosDistL <= ATR × 0.5

# For SHORT
bosDistS = |close - swingLow|
bosOkS = bosDistS <= ATR × 0.5
```

### 4. Cooldown Filter

**Purpose:** Prevent immediate re-entry after loss

```python
# After STOP LOSS
cooldown_bars = 6  # M30 bars

# Independent cooldowns
LONG_cooldown != SHORT_cooldown
```

### 5. Bollinger Range Filter

**Purpose:** Identify range bounce opportunities

**BB Parameters:**
- Period: 20
- StdDev: 2.0

**LONG Entry (in RANGE):**
```
low[current] <= BB_lower
AND
low[previous] > BB_lower  # New touch
AND
CHoCH detected
AND
bounce_count <= maxBounces (2)
```

**Reset Condition:**
```
close > BB_middle  # Reset bounce counter
```

---

## Signal Conditions

### Final LONG Signal

```python
bullSig = (
    bullBreak              # BOS detected
    AND regime != DEAD
    AND votesL >= 2        # or 3 if ALL mode
    AND rangeConditions
    AND impulseOk
    AND atrOk
    AND bosOkL             # Not chasing
    AND cdOkL              # Not in cooldown
)
```

### Final SHORT Signal

```python
bearSig = (
    bearBreak              # BOS detected
    AND regime != DEAD
    AND votesS >= 2        # or 3 if ALL mode
    AND rangeConditions
    AND impulseOk
    AND atrOk
    AND bosOkS             # Not chasing
    AND cdOkS              # Not in cooldown
)
```

---

## Entry & Targets

### Entry Price

```python
LONG:  entry = lastSwingHigh
SHORT: entry = lastSwingLow
```

### Target Levels (based on ATR)

**Target Range = ATR × 2**

**LONG:**
```python
TP1 = entry + (ATR × 2) × 0.8   # 1.6 ATR
TP2 = entry + (ATR × 2) × 1.6   # 3.2 ATR
TP3 = entry + (ATR × 2) × 2.8   # 5.6 ATR
SL  = entry - (ATR × 2) × 1.2   # 2.4 ATR
```

**SHORT:**
```python
TP1 = entry - (ATR × 2) × 0.8
TP2 = entry - (ATR × 2) × 1.6
TP3 = entry - (ATR × 2) × 2.8
SL  = entry + (ATR × 2) × 1.2
```

### Position Sizing

```python
riskAmount = portfolioValue × 1%
stopDist = |entry - SL|
positionSize = riskAmount / stopDist

# In RANGE regime
if regime == RANGE:
    positionSize *= 0.5
```

---

## Trade Management

### Partial Closes

| Level | Close % | Remaining |
|-------|---------|-----------|
| TP1 | 40% | 60% |
| TP2 | 30% | 30% |
| TP3 | 30% | 0% |

### Breakeven

**Activation:** +1R reached

```python
# For LONG
if high >= entry + initialRisk:
    SL = entry  # Move to breakeven

# For SHORT
if low <= entry - initialRisk:
    SL = entry  # Move to breakeven
```

### Trailing Stop (Optional, Default: OFF)

**Activation:** After TP1 hit

```python
# For LONG
trailStop = lastSwingLow - ATR × 0.25

# For SHORT
trailStop = lastSwingHigh + ATR × 0.25

# Never moves backward
trailStop = max(currentTrailStop, newTrailStop)  # LONG
trailStop = min(currentTrailStop, newTrailStop)  # SHORT
```

### Structure Exit (HIGH PRIORITY)

**Immediate exit on opposite BOS:**

```python
# LONG position
if bearish_BOS_detected:
    CLOSE_LONG immediately

# SHORT position
if bullish_BOS_detected:
    CLOSE_SHORT immediately
```

Does NOT wait for TP or SL.

### FLIP Logic

**LONG → SHORT:**
1. Close LONG position
2. Wait for fill confirmation
3. Open SHORT position

**SHORT → LONG:**
1. Close SHORT position
2. Wait for fill confirmation
3. Open LONG position

Never holds conflicting positions simultaneously.

---

## Risk Management

### Per-Trade Risk

```python
riskPerTrade = 1%  # Of portfolio value
```

### Maximum Exposure

| Limit | Value |
|-------|-------|
| Max Open Trades | Configurable (default: 3) |
| Daily Drawdown | Configurable (default: 5%) |
| Position Exposure | Configurable (default: 20%) |
| Symbol Exposure | Configurable (default: 10%) |
| Total Exposure | Configurable (default: 50%) |

### Pre-Order Checks

Before EVERY order:
1. ✓ Available balance check
2. ✓ Risk limit check
3. ✓ Max positions check
4. ✓ Symbol exposure check
5. ✓ Exchange limits check
6. ✓ Duplicate order check
7. ✓ Existing position check
8. ✓ Cooldown check
9. ✓ Emergency stop check

If ANY check fails → REJECT ORDER

---

## Anti-Repainting Rules

### CRITICAL PRINCIPLES

1. **Closed Candles Only**
   - Strategy uses ONLY fully closed candles
   - Current (incomplete) candle is NEVER used for signals

2. **Pivot Confirmation Delay**
   ```python
   # Pivot identified at bar T
   # Confirmed only at bar T + 20
   confirmed_pivot = pivot.shift(structure_period)
   ```

3. **HTF Data Availability**
   - 4H structure available only AFTER 4H candle closes
   - 1D structure available only AFTER 1D candle closes

4. **No Future Data**
   - Indicators calculated using historical data only
   - No peeking at future candles
   - Backtest matches live execution

---

## State Persistence

### What Persists Across Restarts

- ✓ Open positions
- ✓ Pending orders
- ✓ Cooldown state
- ✓ Breakeven state
- ✓ TP partial close state
- ✓ Trailing stop state
- ✓ Bounce counters

### What Resets

- ✗ Current candle data (re-fetched)
- ✗ Temporary indicator states (recalculated)
- ✗ Connection sessions (re-established)

---

## Example Signal Flow

```
1. Fetch M30, 4H, 1D candles (CLOSED only)
2. Calculate indicators (ADX, ATR, BB, Volume)
3. Detect pivots → Confirm after 20 bars
4. Determine HTF trend (4H + 1D)
5. Classify regime (DEAD/RANGE/TREND)
6. Check for BOS/CHoCH
7. Apply voting system (≥2 of 3)
8. Run filters (Impulse, ATR, BOS Chase, Cooldown)
9. If all pass → Generate signal
10. Calculate position size (1% risk)
11. Send to Risk Manager
12. If approved → Execute order
13. Monitor position (TP/SL/BE/Structure)
```

---

## Configuration Reference

```yaml
# Strategy Parameters
structure_period: 20
adx_period: 14
atr_period: 14
min_atr_pct: 0.3
max_bos_dist_atr: 0.5
cooldown_bars: 6
impulse_mult: 1.0

# Bollinger Bands
bb_period: 20
bb_stddev: 2.0
bb_lookback: 10
max_bounces: 2

# Volume
volume_sma: 20
volume_mult: 1.5

# ADX Thresholds
adx_dead: 15
adx_range: 25
adx_vote: 20

# Take Profit / Stop Loss
tp1_pct: 40
tp2_pct: 30
tp3_pct: 30
use_breakeven: true
use_trailing: false

# Filters
filter_mode: "2of3"  # or "ALL"

# Risk
risk_pct: 1.0
```

---

## Important Notes

1. **DO NOT modify mathematical parameters** without thorough backtesting
2. **ALWAYS test in PAPER mode** before LIVE trading
3. **Live trading disabled by default** - explicit enablement required
4. **Strategy is deterministic** - same input = same output
5. **No optimization performed** - parameters are fixed as per specification
