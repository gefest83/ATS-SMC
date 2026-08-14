# ATS-SMC v43

## API / lifecycle integration fixes

- Added authenticated `/engine/start` and `/engine/stop` endpoints so the API can control the SMC engine lifecycle explicitly.
- Prevented duplicate engine tasks when `/engine/start` is called repeatedly.
- Engine shutdown now stops PositionManager, OrderManager and Telegram notifier before cancelling the engine task.
- FastAPI lifespan now registers the auto-start task and performs the same resource cleanup on application shutdown.
- Added regression coverage for start/duplicate-start/stop behavior.

## Verification

- pytest: 87 passed
- compileall backend: PASS
- ZIP integrity: PASS
- Note: the audit environment does not have the optional runtime package `ccxt` installed, so exchange-network integration was not executed against a live/testnet exchange. The full project requirements must be installed before runtime integration testing.
