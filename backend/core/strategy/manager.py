"""Strategy orchestration and centralized risk/order routing."""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Dict, Optional

from backend.config import settings
from backend.core.exchange.base import MarketData
from backend.core.risk.risk_manager import RiskManager
from backend.core.strategy.base import StrategyRegistry

logger = logging.getLogger(__name__)


class StrategyManager:
    def __init__(self, registry: StrategyRegistry, order_manager, risk_manager: Optional[RiskManager] = None):
        self.registry = registry
        self.order_manager = order_manager
        self.risk_manager = risk_manager or RiskManager(settings.INITIAL_EQUITY)
        self.running = False
        self.task = None

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def feed_market_data(self, market_data: MarketData):
        for strategy in self.registry.strategies.values():
            try:
                signal = strategy.on_market_data(market_data)
                if signal:
                    strategy.log_signal(signal)
                    await self._handle_signal(signal)
            except Exception:
                logger.exception("Error processing strategy %s", strategy.name)

    async def _handle_signal(self, signal: Dict):
        action = signal.get("action")
        if action == "open":
            sl = signal.get("sl_price")
            entry = signal.get("price") or signal.get("entry_price")
            if sl is None or entry is None:
                logger.warning("Ignoring open signal without entry/SL: %s", signal)
                return
            if not self.risk_manager.can_open_trade():
                return
            quantity = signal.get("quantity")
            if quantity is None:
                quantity = self.risk_manager.calculate_position_size(float(entry), float(sl))
            quantity = Decimal(str(quantity))
            if quantity <= 0:
                return
            try:
                position_id = await self.order_manager.open_position(
                    symbol=signal["symbol"], side=signal["side"], quantity=quantity,
                    order_type=signal.get("order_type", "market"), price=entry,
                    sl_price=sl, tp_prices=signal.get("tp_prices", []),
                    strategy_name=signal.get("strategy", "unknown"),
                )
            except Exception:
                logger.exception("Failed to open strategy position")
                return
            self.risk_manager.trade_opened()
            logger.info("Opened position %s", position_id)
        elif action == "close":
            position_id = signal.get("position_id")
            if not position_id:
                logger.warning("Close signal missing position_id")
                return
            result = await self.order_manager.close_position(
                position_id,
                close_type=signal.get("close_type", "full"),
                percentage=Decimal(str(signal.get("percentage", 1))),
                reason=signal.get("reason", "strategy"),
            )
            # A close request is not a close event.  Only the fill-aware
            # result after atomic Position/Trade persistence may change risk.
            if result.fully_closed:
                self.risk_manager.trade_closed(
                    float(result.realized_pnl), event_id=position_id
                )
        else:
            logger.warning("Unknown signal action: %s", action)
