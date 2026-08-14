"""
Liquidity Scanner & Screener – evaluates multiple assets for potential setups
(FVG, Order Blocks, BOS, CHOCH, imbalances) and assigns a SignalScore (0‑100).
"""
import logging
from collections import deque
from decimal import Decimal
from typing import List, Dict

from backend.core.exchange.base import MarketData

logger = logging.getLogger(__name__)


class LiquidityScanner:
    """
    Scans a list of symbols and returns those that meet the scanner criteria.
    The scoring algorithm is simplified for demonstration.
    """

    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.latest_prices: Dict[str, Decimal] = {}
        self.history: Dict[str, deque[Decimal]] = {symbol: deque(maxlen=20) for symbol in self.symbols}

    def update_price(self, market_data: MarketData):
        """
        Store the latest price for a symbol.
        """
        self.latest_prices[market_data.symbol] = market_data.price
        self.history.setdefault(market_data.symbol, deque(maxlen=20)).append(market_data.price)

    def scan(self) -> List[Dict]:
        """Return deterministic momentum candidates; never fabricate SMC reasons."""
        candidates = []
        for symbol, price in self.latest_prices.items():
            history = self.history.get(symbol, ())
            if len(history) < 5:
                continue
            baseline = history[0]
            if baseline <= 0:
                continue
            momentum = (price - baseline) / baseline
            score = min(100, int(abs(momentum) * 1000))
            if score < 20:
                continue
            side = "buy" if momentum > 0 else "sell"
            candidates.append({
                "symbol": symbol,
                "score": score,
                "side": side,
                "price": price,
                "reason": f"price momentum over {len(history)} observations",
            })
        logger.info("Scanner found %s candidates", len(candidates))
        return candidates
