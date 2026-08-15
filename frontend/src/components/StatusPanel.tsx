import { useDashboardStore } from '../store/dashboardStore';

export const StatusPanel = () => {
  const { engineStatus, startEngine, stopEngine, pauseEngine, resumeEngine, emergencyStop } =
    useDashboardStore();

  if (!engineStatus) {
    return <div className="status-panel">Loading...</div>;
  }

  const getStatusColor = (health: string) => {
    switch (health) {
      case 'HEALTHY':
        return '#22c55e';
      case 'DEGRADED':
        return '#f59e0b';
      case 'ERROR':
        return '#ef4444';
      default:
        return '#6b7280';
    }
  };

  const getModeBadge = (mode: string) => {
    switch (mode) {
      case 'LIVE':
        return { bg: '#ef4444', text: 'LIVE' };
      case 'TESTNET':
        return { bg: '#f59e0b', text: 'TESTNET' };
      case 'PAPER':
        return { bg: '#22c55e', text: 'PAPER' };
      default:
        return { bg: '#6b7280', text: mode };
    }
  };

  const modeBadge = getModeBadge(engineStatus.mode);

  return (
    <div className="status-panel" style={styles.panel}>
      <div style={styles.header}>
        <h2 style={styles.title}>🖥️ SYSTEM</h2>
        <div
          style={{
            ...styles.badge,
            backgroundColor: getStatusColor(engineStatus.health),
          }}
        >
          {engineStatus.health}
        </div>
      </div>

      <div style={styles.grid}>
        <div style={styles.stat}>
          <div style={styles.statLabel}>Engine</div>
          <div style={styles.statValue}>
            {engineStatus.running ? '🟢 RUNNING' : '🔴 STOPPED'}
          </div>
        </div>

        <div style={styles.stat}>
          <div style={styles.statLabel}>Mode</div>
          <div
            style={{
              ...styles.statValue,
              backgroundColor: modeBadge.bg,
              padding: '2px 8px',
              borderRadius: '4px',
              display: 'inline-block',
            }}
          >
            {modeBadge.text}
          </div>
        </div>

        <div style={styles.stat}>
          <div style={styles.statLabel}>Uptime</div>
          <div style={styles.statValue}>{Math.floor(engineStatus.uptime / 3600)}h {Math.floor((engineStatus.uptime % 3600) / 60)}m</div>
        </div>

        <div style={styles.stat}>
          <div style={styles.statLabel}>Last Update</div>
          <div style={styles.statValue}>
            {new Date(engineStatus.lastUpdate).toLocaleTimeString()}
          </div>
        </div>
      </div>

      <div style={styles.controls}>
        <button
          onClick={startEngine}
          disabled={engineStatus.running}
          style={{
            ...styles.button,
            backgroundColor: engineStatus.running ? '#4b5563' : '#22c55e',
          }}
        >
          ▶️ START
        </button>
        <button
          onClick={stopEngine}
          disabled={!engineStatus.running}
          style={{
            ...styles.button,
            backgroundColor: !engineStatus.running ? '#4b5563' : '#ef4444',
          }}
        >
          ⏹️ STOP
        </button>
        <button
          onClick={pauseEngine}
          disabled={!engineStatus.running}
          style={{
            ...styles.button,
            backgroundColor: !engineStatus.running ? '#4b5563' : '#f59e0b',
          }}
        >
          ⏸️ PAUSE
        </button>
        <button
          onClick={resumeEngine}
          style={{
            ...styles.button,
            backgroundColor: '#3b82f6',
          }}
        >
          ▶️ RESUME
        </button>
        <button
          onClick={emergencyStop}
          style={{
            ...styles.button,
            backgroundColor: '#dc2626',
            fontWeight: 'bold',
          }}
        >
          🚨 EMERGENCY STOP
        </button>
      </div>
    </div>
  );
};

const styles: { [key: string]: React.CSSProperties } = {
  panel: {
    backgroundColor: '#1f2937',
    borderRadius: '8px',
    padding: '20px',
    marginBottom: '20px',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '20px',
  },
  title: {
    margin: 0,
    fontSize: '18px',
    fontWeight: '600',
  },
  badge: {
    padding: '4px 12px',
    borderRadius: '12px',
    fontSize: '12px',
    fontWeight: '600',
    color: 'white',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
    gap: '15px',
    marginBottom: '20px',
  },
  stat: {
    backgroundColor: '#374151',
    padding: '12px',
    borderRadius: '6px',
  },
  statLabel: {
    fontSize: '12px',
    color: '#9ca3af',
    marginBottom: '4px',
  },
  statValue: {
    fontSize: '16px',
    fontWeight: '600',
  },
  controls: {
    display: 'flex',
    gap: '10px',
    flexWrap: 'wrap',
  },
  button: {
    border: 'none',
    padding: '10px 16px',
    borderRadius: '6px',
    color: 'white',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'opacity 0.2s',
  },
};

export default StatusPanel;
