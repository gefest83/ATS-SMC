# ATS-SMC v48

Runtime startup fix.

- Added the missing `LOG_LEVEL` setting required by `backend/utils/logger.py`.
- Added regression coverage for the configuration/logging contract.
- Full test suite: 99 passed.
- `compileall backend`: PASS.

Runtime note:
- `requirements.txt` pins `ccxt==4.2.96`.
- The audit environment could not install that package, so a real exchange connection
  was not executed here.
