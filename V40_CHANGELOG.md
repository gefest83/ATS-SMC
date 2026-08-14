# ATS-SMC v40

## Recovery / Futures protection audit

- Historical terminal orders found by client ID are no longer treated as active protective orders.
- Recovery validates terminal state for both native client-ID lookup and exchange-wide historical lookup.
- Added regression coverage for stale historical protective orders.
- Corrected archive root to `ats-smc-fixed-v40/`.

## Verification

- pytest: 82 passed
- compileall backend: PASS
