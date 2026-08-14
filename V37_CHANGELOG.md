# ATS-SMC v37

## Protective-order recovery after restart

- Added deterministic client order IDs for SL/TP protection derived from position ID.
- Persisted protective client IDs with the position metadata before submission.
- Added startup recovery that searches the exchange for existing SL/TP by client order ID before creating a replacement.
- Missing protection is recreated using the current persisted position quantity.
- Existing protective orders are re-linked instead of duplicated.
- Added regression tests for idempotent protective recovery and stable client IDs.

## Verification

- pytest: 77 passed
- python -m compileall -q backend: PASS
