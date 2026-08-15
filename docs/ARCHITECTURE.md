# ARCHITECTURE DOCUMENTATION

## System Overview

ATS-SMT Pro is a professional algorithmic trading system implementing the Smart Money Trades Pro v2 strategy.

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │   Web UI    │  │  Telegram   │  │   REST API  │          │
│  │  (React)    │  │    Bot      │  │  (FastAPI)  │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                     APPLICATION LAYER                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │   Engine    │  │   Risk      │  │    Order    │          │
│  │ Controller  │  │  Manager    │  │   Manager   │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  Position   │  │  Portfolio  │  │    Trade    │          │
│  │   Manager   │  │   Manager   │  │   Logger    │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      DOMAIN LAYER                            │
│  ┌─────────────────────────────────────────────────┐         │
│  │           SMT Pro Strategy Core                  │         │
│  │  - Market Structure (BOS, CHoCH)                │         │
│  │  - HTF Trend Analysis (4H + 1D)                 │         │
│  │  - Market Regime (DEAD/RANGE/TREND)             │         │
│  │  - Voting System (2-of-3 filter)                │         │
│  │  - Filters (ATR, Impulse, BOS Chase, Cooldown)  │         │
│  │  - Bollinger Bands Range Entry                  │         │
│  └─────────────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   INFRASTRUCTURE LAYER                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │   Market    │  │  Exchange   │  │  Database   │          │
│  │    Data     │  │  Adapters   │  │ Repository  │          │
│  │  Service    │  │             │  │             │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  Technical  │  │    Rate     │  │   Health    │          │
│  │ Indicators  │  │   Limiter   │  │   Monitor   │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

## Module Responsibilities

### Backend Structure

```
backend/
├── api/                    # FastAPI REST endpoints
│   ├── routes/            # API route handlers
│   ├── middleware/        # Request/response middleware
│   └── websocket/         # Real-time WebSocket handlers
│
├── core/                   # Core business logic
│   ├── strategy/          # SMT Pro strategy implementation
│   │   └── smt_pro.py     # Main strategy class
│   ├── engine/            # Trading engine orchestration
│   ├── risk/              # Risk management
│   ├── execution/         # Order execution logic
│   ├── exchange/          # Exchange adapters
│   │   ├── base_adapter.py
│   │   └── binance_adapter.py
│   ├── market_data/       # Market data handling
│   ├── indicators/        # Technical indicators
│   │   └── technical.py
│   ├── positions/         # Position tracking
│   ├── orders/            # Order management
│   ├── portfolio/         # Portfolio accounting
│   ├── persistence/       # Database models & repositories
│   └── monitoring/        # Health checks & metrics
│
├── config/                 # Configuration management
│   └── settings.py
│
├── models/                 # Pydantic data models
│   └── schemas.py
│
├── services/               # Background services
│
├── tests/                  # Test suite
│   └── test_strategy.py
│
└── main.py                 # Application entry point
```

## Data Flow

1. **Market Data Ingestion**
   ```
   Exchange API → Adapter → Normalization → Candle Storage → Strategy
   ```

2. **Signal Generation**
   ```
   Candles → Indicators → Structure Analysis → Signal Decision
   ```

3. **Order Execution**
   ```
   Signal → Risk Check → Order Creation → Exchange → Confirmation
   ```

4. **Position Management**
   ```
   Fill Event → Position Update → TP/SL Monitoring → Exit Execution
   ```

## Key Design Principles

### 1. Separation of Concerns
- Strategy knows nothing about exchanges
- Exchange adapters know nothing about strategy
- Risk manager is independent and can veto any order

### 2. No Lookahead Bias
- All indicators use only closed candles
- Pivots confirmed after `structurePeriod` bars
- HTF data available only after candle close

### 3. Deterministic Execution
- Same input always produces same output
- State is explicitly tracked and persisted
- Restart recovery maintains continuity

### 4. Safety First
- Live trading disabled by default
- Explicit enablement required for real orders
- Multiple validation layers before execution

### 5. Multi-Symbol Isolation
- Each symbol has independent state
- Error in one symbol doesn't affect others
- Parallel processing where safe

### 6. Multi-Exchange Abstraction
- Unified interface across all exchanges
- Symbol normalization handled by adapters
- Exchange-specific limits respected

## State Management

### Persistent State (Database)
- Positions
- Orders
- Historical signals
- Portfolio balances
- Configuration

### Volatile State (Memory)
- Current candle data
- Indicator calculations
- Temporary signal states
- Connection sessions

### Recovery Process
1. Load persistent state from database
2. Reconnect to exchanges
3. Sync open orders with exchange
4. Sync positions with exchange
5. Resume strategy state
6. Continue normal operation

## Communication Patterns

### Synchronous
- REST API requests
- Configuration queries
- Status checks

### Asynchronous
- Market data updates
- Order executions
- Telegram notifications
- WebSocket broadcasts

## Security Considerations

1. **API Keys**
   - Never logged
   - Never exposed in responses
   - Stored encrypted at rest
   - Environment variables only

2. **Live Trading**
   - Disabled by default
   - Requires explicit config flag
   - Additional validation layer

3. **Rate Limiting**
   - Per-exchange limits enforced
   - Backoff on errors
   - Queue for excess requests

## Monitoring & Observability

### Metrics Tracked
- Engine uptime
- Signal count
- Order success rate
- Latency percentiles
- Error rates
- Exchange connection status

### Health Checks
- Database connectivity
- Exchange API availability
- Market data freshness
- Strategy calculation time

### Logging
- Structured JSON logs
- Correlation IDs for tracing
- Separate streams for different levels
- Sensitive data redaction

## Extension Points

### Adding New Exchange
1. Create adapter class extending `ExchangeAdapter`
2. Implement all abstract methods
3. Register with `@register_adapter` decorator
4. Add configuration options
5. Test with testnet first

### Adding New Indicator
1. Implement in `backend/core/indicators/`
2. Ensure no lookahead bias
3. Add to strategy config if needed
4. Update tests

### Adding New Strategy
1. Create new strategy class
2. Implement signal generation interface
3. Keep isolated from existing strategies
4. Configure separately

## Performance Considerations

1. **Async I/O**
   - All exchange calls are async
   - Non-blocking market data fetches
   - Concurrent symbol processing

2. **Caching**
   - Market metadata cached
   - Indicator results memoized where safe
   - Configuration loaded once

3. **Database**
   - Indexed queries
   - Batch inserts for historical data
   - Connection pooling

## Testing Strategy

### Unit Tests
- Individual components in isolation
- Mock external dependencies
- Fast execution

### Integration Tests
- Component interactions
- Database operations
- Exchange adapter functionality

### Strategy Tests
- Mathematical correctness
- No lookahead bias verification
- Edge case handling

### End-to-End Tests
- Full system workflow
- Paper trading mode
- Simulated market conditions
