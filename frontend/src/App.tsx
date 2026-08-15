import { useState } from 'react';
import StatusPanel from './components/StatusPanel';
import MarketsPanel from './components/MarketsPanel';
import PositionsPanel from './components/PositionsPanel';
import SettingsPanel from './components/SettingsPanel';
import { useDashboardData } from './hooks/useDashboardData';
import { useDashboardStore } from './store/dashboardStore';

type Tab = 'dashboard' | 'settings' | 'logs';

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard');
  const { wsConnected } = useDashboardData();
  const { error, clearError } = useDashboardStore();

  return (
    <div style={styles.app}>
      {/* Header */}
      <header style={styles.header}>
        <div style={styles.logo}>
          <span style={styles.logoIcon}>🤖</span>
          <h1 style={styles.logoText}>ATS-SMT PRO</h1>
        </div>
        <div style={styles.wsStatus}>
          <span style={{
            ...styles.wsDot,
            backgroundColor: wsConnected ? '#22c55e' : '#ef4444',
          }} />
          <span style={styles.wsText}>
            {wsConnected ? 'WebSocket Connected' : 'WebSocket Disconnected'}
          </span>
        </div>
      </header>

      {/* Error Banner */}
      {error && (
        <div style={styles.errorBanner}>
          <span>⚠️ {error}</span>
          <button onClick={clearError} style={styles.closeButton}>✕</button>
        </div>
      )}

      {/* Navigation */}
      <nav style={styles.nav}>
        <button
          onClick={() => setActiveTab('dashboard')}
          style={{
            ...styles.navButton,
            ...(activeTab === 'dashboard' ? styles.navButtonActive : {}),
          }}
        >
          📊 Dashboard
        </button>
        <button
          onClick={() => setActiveTab('settings')}
          style={{
            ...styles.navButton,
            ...(activeTab === 'settings' ? styles.navButtonActive : {}),
          }}
        >
          ⚙️ Settings
        </button>
        <button
          onClick={() => setActiveTab('logs')}
          style={{
            ...styles.navButton,
            ...(activeTab === 'logs' ? styles.navButtonActive : {}),
          }}
        >
          📝 Logs
        </button>
      </nav>

      {/* Main Content */}
      <main style={styles.main}>
        {activeTab === 'dashboard' && (
          <>
            <StatusPanel />
            <MarketsPanel />
            <PositionsPanel />
          </>
        )}

        {activeTab === 'settings' && (
          <SettingsPanel />
        )}

        {activeTab === 'logs' && (
          <LogsPanel />
        )}
      </main>
    </div>
  );
}

const LogsPanel = () => {
  const { logs } = useDashboardStore();

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'ERROR':
        return '#ef4444';
      case 'WARNING':
        return '#f59e0b';
      case 'TRADE':
      case 'SIGNAL':
        return '#3b82f6';
      case 'RISK':
        return '#f97316';
      case 'EXCHANGE':
        return '#8b5cf6';
      default:
        return '#22c55e';
    }
  };

  return (
    <div style={styles.panel}>
      <h2 style={styles.title}>📝 LOGS</h2>
      <div style={styles.logsContainer}>
        {logs.length === 0 ? (
          <div style={styles.emptyLogs}>No logs available</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} style={styles.logEntry}>
              <span style={styles.logTime}>
                {new Date(log.timestamp).toLocaleTimeString()}
              </span>
              <span style={{
                ...styles.logLevel,
                color: getLevelColor(log.level),
              }}>
                [{log.level}]
              </span>
              <span style={styles.logMessage}>{log.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

const styles: { [key: string]: React.CSSProperties } = {
  app: {
    minHeight: '100vh',
    backgroundColor: '#111827',
    color: 'white',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '16px 24px',
    backgroundColor: '#1f2937',
    borderBottom: '1px solid #374151',
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  logoIcon: {
    fontSize: '28px',
  },
  logoText: {
    margin: 0,
    fontSize: '20px',
    fontWeight: '700',
  },
  wsStatus: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  wsDot: {
    display: 'inline-block',
    width: '8px',
    height: '8px',
    borderRadius: '50%',
  },
  wsText: {
    fontSize: '12px',
    color: '#9ca3af',
  },
  errorBanner: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 24px',
    backgroundColor: '#fef2f2',
    color: '#991b1b',
    borderBottom: '1px solid #fecaca',
  },
  closeButton: {
    background: 'none',
    border: 'none',
    color: '#991b1b',
    cursor: 'pointer',
    fontSize: '16px',
    padding: '0',
  },
  nav: {
    display: 'flex',
    gap: '8px',
    padding: '16px 24px',
    backgroundColor: '#1f2937',
    borderBottom: '1px solid #374151',
  },
  navButton: {
    padding: '8px 16px',
    backgroundColor: 'transparent',
    color: '#9ca3af',
    border: '1px solid #374151',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  navButtonActive: {
    backgroundColor: '#3b82f6',
    color: 'white',
    borderColor: '#3b82f6',
  },
  main: {
    padding: '24px',
    maxWidth: '1600px',
    margin: '0 auto',
  },
  panel: {
    backgroundColor: '#1f2937',
    borderRadius: '8px',
    padding: '20px',
    marginBottom: '20px',
  },
  title: {
    margin: '0 0 20px 0',
    fontSize: '18px',
    fontWeight: '600',
  },
  logsContainer: {
    maxHeight: '600px',
    overflowY: 'auto',
    backgroundColor: '#111827',
    borderRadius: '6px',
    padding: '12px',
  },
  emptyLogs: {
    textAlign: 'center',
    color: '#6b7280',
    padding: '40px',
  },
  logEntry: {
    display: 'flex',
    gap: '12px',
    padding: '8px 0',
    borderBottom: '1px solid #374151',
    fontSize: '13px',
  },
  logTime: {
    color: '#6b7280',
    minWidth: '80px',
  },
  logLevel: {
    fontWeight: '600',
    minWidth: '80px',
  },
  logMessage: {
    flex: 1,
    color: '#d1d5db',
  },
};

export default App;
