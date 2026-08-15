import { useDashboardStore } from '../store/dashboardStore';

export const PositionsPanel = () => {
  const { positions } = useDashboardStore();

  const getSideColor = (side: string) => {
    return side === 'LONG' ? '#22c55e' : '#ef4444';
  };

  const getPnlColor = (pnl: number) => {
    return pnl >= 0 ? '#22c55e' : '#ef4444';
  };

  return (
    <div style={styles.panel}>
      <h2 style={styles.title}>📈 POSITIONS</h2>
      
      <div style={styles.tableContainer}>
        <table style={styles.table}>
          <thead>
            <tr style={styles.trHead}>
              <th style={styles.th}>Exchange</th>
              <th style={styles.th}>Symbol</th>
              <th style={styles.th}>Side</th>
              <th style={styles.th}>Entry</th>
              <th style={styles.th}>Current</th>
              <th style={styles.th}>SL</th>
              <th style={styles.th}>TP1/TP2/TP3</th>
              <th style={styles.th}>Qty</th>
              <th style={styles.th}>PnL</th>
              <th style={styles.th}>R:R</th>
              <th style={styles.th}>BE</th>
              <th style={styles.th}>Opened</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={12} style={styles.emptyCell}>
                  No open positions
                </td>
              </tr>
            ) : (
              positions.map((pos) => (
                <tr key={pos.id} style={styles.tr}>
                  <td style={styles.td}>{pos.exchange}</td>
                  <td style={styles.tdSymbol}>{pos.symbol}</td>
                  <td>
                    <span style={{
                      ...styles.badge,
                      backgroundColor: getSideColor(pos.side),
                    }}>
                      {pos.side}
                    </span>
                  </td>
                  <td style={styles.td}>{pos.entry.toLocaleString()}</td>
                  <td style={styles.td}>{pos.currentPrice.toLocaleString()}</td>
                  <td style={styles.td}>{pos.sl.toLocaleString()}</td>
                  <td style={styles.td}>
                    <div style={styles.tpContainer}>
                      <span style={styles.tpItem}>TP1: {pos.tp1.toLocaleString()}</span>
                      <span style={styles.tpItem}>TP2: {pos.tp2.toLocaleString()}</span>
                      <span style={styles.tpItem}>TP3: {pos.tp3.toLocaleString()}</span>
                    </div>
                  </td>
                  <td style={styles.td}>{pos.quantity.toFixed(4)}</td>
                  <td style={{
                    ...styles.td,
                    color: getPnlColor(pos.pnl),
                    fontWeight: '600',
                  }}>
                    {pos.pnl >= 0 ? '+' : ''}{pos.pnl.toFixed(2)} USDT
                  </td>
                  <td style={styles.td}>{pos.rr.toFixed(2)}R</td>
                  <td style={styles.td}>
                    {pos.breakevenActive ? '✅' : '❌'}
                  </td>
                  <td style={styles.td}>
                    {new Date(pos.openedAt).toLocaleString()}
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
    fontSize: '13px',
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
  tpContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
  },
  tpItem: {
    fontSize: '11px',
    color: '#9ca3af',
  },
};

export default PositionsPanel;
