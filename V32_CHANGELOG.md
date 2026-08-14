# ATS-SMC v32

## PostgreSQL close/recovery idempotency

- Close/recovery is serialized per `position_id` with an asyncio lock.
- `Position CLOSED` and aggregate `Trade` persistence now happen in one DB transaction.
- Existing `Trade.position_id` is reused during recovery instead of creating a duplicate.
- A failed close transaction no longer removes the position from memory.
- Failed persistence leaves the position OPEN/pending recovery so a restart can reconcile exchange fills.
- Added regression coverage for concurrent `close_position()` calls.

## Verification

- pytest: **63 passed**
- `python -m compileall -q backend`: **PASS**
- ZIP integrity: verified after creation.
