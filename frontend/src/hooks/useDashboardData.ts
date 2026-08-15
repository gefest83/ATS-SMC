import { useEffect } from 'react';
import { useDashboardStore } from '../store/dashboardStore';
import { useWebSocket } from '../hooks/useWebSocket';

export const useDashboardData = () => {
  const {
    fetchEngineStatus,
    fetchMarkets,
    fetchPositions,
    fetchSignals,
    fetchOrders,
    fetchRisk,
    fetchSettings,
  } = useDashboardStore();

  // WebSocket for realtime updates
  const { connected: wsConnected } = useWebSocket(
    `ws://${window.location.hostname}:8000/ws`
  );

  // Initial data fetch
  useEffect(() => {
    const loadData = async () => {
      await Promise.all([
        fetchEngineStatus(),
        fetchMarkets(),
        fetchPositions(),
        fetchSignals(),
        fetchOrders(),
        fetchRisk(),
        fetchSettings(),
      ]);
    };

    loadData();

    // Polling interval (30 seconds)
    const interval = setInterval(async () => {
      await Promise.all([
        fetchEngineStatus(),
        fetchMarkets(),
        fetchPositions(),
        fetchRisk(),
      ]);
    }, 30000);

    return () => clearInterval(interval);
  }, [
    fetchEngineStatus,
    fetchMarkets,
    fetchPositions,
    fetchSignals,
    fetchOrders,
    fetchRisk,
    fetchSettings,
  ]);

  return { wsConnected };
};
