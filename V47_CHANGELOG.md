# ATS-SMC v47

Paper Trading stress pass.

- 100 consecutive round trips without fee drift.
- 100 consecutive round trips with exact fee accounting.
- Partial TP followed by snapshot/recovery preserves remaining exposure.
- TP price-stream execution closes the position once and records one trade.
- Full regression suite passes.

Verification:
- pytest: 97 passed
- compileall backend: PASS
- ZIP integrity: PASS
