# ATS-SMC v36

## Position persistence before protective orders

- The entry order is now persisted and the `Position` is created before any SL/TP order is submitted.
- The position quantity uses the actually filled entry quantity when the exchange reports a partial fill.
- SL and TP quantities are based on the actual filled position size, not the requested entry size.
- The entry fill is recorded immediately after the position is persisted.
- Protective-order IDs are persisted incrementally so recovery can see protection already created before a restart.
- If protective setup fails, existing protective orders are cancelled and an emergency market flatten uses the actual filled position quantity.
- If emergency flatten also fails, the persisted position is deliberately retained for reconciliation/recovery instead of being discarded.

## Regression coverage

- Added tests for partial entry fills and protection sizing.
- Added a test proving the position exists before the first protective order is submitted.

## Verification

- pytest: 75 passed
- python -m compileall -q backend: PASS
