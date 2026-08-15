import { create } from 'zustand';
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
import { dashboardService } from '../services/api';

interface DashboardState {
  // Data
  engineStatus: EngineStatus | null;
  markets: MarketData[];
  positions: Position[];
  signals: Signal[];
  orders: Order[];
  risk: RiskMetrics | null;
  logs: LogEntry[];
  settings: StrategySettings | null;

  // Loading states
  loading: boolean;
  error: string | null;
  lastUpdate: Date | null;

  // Actions
  fetchEngineStatus: () => Promise<void>;
  fetchMarkets: () => Promise<void>;
  fetchPositions: () => Promise<void>;
  fetchSignals: () => Promise<void>;
  fetchOrders: () => Promise<void>;
  fetchRisk: () => Promise<void>;
  fetchLogs: (level?: string) => Promise<void>;
  fetchSettings: () => Promise<void>;
  updateSettings: (settings: Partial<StrategySettings>) => Promise<void>;
  resetSettings: () => Promise<void>;
  startEngine: () => Promise<void>;
  stopEngine: () => Promise<void>;
  pauseEngine: () => Promise<void>;
  resumeEngine: () => Promise<void>;
  emergencyStop: () => Promise<void>;
  addLog: (log: LogEntry) => void;
  clearError: () => void;
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  // Initial state
  engineStatus: null,
  markets: [],
  positions: [],
  signals: [],
  orders: [],
  risk: null,
  logs: [],
  settings: null,
  loading: false,
  error: null,
  lastUpdate: null,

  // Fetch actions
  fetchEngineStatus: async () => {
    try {
      const response = await dashboardService.getStatus();
      set({ engineStatus: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch engine status', lastUpdate: new Date() });
    }
  },

  fetchMarkets: async () => {
    try {
      const response = await dashboardService.getMarkets();
      set({ markets: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch markets', lastUpdate: new Date() });
    }
  },

  fetchPositions: async () => {
    try {
      const response = await dashboardService.getPositions();
      set({ positions: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch positions', lastUpdate: new Date() });
    }
  },

  fetchSignals: async () => {
    try {
      const response = await dashboardService.getSignals();
      set({ signals: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch signals', lastUpdate: new Date() });
    }
  },

  fetchOrders: async () => {
    try {
      const response = await dashboardService.getOrders();
      set({ orders: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch orders', lastUpdate: new Date() });
    }
  },

  fetchRisk: async () => {
    try {
      const response = await dashboardService.getRisk();
      set({ risk: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch risk metrics', lastUpdate: new Date() });
    }
  },

  fetchLogs: async (level?: string) => {
    try {
      const response = await dashboardService.getLogs(level);
      set({ logs: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch logs', lastUpdate: new Date() });
    }
  },

  fetchSettings: async () => {
    try {
      const response = await dashboardService.getStrategySettings();
      set({ settings: response.data, lastUpdate: new Date() });
    } catch (error) {
      set({ error: 'Failed to fetch settings', lastUpdate: new Date() });
    }
  },

  updateSettings: async (settings: Partial<StrategySettings>) => {
    try {
      set({ loading: true });
      const response = await dashboardService.updateStrategySettings(settings);
      set({ settings: response.data, loading: false, error: null });
    } catch (error) {
      set({ 
        error: 'Failed to update settings', 
        loading: false 
      });
    }
  },

  resetSettings: async () => {
    try {
      const response = await dashboardService.resetStrategySettings();
      set({ settings: response.data, error: null });
    } catch (error) {
      set({ error: 'Failed to reset settings' });
    }
  },

  // Engine control
  startEngine: async () => {
    try {
      await dashboardService.startEngine();
      await get().fetchEngineStatus();
    } catch (error) {
      set({ error: 'Failed to start engine' });
    }
  },

  stopEngine: async () => {
    try {
      await dashboardService.stopEngine();
      await get().fetchEngineStatus();
    } catch (error) {
      set({ error: 'Failed to stop engine' });
    }
  },

  pauseEngine: async () => {
    try {
      await dashboardService.pauseEngine();
      await get().fetchEngineStatus();
    } catch (error) {
      set({ error: 'Failed to pause engine' });
    }
  },

  resumeEngine: async () => {
    try {
      await dashboardService.resumeEngine();
      await get().fetchEngineStatus();
    } catch (error) {
      set({ error: 'Failed to resume engine' });
    }
  },

  emergencyStop: async () => {
    try {
      await dashboardService.emergencyStop();
      await get().fetchEngineStatus();
    } catch (error) {
      set({ error: 'Failed to execute emergency stop' });
    }
  },

  addLog: (log: LogEntry) => {
    set((state) => ({ logs: [log, ...state.logs].slice(0, 100) }));
  },

  clearError: () => {
    set({ error: null });
  },
}));
