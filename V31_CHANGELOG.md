# ATS-SMC v31

## Persistence / Recovery audit fixes

- Trade-history reconciliation now ignores fills timestamped before the persisted position entry time. The 60-second pre-entry API query window remains for clock-skew tolerance, but old fills cannot contaminate recovered quantity, cost, fees, or PnL.
- Fees without an explicit CCXT currency are treated as quote/settlement fees during reconciliation, matching the normal order-fill path. Fees explicitly denominated in another currency remain in `fee_unconverted` and are not subtracted from quote PnL.
- Added regression coverage for pre-entry fill contamination and missing fee currency.

## Verification

- pytest: 62 passed
- compileall backend: PASS
