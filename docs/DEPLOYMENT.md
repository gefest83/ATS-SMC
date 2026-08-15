# ATS-SMT Pro - Deployment Guide

## Quick Start

### Prerequisites

- Python 3.10+ or Docker with docker-compose
- PostgreSQL 15+ (or use Docker)
- API keys for supported exchanges (for live trading)

### Option 1: Docker Compose (Recommended)

1. **Clone and configure:**
```bash
cd /workspace
cp .env.example .env
# Edit .env with your settings
```

2. **Start all services:**
```bash
docker-compose up -d
```

3. **Check logs:**
```bash
docker-compose logs -f backend
```

4. **Access API:**
- Backend API: http://localhost:8000
- Health check: http://localhost:8000/health
- Dashboard: http://localhost:3000 (when frontend is implemented)

5. **Stop services:**
```bash
docker-compose down
```

### Option 2: Manual Installation

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Configure environment:**
```bash
cp .env.example .env
# Edit .env with your settings
```

3. **Setup database (PostgreSQL):**
```bash
# Create database
createdb ats_smt_pro

# Or connect to existing PostgreSQL instance
export DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/ats_smt_pro"
```

4. **Run database migrations:**
```bash
# Initialize database tables
python -m backend.core.persistence.database init
```

5. **Start the backend:**
```bash
# Development mode
python -m uvicorn backend.api.app:app --reload --host 0.0.0.0 --port 8000

# Production mode
python -m uvicorn backend.api.app:app --host 0.0.0.0 --port 8000 --workers 4
```

6. **Start trading engine:**
```bash
python -m backend.main
```

## Configuration

### Environment Variables

See `.env.example` for all available options:

```bash
# Trading Mode
TRADING_MODE=paper          # paper, testnet, live

# Exchanges
EXCHANGES=binance           # comma-separated list

# Symbols
SYMBOLS=BTC/USDT,ETH/USDT   # canonical format

# Risk
RISK_PCT=1.0                # risk per trade (%)
MAX_OPEN_TRADES=3           # maximum concurrent positions

# Strategy
STRUCTURE_PERIOD=20
ADX_DEAD=15
ADX_TREND=25
FILTER_MODE=2of3

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/dbname

# Telegram
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

### Trading Modes

#### Paper Trading (Default)
- Real market data
- Simulated orders and positions
- Virtual balance tracking
- No risk of real losses

```bash
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
```

#### Testnet Trading
- Real exchange testnet APIs
- Test funds provided by exchange
- Same execution as live

```bash
TRADING_MODE=testnet
LIVE_TRADING_ENABLED=false
```

#### Live Trading
- Real money at risk
- Requires explicit enable flag
- Additional safety checks

```bash
TRADING_MODE=live
LIVE_TRADING_ENABLED=true
```

⚠️ **WARNING**: Never enable live trading without thorough testing!

## Exchange Setup

### Binance

1. Create API key at https://www.binance.com/en/my/settings/api-management
2. Enable Spot & Margin Trading
3. Enable Testnet if using test mode
4. Add credentials to secure storage (not in code!)

### OKX, Bybit, etc.

Similar process for each exchange. Refer to `docs/EXCHANGE_SETUP.md` for detailed instructions.

## Security Best Practices

1. **Never commit API keys**
   - Use environment variables
   - Use secrets management in production

2. **Enable IP whitelisting** on exchange API keys

3. **Use minimum required permissions**
   - Enable only necessary API permissions
   - Disable withdrawal permissions

4. **Regular security audits**
   - Review logs regularly
   - Monitor for unusual activity

5. **Database security**
   - Use strong passwords
   - Enable SSL connections
   - Regular backups

## Monitoring

### Health Checks

```bash
# API health
curl http://localhost:8000/health

# System status
curl http://localhost:8000/status

# Risk status
curl http://localhost:8000/risk
```

### Logs

```bash
# View backend logs
docker-compose logs -f backend

# Filter by level
docker-compose logs -f backend | grep ERROR
```

### Metrics

Prometheus metrics available at `/metrics` endpoint (when enabled).

## Troubleshooting

### Database Connection Issues

```bash
# Check PostgreSQL is running
docker-compose ps postgres

# Test connection
psql -h localhost -U atsuser -d ats_smt_pro
```

### API Not Responding

```bash
# Check backend container
docker-compose ps backend

# View logs
docker-compose logs backend

# Restart service
docker-compose restart backend
```

### Strategy Not Generating Signals

1. Check market data is being received
2. Verify ADX is not in DEAD zone (< 15)
3. Ensure voting system conditions are met
4. Check cooldown is not active

## Backup & Recovery

### Database Backup

```bash
# Backup
pg_dump -h localhost -U atsuser ats_smt_pro > backup.sql

# Restore
psql -h localhost -U atsuser ats_smt_pro < backup.sql
```

### Position Recovery

After system restart:
1. Engine automatically loads open positions from database
2. Reconciles with exchange state
3. Continues monitoring TP/SL levels

## Production Deployment

### Recommended Infrastructure

- **Database**: Managed PostgreSQL (AWS RDS, Google Cloud SQL)
- **Backend**: Container orchestration (Kubernetes, ECS)
- **Monitoring**: Prometheus + Grafana
- **Logging**: ELK stack or similar
- **Secrets**: HashiCorp Vault or cloud secrets manager

### High Availability

For production trading:
1. Deploy multiple backend instances
2. Use database replication
3. Implement proper failover
4. Monitor exchange connectivity
5. Set up alerting for critical events

## Support

For issues and questions:
1. Check documentation in `/docs`
2. Review logs for error messages
3. Verify configuration settings
4. Test in paper mode first
