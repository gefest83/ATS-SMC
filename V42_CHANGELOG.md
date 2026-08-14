# ATS-SMC v42

## Exchange precision and market-limit safety

- Order amounts and prices no longer fall back to unrounded values when CCXT precision conversion fails.
- Amount limits (`min`/`max`) are enforced after precision normalization.
- Price limits (`min`/`max`) are enforced after precision normalization.
- Cost limits (`min`/`max`) are enforced when a normalized price is available; contract markets use `contractSize` when provided.
- Orders that round to zero are rejected.
- Added regression tests for market amount limits and precision-conversion failures.
- Archive root now matches the release version: `ats-smc-fixed-v42/`.

## Verification

- pytest: 86 passed
- compileall backend: PASS
- ZIP integrity: PASS
