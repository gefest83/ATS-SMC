import { useDashboardStore } from '../store/dashboardStore';

export const MarketsPanel = () => {
  const { markets } = useDashboardStore();

  const getRegimeColor = (regime: string) => {
    switch (regime) {
      case 'TREND':
        return '#22c55e';
      case 'RANGE':
        return '#f59e0b';
      case 'DEAD':
        return '#ef4444';
      default:
        return '#6b7280';
    }
  };

  const getTrendIcon = (trend: number) => {
    if (trend > 0) return '📈';
    if (trend < 0) return '📉';
    return '➡️';
  };

  const getHealthColor = (health: string) => {
    switch (health) {
      case 'HEALTHY':
        return '#22c55e';
      case 'DEGRADED':
        return '#f59e0b';
      case 'STALE':
        return '#f97316';
      case 'OFFLINE':
        return '#ef4444';
      default:
        return '#6b7280';
    }
  };

  return (
    <div style={styles.panel}>
      <h2 style={styles.title}>📊 MARKETS</h2>
      
      <div style={styles.tableContainer}>
        <table style={styles.table}>
          <thead>
            <tr style={styles.trHead}>
              <th style={styles.th}>Symbol</th>
              <th style={styles.th}>Price</th>
              <th style={styles.th}>24h</th>
              <th style={styles.th}>M30</th>
              <th style={styles.th}>4H</th>
              <th style={styles.th}>1D</th>
              <th style={styles.th}>ADX</th>
              <th style={styles.th}>Regime</th>
              <th style={styles.th}>Signal</th>
              <th style={styles.th}>Health</th>
            </tr>
          </thead>
          <tbody>
            {markets.length === 0 ? (
              <tr>
                <td colSpan={10} style={styles.emptyCell}>
                  No market data available
                </td>
              </tr>
            ) : (
              markets.map((market) => (
                <tr key={market.symbol} style={styles.tr}>
                  <td style={styles.tdSymbol}>{market.symbol}</td>
                  <td style={styles.td}>{market.price.toLocaleString()}</td>
                  <td style={{
                    ...styles.td,
                    color: market.change24h >= 0 ? '#22c55e' : '#ef4444',
                  }}>
                    {market.change24h >= 0 ? '+' : ''}{market.change24h.toFixed(2)}%
                  </td>
                  <td style={styles.td}>{getTrendIcon(market.trendM30)}</td>
                  <td style={styles.td}>{getTrendIcon(market.trend4H)}</td>
                  <td style={styles.td}>{getTrendIcon(market.trend1D)}</td>
                  <td style={styles.td}>{market.adx.toFixed(1)}</td>
                  <td>
                    <span style={{
                      ...styles.badge,
                      backgroundColor: getRegimeColor(market.regime),
                    }}>
                      {market.regime}
                    </span>
                  </td>
                  <td style={styles.td}>
                    {market.signal ? (
                      <span style={{
                        ...styles.badge,
                        backgroundColor: market.signal === 'LONG' ? '#22c55e' : '#ef4444',
                      }}>
                        {market.signal}
                      </span>
                    ) : (
                      <span style={{ color: '#6b7280' }}>-</span>
                    )}
                  </td>
                  <td style={styles.td}>
                    <span style={{
                      display: 'inline-block',
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      backgroundColor: getHealthColor(market.health),
                    }} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
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
  title: {
    margin: '0 0 20px 0',
    fontSize: '18px',
    fontWeight: '600',
  },
  tableContainer: {
    overflowX: 'auto',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
  },
  trHead: {
    borderBottom: '2px solid #374151',
  },
  th: {
    textAlign: 'left',
    padding: '12px 8px',
    fontSize: '12px',
    fontWeight: '600',
    color: '#9ca3af',
    textTransform: 'uppercase',
  },
  tr: {
    borderBottom: '1px solid #374151',
  },
  td: {
    padding: '12px 8px',
    fontSize: '14px',
  },
  tdSymbol: {
    padding: '12px 8px',
    fontSize: '14px',
    fontWeight: '600',
  },
  emptyCell: {
    padding: '40px',
    textAlign: 'center',
    color: '#6b7280',
  },
  badge: {
    padding: '2px 8px',
    borderRadius: '4px',
    fontSize: '11px',
    fontWeight: '600',
    color: 'white',
    textTransform: 'uppercase',
  },
};

export default MarketsPanel;
