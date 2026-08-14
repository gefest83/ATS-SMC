# ATS-SMC v41

## Risk Manager hardening

- Added strict configuration validation for risk percentage, daily drawdown, maximum open trades, initial equity, and minimum R:R.
- Position sizing now validates positive equity/prices/leverage and applies a leverage-aware notional/margin cap.
- Risk-based sizing remains stop-distance based; leverage only limits the maximum supportable notional.
- Added regression tests for margin-capped sizing and invalid risk configuration.

## Verification

- pytest: 84 passed
- compileall backend: PASS
- ZIP integrity: PASS
