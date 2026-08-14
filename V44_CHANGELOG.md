# ATS-SMC v44

## Runtime persistence

- Added durable `RuntimeState` persistence for equity, daily-start equity, daily loss, open-trade count, UTC equity day, and paper-position state.
- Risk Manager restores persisted state before the engine starts, preventing a process restart from resetting daily drawdown protection to `INITIAL_EQUITY`.
- Paper Trading position state is serialized and restored across process restarts.
- Live/testnet exchange recovery remains authoritative for the open-trade count; stale persisted counters cannot inflate it.
- Added regression tests for risk state survival across restart and paper-state payload preservation.

## Verification

- pytest: 89 passed
- compileall backend: PASS
- ZIP integrity: PASS
