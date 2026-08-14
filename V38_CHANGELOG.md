# ATS-SMC v38

## Fixes

- Preserved individual CCXT `fees[]` entries in `OrderResponse` so quote/settlement fees and fees in other currencies are not collapsed into one currency.
- `PositionManager.record_fill()` now aggregates multi-currency fees correctly.
- Added regression coverage for multi-currency fees and repeated cumulative fills.
- After a partial/filled Take Profit, remaining TP orders are resized against the actual remaining position so their aggregate quantity cannot exceed exposure.
- Stop-loss resizing now retains a stable protective `client_order_id`.
- TP/SL resizing uses stable protective IDs for recovery.

## Verification

- pytest: 79 passed
- compileall backend: PASS
- ZIP integrity: PASS
