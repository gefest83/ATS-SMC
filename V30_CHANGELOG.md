# ATS-SMC v30

## Fixes
- Trade-history reconciliation now aggregates CCXT `fee` or `fees` arrays.
- Quote/settlement fees are included in net PnL; non-quote fees remain in `fee_unconverted`.
- SQLAlchemy timestamps use timezone-aware UTC (`datetime.now(timezone.utc)`).
- Closed-position Trade persistence is idempotent using `Trade.position_id`.
- `init_db.py` includes a forward migration for the new `trades.position_id` column and unique index.
- Added regression coverage for multiple fees during reconciliation.

## Verification
- `pytest -q`: 61 passed
- `python -m compileall -q backend`: PASS
- No remaining `datetime.utcnow()` in Python sources.
