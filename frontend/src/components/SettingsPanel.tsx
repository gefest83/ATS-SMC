import { useState } from 'react';
import { useDashboardStore } from '../store/dashboardStore';

export const SettingsPanel = () => {
  const { settings, updateSettings, resetSettings, loading } = useDashboardStore();
  const [localSettings, setLocalSettings] = useState(settings);
  const [saved, setSaved] = useState(false);

  if (!settings) {
    return <div style={styles.panel}>Loading settings...</div>;
  }

  const handleChange = (key: keyof typeof settings, value: any) => {
    setLocalSettings((prev: any) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const handleSave = async () => {
    if (localSettings) {
      await updateSettings(localSettings);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    }
  };

  const handleReset = async () => {
    await resetSettings();
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <h2 style={styles.title}>⚙️ STRATEGY SETTINGS</h2>
        {saved && <span style={styles.saved}>✅ Saved!</span>}
      </div>

      <div style={styles.grid}>
        {/* Market Structure */}
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>MARKET STRUCTURE</h3>
          
          <div style={styles.field}>
            <label style={styles.label}>Structure Period</label>
            <input
              type="number"
              value={localSettings?.structurePeriod}
              onChange={(e) => handleChange('structurePeriod', parseInt(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Confirmation Type</label>
            <select
              value={localSettings?.confirmationType}
              onChange={(e) => handleChange('confirmationType', e.target.value)}
              style={styles.select}
            >
              <option value="Body">Body</option>
              <option value="Wick">Wick</option>
            </select>
          </div>

          <div style={styles.field}>
            <label style={styles.label}>HTF 1</label>
            <select
              value={localSettings?.htf1}
              onChange={(e) => handleChange('htf1', e.target.value)}
              style={styles.select}
            >
              <option value="4H">4H</option>
              <option value="1D">1D</option>
            </select>
          </div>

          <div style={styles.field}>
            <label style={styles.label}>HTF 2</label>
            <select
              value={localSettings?.htf2}
              onChange={(e) => handleChange('htf2', e.target.value)}
              style={styles.select}
            >
              <option value="4H">4H</option>
              <option value="1D">1D</option>
            </select>
          </div>
        </div>

        {/* ADX / Market Regime */}
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>ADX / MARKET REGIME</h3>
          
          <div style={styles.field}>
            <label style={styles.label}>ADX Vote Threshold</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.adxTh}
              onChange={(e) => handleChange('adxTh', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>ADX Trend Threshold</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.adxTrend}
              onChange={(e) => handleChange('adxTrend', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>ADX Dead Threshold</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.adxDead}
              onChange={(e) => handleChange('adxDead', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>
        </div>

        {/* Voting */}
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>VOTING</h3>
          
          <div style={styles.field}>
            <label style={styles.label}>Filter Mode</label>
            <select
              value={localSettings?.filterMode}
              onChange={(e) => handleChange('filterMode', e.target.value)}
              style={styles.select}
            >
              <option value="2of3">2 of 3</option>
              <option value="ALL">ALL (3 of 3)</option>
            </select>
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Volume Multiplier</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.volMult}
              onChange={(e) => handleChange('volMult', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>
        </div>

        {/* Filters */}
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>FILTERS</h3>
          
          <div style={styles.field}>
            <label style={styles.label}>Use Impulse Filter</label>
            <input
              type="checkbox"
              checked={localSettings?.useImpulse}
              onChange={(e) => handleChange('useImpulse', e.target.checked)}
              style={styles.checkbox}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Impulse Multiplier</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.impulseMult}
              onChange={(e) => handleChange('impulseMult', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Use Range Bounce</label>
            <input
              type="checkbox"
              checked={localSettings?.useRangeBounce}
              onChange={(e) => handleChange('useRangeBounce', e.target.checked)}
              style={styles.checkbox}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>BB Lookback</label>
            <input
              type="number"
              value={localSettings?.bbLookback}
              onChange={(e) => handleChange('bbLookback', parseInt(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Maximum Bounces</label>
            <input
              type="number"
              value={localSettings?.maxBounces}
              onChange={(e) => handleChange('maxBounces', parseInt(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Minimum ATR %</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.minAtrPct}
              onChange={(e) => handleChange('minAtrPct', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Max BOS Distance ATR</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.maxBosDistAtr}
              onChange={(e) => handleChange('maxBosDistAtr', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Use Cooldown</label>
            <input
              type="checkbox"
              checked={localSettings?.useCooldown}
              onChange={(e) => handleChange('useCooldown', e.target.checked)}
              style={styles.checkbox}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Cooldown Bars</label>
            <input
              type="number"
              value={localSettings?.cooldownBars}
              onChange={(e) => handleChange('cooldownBars', parseInt(e.target.value))}
              style={styles.input}
            />
          </div>
        </div>

        {/* Risk */}
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>RISK</h3>
          
          <div style={styles.field}>
            <label style={styles.label}>Risk Per Trade %</label>
            <input
              type="number"
              step="0.1"
              value={localSettings?.riskPct}
              onChange={(e) => handleChange('riskPct', parseFloat(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>TP1 Close %</label>
            <input
              type="number"
              value={localSettings?.tp1Pct}
              onChange={(e) => handleChange('tp1Pct', parseInt(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>TP2 Close %</label>
            <input
              type="number"
              value={localSettings?.tp2Pct}
              onChange={(e) => handleChange('tp2Pct', parseInt(e.target.value))}
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>TP3 Close %</label>
            <input
              type="number"
              value={localSettings?.tp3Pct}
              onChange={(e) => handleChange('tp3Pct', parseInt(e.target.value))}
              style={styles.input}
            />
          </div>
        </div>

        {/* Trade Management */}
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>TRADE MANAGEMENT</h3>
          
          <div style={styles.field}>
            <label style={styles.label}>Use Breakeven</label>
            <input
              type="checkbox"
              checked={localSettings?.useBreakeven}
              onChange={(e) => handleChange('useBreakeven', e.target.checked)}
              style={styles.checkbox}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Use Trailing Stop</label>
            <input
              type="checkbox"
              checked={localSettings?.useTrail}
              onChange={(e) => handleChange('useTrail', e.target.checked)}
              style={styles.checkbox}
            />
          </div>
        </div>
      </div>

      <div style={styles.buttons}>
        <button
          onClick={handleSave}
          disabled={loading}
          style={{
            ...styles.button,
            backgroundColor: loading ? '#4b5563' : '#22c55e',
          }}
        >
          {loading ? 'Saving...' : '💾 SAVE SETTINGS'}
        </button>
        <button
          onClick={handleReset}
          disabled={loading}
          style={{
            ...styles.button,
            backgroundColor: '#6b7280',
          }}
        >
          🔄 RESET TO DEFAULTS
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
  saved: {
    color: '#22c55e',
    fontSize: '14px',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
    gap: '20px',
  },
  section: {
    backgroundColor: '#374151',
    padding: '16px',
    borderRadius: '6px',
  },
  sectionTitle: {
    margin: '0 0 16px 0',
    fontSize: '14px',
    fontWeight: '600',
    color: '#9ca3af',
    textTransform: 'uppercase',
  },
  field: {
    marginBottom: '12px',
  },
  label: {
    display: 'block',
    fontSize: '12px',
    color: '#d1d5db',
    marginBottom: '4px',
  },
  input: {
    width: '100%',
    padding: '8px 12px',
    backgroundColor: '#1f2937',
    border: '1px solid #4b5563',
    borderRadius: '4px',
    color: 'white',
    fontSize: '14px',
  },
  select: {
    width: '100%',
    padding: '8px 12px',
    backgroundColor: '#1f2937',
    border: '1px solid #4b5563',
    borderRadius: '4px',
    color: 'white',
    fontSize: '14px',
  },
  checkbox: {
    width: '18px',
    height: '18px',
  },
  buttons: {
    display: 'flex',
    gap: '10px',
    marginTop: '20px',
  },
  button: {
    border: 'none',
    padding: '12px 24px',
    borderRadius: '6px',
    color: 'white',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'opacity 0.2s',
  },
};

export default SettingsPanel;
