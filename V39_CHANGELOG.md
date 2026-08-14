# ATS-SMC v39

## Recovery / protective-order fixes

- Recovery now treats persisted exchange order IDs as hints, not proof that a protective order still exists.
- Missing/stale Stop Loss orders are recreated using the stable protective client ID.
- Recovery can locate existing protective orders by client ID or by the persisted native order ID.
- Take Profit recovery rebuilds the TP set from exchange-confirmed orders.
- TP quantities are allocated from the current position quantity, preventing stale initial quantities from exceeding remaining Futures exposure.
- Added regression coverage for stale SL/TP IDs, partial exposure, and recovery without native client-ID lookup.

## Verification

- pytest: 81 passed
- compileall backend: PASS
- ZIP integrity: PASS
