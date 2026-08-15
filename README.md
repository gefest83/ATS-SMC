# ATS-SMT Pro - Professional Algorithmic Trading System

## Smart Money Trades Pro v2 Strategy

A professional-grade algorithmic trading bot implementing the Smart Money Trades Pro (SMT Pro) strategy with multi-exchange, multi-symbol support.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     WEB DASHBOARD                            │
│                  (React + WebSocket)                         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND                         │
│                    REST API + WebSocket                      │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   STRATEGY    │  │     RISK      │  │     ORDER     │
│     CORE      │  │   MANAGER     │  │   MANAGER     │
└───────────────┘  └───────────────┘  └───────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  EXECUTION ENGINE                            │
│            (Multi-Exchange Support)                          │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   BINANCE     │  │     OKX       │  │    BYBIT      │
│   ADAPTER     │  │   ADAPTER     │  │   ADAPTER     │
└───────────────┘  └───────────────┘  └───────────────┘
        │                   │                   │
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   BINANCE     │  │     OKX       │  │    BYBIT      │
│     API       │  │     API       │  │     API       │
└───────────────┘  └───────────────┘  └───────────────┘
```

## Directory Structure

```
/workspace
├── backend/
│   ├── api/              # FastAPI endpoints
│   ├── core/
│   │   ├── strategy/     # SMT Pro strategy implementation
│   │   ├── engine/       # Main trading engine
│   │   ├── risk/         # Risk management
│   │   ├── execution/    # Order execution
│   │   ├── exchange/     # Exchange adapters
│   │   ├── market_data/  # Market data handling
│   │   ├── portfolio/    # Portfolio tracking
│   │   ├── positions/    # Position management
│   │   ├── orders/       # Order management
│   │   ├── indicators/   # Technical indicators
│   │   ├── persistence/  # Database models & migrations
│   │   └── monitoring/   # Health checks & observability
│   ├── config/           # Configuration management
│   ├── models/           # Pydantic models
│   ├── services/         # Background services
│   └── tests/            # Test suite
├── frontend/
│   └── dashboard/        # React dashboard
├── infra/
│   └── docker/           # Docker configuration
├── docs/                 # Documentation
├── .env.example          # Environment template
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

## Features

### Strategy: Smart Money Trades Pro v2

- **Timeframes**: M30 (working), 4H & 1D (higher timeframes)
- **Market Structure**: Pivot High/Low detection with 20-bar confirmation
- **BOS (Break of Structure)**: Body-based confirmation
- **CHoCH (Change of Character)**: Trend reversal detection
- **HTF Trend**: Combined 4H + 1D trend analysis
- **Market Regime**: DEAD / RANGE / TREND based on ADX
- **Voting System**: 2-of-3 filter (HTF, ADX, Volume)
- **Filters**: Impulse, ATR, BOS Chase, Cooldown
- **Bollinger Bands**: Range entry detection with bounce counter
- **Risk Management**: 1% per trade, dynamic position sizing
- **Exit Strategy**: TP1 (40%), TP2 (30%), TP3 (30%), SL, Breakeven, Structure Exit

### Multi-Exchange Support

Supported exchanges:
1. Binance
2. OKX
3. Bybit
4. MEXC
5. HTX
6. BingX
7. Bitget
8. Gate.io
9. KuCoin
10. Kraken

### Trading Modes

- **PAPER**: Virtual trading with real market data
- **TESTNET**: Testnet trading with test API keys
- **LIVE**: Live trading (disabled by default)

### Supported Symbols

BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, ENA/USDT, ART/USDT, ADA/USDT, TRX/USDT, DOGE/USDT, SUI/USDT

### Web Dashboard

Real-time monitoring:
- System status & health
- Market data for all symbols
- Active positions with PnL
- Signal history
- Order book
- Risk metrics
- Interactive charts
- Engine controls (Start/Stop/Emergency)

### Telegram Notifications

- Bot started/stopped
- Trade signals (LONG/SHORT)
- Order executions
- TP/SL hits
- Breakeven activation
- Structure exits
- FLIP operations
- Error alerts

### Safety Features

- No duplicate orders (idempotency)
- Exchange state reconciliation
- Position recovery after restart
- Rate limiting & retries
- Emergency stop
- Maximum exposure limits
- Daily drawdown protection

## Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Node.js 18+ (for frontend)
- Docker & Docker Compose (optional)

### Installation

1. **Clone repository**
```bash
cd /workspace
```

2. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Setup database**
```bash
createdb ats_smt_db
# Or use Docker: docker-compose up -d postgres
```

5. **Configure environment**
```bash
cp .env.example .env
# Edit .env with your settings
```

6. **Run migrations**
```bash
python -m backend.core.persistence.migrations
```

7. **Start backend**
```bash
python -m backend.api.main
```

8. **Start frontend** (if using separate frontend)
```bash
cd frontend/dashboard
npm install
npm run dev
```

### Docker Deployment

```bash
cd infra/docker
docker-compose up -d
```

## Configuration

See `.env.example` for all available options.

Key settings:
- `TRADING_MODE`: paper | testnet | live
- `EXCHANGES`: comma-separated list
- `SYMBOLS`: canonical format (BTC/USDT)
- `RISK_PCT`: risk per trade (default 1%)
- `MAX_OPEN_TRADES`: maximum concurrent positions
- `LIVE_TRADING_ENABLED`: must be explicitly set to true

## API Endpoints

### System
- `GET /health` - Health check
- `GET /status` - System status
- `GET /config` - Current configuration

### Markets
- `GET /markets` - Available markets
- `GET /symbols` - Trading symbols

### Trading
- `GET /positions` - Active positions
- `GET /orders` - Order history
- `GET /signals` - Signal history
- `GET /trades` - Executed trades
- `GET /risk` - Risk metrics

### Control
- `POST /engine/start` - Start engine
- `POST /engine/stop` - Stop engine
- `POST /engine/pause` - Pause trading
- `POST /engine/resume` - Resume trading
- `POST /engine/emergency-stop` - Emergency shutdown

## Testing

```bash
pytest backend/tests/ -v
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Strategy Details](docs/STRATEGY.md)
- [Exchange Setup](docs/EXCHANGE_SETUP.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Risk Management](docs/RISK_MANAGEMENT.md)
- [API Reference](docs/API.md)

## Important Warnings

⚠️ **LIVE TRADING IS DISABLED BY DEFAULT**

Do not enable live trading until you have:
1. Thoroughly tested in PAPER mode
2. Verified all configurations
3. Set appropriate risk limits
4. Understood all risks

⚠️ **NO FINANCIAL ADVICE**

This software is provided as-is. Trading cryptocurrencies involves substantial risk of loss. Past performance does not guarantee future results.

## License

Proprietary - All rights reserved

## Support

For issues and questions, please refer to the documentation or contact support.
