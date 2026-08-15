import axios from 'axios';
import type {
  MarketData,
  Position,
  Signal,
  Order,
  RiskMetrics,
  EngineStatus,
  StrategySettings,
  LogEntry,
} from '../types';

const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

export const dashboardService = {
  // System
  getHealth: () => api.get('/health'),
  getStatus: () => api.get<EngineStatus>('/status'),
  getConfig: () => api.get('/config'),

  // Markets
  getMarkets: () => api.get('/markets'),
  getSymbols: () => api.get('/symbols'),
  getMarketData: (symbol: string) => api.get<MarketData>(`/markets/${symbol}`),

  // Positions
  getPositions: () => api.get<Position[]>('/positions'),
  getPosition: (id: string) => api.get<Position>(`/positions/${id}`),

  // Orders
  getOrders: () => api.get<Order[]>('/orders'),
  getSignals: () => api.get<Signal[]>('/signals'),

  // Risk
  getRisk: () => api.get<RiskMetrics>('/risk'),

  // Logs
  getLogs: (level?: string) => api.get<LogEntry[]>('/logs', { params: { level } }),

  // Engine Control
  startEngine: () => api.post('/engine/start'),
  stopEngine: () => api.post('/engine/stop'),
  pauseEngine: () => api.post('/engine/pause'),
  resumeEngine: () => api.post('/engine/resume'),
  emergencyStop: () => api.post('/engine/emergency-stop'),

  // Strategy Settings
  getStrategySettings: () => api.get<StrategySettings>('/strategy/settings'),
  updateStrategySettings: (settings: Partial<StrategySettings>) =>
    api.post<StrategySettings>('/strategy/settings', settings),
  resetStrategySettings: () => api.post<StrategySettings>('/strategy/settings/reset'),
  getSettingsHistory: () => api.get('/strategy/settings/history'),
};

export default api;
