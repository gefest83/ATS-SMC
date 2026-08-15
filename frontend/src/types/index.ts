export interface MarketData {
  symbol: string;
  price: number;
  change24h: number;
  trendM30: number;
  trend4H: number;
  trend1D: number;
  adx: number;
  regime: 'DEAD' | 'RANGE' | 'TREND';
  atr: number;
  volume: number;
  votes: number;
  signal: 'LONG' | 'SHORT' | null;
  position: 'LONG' | 'SHORT' | null;
  health: 'HEALTHY' | 'DEGRADED' | 'STALE' | 'OFFLINE';
}

export interface Position {
  id: string;
  exchange: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entry: number;
  currentPrice: number;
  sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  quantity: number;
  remainingQuantity: number;
  pnl: number;
  rr: number;
  breakevenActive: boolean;
  trailingActive: boolean;
  openedAt: string;
}

export interface Signal {
  id: string;
  timestamp: string;
  exchange: string;
  symbol: string;
  action: 'LONG' | 'SHORT';
  bos: boolean;
  choch: boolean;
  htf4H: number;
  htf1D: number;
  adx: number;
  volume: number;
  votes: number;
  regime: string;
  entry: number;
  sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  risk: number;
}

export interface Order {
  id: string;
  exchange: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  type: string;
  quantity: number;
  price: number;
  status: string;
  orderId: string;
  createdAt: string;
  updatedAt: string;
}

export interface RiskMetrics {
  equity: number;
  balance: number;
  availableBalance: number;
  dailyPnl: number;
  dailyDrawdown: number;
  riskPerTrade: number;
  openTrades: number;
  exposure: number;
  maxExposure: number;
  riskStatus: 'OK' | 'WARNING' | 'CRITICAL';
}

export interface EngineStatus {
  running: boolean;
  mode: 'PAPER' | 'TESTNET' | 'LIVE';
  uptime: number;
  health: 'HEALTHY' | 'DEGRADED' | 'ERROR';
  lastUpdate: string;
}

export interface StrategySettings {
  structurePeriod: number;
  confirmationType: 'Body' | 'Wick';
  htf1: string;
  htf2: string;
  adxTh: number;
  adxTrend: number;
  adxDead: number;
  filterMode: '2of3' | 'ALL';
  volMult: number;
  useImpulse: boolean;
  impulseMult: number;
  useRangeBounce: boolean;
  bbLookback: number;
  maxBounces: number;
  minAtrPct: number;
  maxBosDistAtr: number;
  useCooldown: boolean;
  cooldownBars: number;
  riskPct: number;
  tp1Pct: number;
  tp2Pct: number;
  tp3Pct: number;
  useBreakeven: boolean;
  useTrail: boolean;
}

export interface LogEntry {
  timestamp: string;
  level: 'INFO' | 'WARNING' | 'ERROR' | 'TRADE' | 'SIGNAL' | 'RISK' | 'EXCHANGE';
  message: string;
  component?: string;
  symbol?: string;
  exchange?: string;
}
