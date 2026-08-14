# ATS-SMC — Algorithmic Trading System with Smart Money Concepts

ATS-SMC объединяет SMC-анализ и торговую инфраструктуру в едином backend.

## Реализовано

- SMC: FVG, Order Blocks, BOS/CHOCH.
- EMA/RSI/ATR на pandas.
- 7 адаптеров: Binance, Bybit, OKX, Bitget, MEXC, KuCoin, Gate.io.
- Paper и защищённый Live режим.
- Risk-based position sizing и daily drawdown protection.
- Order/Position Manager с частичными TP, breakeven и trailing stop.
- PostgreSQL persistence для позиций и ордеров.
- FastAPI API с Bearer-аутентификацией для защищённого режима.
- Telegram notifications.
- StrategyRegistry, StrategyManager и MarketStream.

## Важное ограничение

Проект **не следует считать production-ready для реальных средств**, пока не выполнена реальная проверка каждого выбранного exchange adapter на testnet/sandbox. Единый интерфейс CCXT не гарантирует одинаковую семантику trigger/SL/TP ордеров на всех биржах.

## Быстрый старт

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env
```

Запуск API:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Или Docker:

```bash
docker compose up -d --build
docker compose exec backend python -m backend.db.init_db
```

API:

```text
http://localhost:8000/docs
http://localhost:8000/health
```

## Paper Trading

Для безопасного режима:

```env
TRADING_MODE=paper
EXCHANGE=binance
EXCHANGE_MODE=testnet
LIVE_TRADING_ENABLED=false
```

## Live Trading

Live намеренно защищён несколькими условиями:

```env
TRADING_MODE=live
EXCHANGE_MODE=live
LIVE_TRADING_ENABLED=true
API_AUTH_ENABLED=true
API_ACCESS_TOKEN=<strong-random-token>
```

Не включайте live, пока выбранная биржа не проверена на testnet/sandbox и не подтверждены размеры, tick size, trigger order semantics и позиционный режим.

## Тесты

```bash
pytest -q
python -m compileall -q backend
```

## Структура

```text
backend/
├── main.py
├── config.py
├── api/
├── core/
│   ├── analysis/
│   ├── engine/
│   ├── exchange/
│   ├── execution/
│   ├── monitoring/
│   ├── portfolio/
│   ├── risk/
│   ├── strategy/
│   ├── websocket/
│   ├── order_manager.py
│   └── position_manager.py
├── db/
└── utils/
tests/
docker/
docker-compose.yml
```


## Safe exchange testnet mode

Use `TRADING_MODE=testnet` for real orders on an exchange sandbox/testnet.
This is separate from `paper` and `live`:

- `paper`: no exchange orders are submitted.
- `testnet`: exchange orders are submitted only when `EXCHANGE_MODE=testnet`
  and `TESTNET_TRADING_ENABLED=true`.
- `live`: real exchange orders require `EXCHANGE_MODE=live`,
  `LIVE_TRADING_ENABLED=true`, API authentication, and exchange credentials.

Example:

```env
TRADING_MODE=testnet
TESTNET_TRADING_ENABLED=true
LIVE_TRADING_ENABLED=false
EXCHANGE_MODE=testnet
EXCHANGE=binance
```

Do not set `EXCHANGE_MODE=live` when testing.
