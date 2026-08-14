"""
Portfolio Correlation Manager – computes correlation matrix for tracked symbols
and blocks trades that exceed a correlation threshold.
"""
import logging
from decimal import Decimal
from typing import Dict, List

import numpy as np
import pandas as pd

from backend.core.exchange.base import MarketData

logger = logging.getLogger(__name__)


class CorrelationManager:
    """
    Maintains a rolling window of price history per symbol and computes Pearson correlation.
    If correlation between two candidate symbols exceeds the threshold, the trade is rejected.
    """

    def __init__(self, window_size: int = 100, threshold: float = 0.95):
        self.window_size = window_size
        self.threshold = threshold
        self.price_history: Dict[str, List[Decimal]] = {}
        self.correlation_matrix: Dict[str, Dict[str, float]] = {}
        self._dirty = True

    def update_price(self, symbol: str, price: Decimal):
        """
        Record a new price point for the symbol.
        """
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        self.price_history[symbol].append(price)
        if len(self.price_history[symbol]) > self.window_size:
            self.price_history[symbol].pop(0)
        self._dirty = True

    def _recompute_correlation(self):
        """
        Re‑compute Pearson correlation matrix based on current price histories.
        """
        symbols = list(self.price_history.keys())
        if len(symbols) < 2:
            self.correlation_matrix = {}
            return

        # Align series to same length for reliable pairwise correlation
        min_len = min(len(self.price_history[s]) for s in symbols)
        df = pd.DataFrame({s: list(map(float, self.price_history[s][-min_len:])) for s in symbols})
        corr = df.corr()
        self.correlation_matrix = corr.round(4).to_dict()
        self._dirty = False

    def is_trade_allowed(self, symbol: str, existing_positions: Dict[str, Decimal]) -> bool:
        """
        Determine whether opening a position in `symbol` is allowed given current
        open positions. Returns False if any existing position symbol has correlation
        above the threshold with `symbol`.
        """
        if self._dirty:
            self._recompute_correlation()
        for pos_symbol in existing_positions.keys():
            corr = self.correlation_matrix.get(symbol, {}).get(pos_symbol, 0)
            if abs(corr) >= self.threshold:
                logger.warning(
                    "Trade blocked for %s due to high correlation (%.2f) with %s",
                    symbol, corr, pos_symbol,
                )
                return False
        return True