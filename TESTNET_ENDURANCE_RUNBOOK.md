# ATS-SMC v48 — TESTNET ENDURANCE RUNBOOK

## PRE-FLIGHT

### 1. Verify Configuration

```bash
py -3.13 -c "
from backend.config import settings
checks = {
    'TRADING_MODE': (settings.TRADING_MODE, 'testnet'),
    'TESTNET_TRADING_ENABLED': (settings.TESTNET_TRADING_ENABLED, True),
    'LIVE_TRADING_ENABLED': (settings.LIVE_TRADING_ENABLED, False),
    'EXCHANGE': (settings.EXCHANGE, 'binance'),
    'EXCHANGE_MODE': (settings.EXCHANGE_MODE, 'testnet'),
}
for k, (v, expected) in checks.items():
    status = 'PASS' if v == expected else 'FAIL'
    print(f'  [{status}] {k} = {v}')
"
```

### 2. Run Pre-Endurance Tests

```bash
py -3.13 -m pytest tests/ -q --tb=short
py -3.13 -m compileall backend/ -q
```

Expected: 148 passed, 0 failed, no compilation errors.

### 3. Check Telegram

```bash
py -3.13 -c "
from backend.config import settings
print('  TOKEN:', 'PRESENT' if settings.TELEGRAM_BOT_TOKEN else 'MISSING')
print('  CHAT_ID:', 'PRESENT' if settings.TELEGRAM_CHAT_ID else 'MISSING')
"
```

---

## START

### Launch Command (with PostgreSQL)

```bash
# Terminal 1: Start PostgreSQL
docker compose up postgres -d

# Terminal 2: Start bot
py -3.13 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Launch Command (without PostgreSQL — degradation mode)

```bash
py -3.13 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Note: Without PostgreSQL, positions/orders/trades will NOT be persisted to disk.
State is held in memory only. A restart loses all state.

### Enable Auto-Start Engine

Edit `.env`:
```
AUTO_START_ENGINE=true
```

Or start engine manually via API:
```bash
curl -X POST http://localhost:8000/engine/start
```

---

## HEALTH CHECKS

### Process Alive

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Trading Status

```bash
curl http://localhost:8000/status
# Expected: {"trading_mode": "testnet", "exchange": "binance", "running": true, ...}
```

### Risk Status

```bash
curl http://localhost:8000/risk
# Expected: {"current_equity": ..., "open_trades": ..., ...}
```

---

## MONITORING (during 24-72h run)

### Periodic Checks (every 1-4 hours)

1. **Health**: `curl http://localhost:8000/health`
2. **Status**: `curl http://localhost:8000/status`
3. **Risk**: `curl http://localhost:8000/risk`
4. **Log tail**: `tail -50 logs/bot_execution.log`
5. **Process**: check Python process is running (no crash)
6. **Telegram**: verify startup/close messages received

### What to Watch

| Metric | Healthy | Alert |
|--------|---------|-------|
| Process alive | yes | no |
| Health endpoint | `{"status":"ok"}` | error/timeout |
| Engine running | `true` | `false` (crashed?) |
| Open trades | <= 3 | > 3 (risk leak) |
| Equity | ~10000 | dropping > 5% |
| REST errors | infrequent | frequent (API ban?) |
| Log file size | growing slowly | unbounded |
| WebSocket reconnections | occasional | rapid loop |
| RAM usage | stable | growing continuously |

### Manual Log Analysis

```bash
# Count errors in last hour
py -3.13 -c "
import re
from datetime import datetime, timedelta
from pathlib import Path

cutoff = datetime.now() - timedelta(hours=1)
errors = 0
for line in Path('logs/bot_execution.log').read_text().splitlines():
    if 'ERROR' in line or 'CRITICAL' in line:
        errors += 1
print(f'Errors in log: {errors}')
"
```

---

## TELEGRAM MONITORING

During endurance test, watch for these Telegram messages:

- **Startup**: bot started
- **Position open**: `PAPER/TESTNET BUY/SELL BTC/USDT size=...`
- **Position close**: `PAPER/TESTNET close BTC/USDT: stop_loss/take_profit`
- **Errors**: Telegram error messages

If no messages appear for >4 hours during market hours, check engine status.

---

## GRACEFUL SHUTDOWN

### Method 1: Ctrl+C

Press `Ctrl+C` in the terminal running uvicorn. The lifespan handler will:
1. Stop the engine task
2. Stop order monitor
3. Stop position sync loop
4. Persist runtime state
5. Close exchange connection
6. Shutdown Telegram bot

### Method 2: API

```bash
curl -X POST http://localhost:8000/engine/stop
```

### Method 3: Process Kill

```bash
# Find process
tasklist | findstr python

# Kill
taskkill /PID <pid> /F
```

### Post-Shutdown Cleanup

After test, verify no open orders remain:

```bash
curl http://localhost:8000/risk
# open_trades should be 0
```

---

## EMERGENCY STOP

If bot behaves unexpectedly:

1. **Stop engine immediately**: `curl -X POST http://localhost:8000/engine/stop`
2. **Kill process**: `taskkill /PID <pid> /F`
3. **Check Binance Testnet**: manually cancel any open orders via web UI
4. **Check logs**: `logs/bot_execution.log`

---

## POST-TEST CLEANUP

1. Stop engine: `curl -X POST http://localhost:8000/engine/stop`
2. Verify: `curl http://localhost:8000/risk` → `open_trades: 0`
3. Check Binance Testnet web UI for any leftover orders
4. Archive logs: `copy logs\bot_execution.log logs\endurance_YYYYMMDD.log`
5. Review Telegram messages for anomalies

---

## KNOWN LIMITATIONS

1. **No rotation on logs**: `logs/bot_execution.log` grows unbounded. For 72h test, monitor file size manually. If >100MB, truncate manually.

2. **No PostgreSQL locally**: All state is in-memory. A process restart loses positions/orders. For endurance test, minimize restarts.

3. **Exchange position sync only for futures**: `sync_with_exchange()` is skipped for spot market. Spot positions rely on order monitor loop (5s interval).

4. **Risk counter can drift**: If `trade_closed()` is called without proper `event_id`, the counter may not decrement correctly. Existing `_closed_event_ids` set prevents double-close.

5. **Rate limits**: Binance Testnet has rate limits. The 60s poll interval is safe, but order monitor loop (5s) with many open orders could hit limits.

---

## TROUBLESHOOTING

| Problem | Solution |
|---------|----------|
| Engine won't start | Check `.env`, ensure `AUTO_START_ENGINE=true` or POST `/engine/start` |
| No Telegram messages | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` |
| Orders not placing | Check Binance Testnet API keys, check logs for errors |
| High CPU | Check for reconnect loops in logs, restart bot |
| RAM growing | Restart bot, check for task leaks in logs |
| Exchange connection lost | Bot will retry in main loop (10s sleep on error) |

---

*Last updated: 2026-08-12*
