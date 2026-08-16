/**
 * PriceChart Component for ATS-SMT PRO Dashboard
 * Displays candlestick chart with BOS, CHoCH, and trade levels using Recharts
 */

import React, { useMemo } from 'react';
import {
  ComposedChart,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine
} from 'recharts';

interface PriceChartProps {
  symbol: string;
  timeframe?: string;
  height?: number;
}

interface CandleData {
  timestamp: number;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export const PriceChart: React.FC<PriceChartProps> = ({
  symbol,
  timeframe = '30m',
  height = 400
}) => {
  // In production, prices would come from WebSocket or API
  // For now we generate mock data locally
  
  // Generate mock candle data for demonstration
  const candleData: CandleData[] = useMemo(() => {
    const data: CandleData[] = [];
    const basePrice = symbol.includes('BTC') ? 60000 : 
                      symbol.includes('ETH') ? 3000 : 
                      symbol.includes('SOL') ? 150 : 100;
    
    const now = Date.now();
    const intervalMs = timeframe === '30m' ? 30 * 60 * 1000 : 
                       timeframe === '4h' ? 4 * 60 * 60 * 1000 : 
                       24 * 60 * 60 * 1000;
    
    for (let i = 50; i >= 0; i--) {
      const ts = now - (i * intervalMs);
      const volatility = basePrice * 0.02;
      const open = basePrice + (Math.random() - 0.5) * volatility;
      const close = basePrice + (Math.random() - 0.5) * volatility;
      const high = Math.max(open, close) + Math.random() * volatility * 0.5;
      const low = Math.min(open, close) - Math.random() * volatility * 0.5;
      
      data.push({
        timestamp: ts,
        time: new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        open: parseFloat(open.toFixed(2)),
        high: parseFloat(high.toFixed(2)),
        low: parseFloat(low.toFixed(2)),
        close: parseFloat(close.toFixed(2)),
        volume: Math.floor(Math.random() * 1000) + 100
      });
    }
    
    return data;
  }, [symbol, timeframe]);

  // Mock entry, SL, TP levels (would come from position data in production)
  const mockPosition = {
    entry: 59500,
    sl: 58500,
    tp1: 61000,
    tp2: 62000,
    tp3: 63500
  };

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold text-white">
          {symbol} - {timeframe}
        </h3>
        <div className="flex gap-2">
          <span className="text-xs px-2 py-1 bg-blue-900 text-blue-300 rounded">
            M30
          </span>
          <span className="text-xs px-2 py-1 bg-purple-900 text-purple-300 rounded">
            4H: UP
          </span>
          <span className="text-xs px-2 py-1 bg-indigo-900 text-indigo-300 rounded">
            1D: UP
          </span>
        </div>
      </div>
      
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={candleData}>
          <XAxis 
            dataKey="time" 
            stroke="#6B7280"
            tick={{ fontSize: 12 }}
          />
          <YAxis 
            domain={['auto', 'auto']}
            stroke="#6B7280"
            tick={{ fontSize: 12 }}
            orientation="right"
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1F2937',
              border: '1px solid #374151',
              borderRadius: '8px'
            }}
            labelStyle={{ color: '#9CA3AF' }}
            formatter={(value: any, name: string) => [
              typeof value === 'number' ? value.toFixed(2) : value,
              name
            ]}
          />
          
          {/* Entry Level */}
          <ReferenceLine
            y={mockPosition.entry}
            stroke="#3B82F6"
            strokeWidth={2}
            strokeDasharray="3 3"
            label={{ 
              value: `Entry: ${mockPosition.entry}`, 
              position: 'right',
              fill: '#3B82F6',
              fontSize: 12
            }}
          />
          
          {/* Stop Loss */}
          <ReferenceLine
            y={mockPosition.sl}
            stroke="#EF4444"
            strokeWidth={2}
            label={{ 
              value: `SL: ${mockPosition.sl}`, 
              position: 'right',
              fill: '#EF4444',
              fontSize: 12
            }}
          />
          
          {/* Take Profit Levels */}
          <ReferenceLine
            y={mockPosition.tp1}
            stroke="#10B981"
            strokeWidth={1}
            strokeDasharray="3 3"
            label={{ 
              value: `TP1: ${mockPosition.tp1}`, 
              position: 'right',
              fill: '#10B981',
              fontSize: 10
            }}
          />
          <ReferenceLine
            y={mockPosition.tp2}
            stroke="#10B981"
            strokeWidth={1}
            strokeDasharray="3 3"
            label={{ 
              value: `TP2: ${mockPosition.tp2}`, 
              position: 'right',
              fill: '#10B981',
              fontSize: 10
            }}
          />
          <ReferenceLine
            y={mockPosition.tp3}
            stroke="#10B981"
            strokeWidth={1}
            strokeDasharray="3 3"
            label={{ 
              value: `TP3: ${mockPosition.tp3}`, 
              position: 'right',
              fill: '#10B981',
              fontSize: 10
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
      
      {/* Chart Legend */}
      <div className="mt-4 flex flex-wrap gap-4 text-xs">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 bg-blue-500 rounded"></div>
          <span className="text-gray-400">Entry</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 bg-red-500 rounded"></div>
          <span className="text-gray-400">Stop Loss</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 bg-green-500 rounded"></div>
          <span className="text-gray-400">Take Profit</span>
        </div>
      </div>
    </div>
  );
};

export default PriceChart;
