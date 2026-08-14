# ATS-SMC v46

Paper Trading is a deterministic local execution simulator.

- Market orders fill at supplied prices.
- Limit and trigger orders can be filled through `process_price`.
- Virtual balance, open positions, realized PnL and fees are tracked.
- Partial closes and reversals are handled correctly.
- Mark-to-market and snapshots are available.
- Paper Trading regression tests cover round trips, partial closes, limit fills and snapshots.

Verification:
- pytest: 93 passed
- compileall backend: PASS
- ZIP integrity: PASS
