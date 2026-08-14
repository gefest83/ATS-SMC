"""Position sizing and durable live equity/drawdown protection."""
from __future__ import annotations

import logging
from datetime import date, timezone, datetime
from decimal import Decimal
from typing import Optional
import json

from sqlalchemy import select

from backend.config import settings
from backend.db.models import RuntimeState

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, initial_equity: float, db_session_factory=None, state_scope: str = "default"):
        self.initial_equity = float(initial_equity)
        self.current_equity = float(initial_equity)
        self.daily_start_equity = float(initial_equity)
        self.daily_loss = 0.0
        self.open_trades = 0
        # Position close notifications can be delivered by both the order
        # request path and the authoritative PositionManager callback.  Keep
        # the event key so the risk counter and realized PnL are applied once.
        self._closed_event_ids: set[str] = set()
        self._equity_day = self._today_utc()
        self.db_session_factory = db_session_factory
        self.state_scope = state_scope

    @staticmethod
    def _today_utc() -> date:
        return datetime.now(timezone.utc).date()

    async def restore_state(self) -> Optional[str]:
        """Restore equity/drawdown state before trading after a process restart."""
        if not self.db_session_factory:
            return None
        try:
            async with self.db_session_factory() as session:
                row = await session.get(RuntimeState, self.state_scope)
            if row is None:
                return None
            today = self._today_utc().isoformat()
            if row.equity_day != today:
                self.current_equity = float(row.current_equity)
                self.daily_start_equity = self.current_equity
                self.daily_loss = 0.0
                self.open_trades = max(0, int(row.open_trades or 0))
            else:
                self.current_equity = max(0.0, float(row.current_equity))
                self.daily_start_equity = max(0.0, float(row.daily_start_equity))
                self.daily_loss = max(0.0, float(row.daily_loss))
                self.open_trades = max(0, int(row.open_trades or 0))
            return row.paper_position_json
        except Exception:
            logger.exception("Risk/runtime state restore failed")
            return None

    async def persist_state(self, paper_position_json: Optional[str] = None) -> bool:
        """Persist risk state atomically enough that a restart cannot reset daily risk."""
        if not self.db_session_factory:
            return True
        self._roll_daily_state()
        try:
            async with self.db_session_factory() as session:
                row = await session.get(RuntimeState, self.state_scope)
                values = dict(
                    current_equity=Decimal(str(self.current_equity)),
                    daily_start_equity=Decimal(str(self.daily_start_equity)),
                    daily_loss=Decimal(str(self.daily_loss)),
                    open_trades=max(0, int(self.open_trades)),
                    equity_day=self._equity_day.isoformat(),
                )
                if paper_position_json is not None:
                    values["paper_position_json"] = paper_position_json
                if row is None:
                    session.add(RuntimeState(scope=self.state_scope, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                await session.commit()
            return True
        except Exception:
            logger.exception("Risk/runtime state persistence failed")
            return False

    def _roll_daily_state(self) -> None:
        today = self._today_utc()
        if today != self._equity_day:
            self._equity_day = today
            self.daily_start_equity = self.current_equity
            self.daily_loss = 0.0

    def update_equity(self, equity: float) -> None:
        self._roll_daily_state()
        self.current_equity = max(0.0, float(equity))
        self.daily_loss = max(0.0, self.daily_start_equity - self.current_equity)

    def apply_pnl(self, pnl: float) -> None:
        self.update_equity(self.current_equity + float(pnl))

    def reset_daily(self) -> None:
        self.daily_start_equity = self.current_equity
        self.daily_loss = 0.0

    def calculate_position_size(self, entry_price: float, stop_loss: float, *, leverage: float = 1.0, max_notional_pct: float = 1.0) -> float:
        entry = float(entry_price)
        stop = float(stop_loss)
        equity = max(0.0, float(self.current_equity))
        lev = float(leverage)
        notional_pct = float(max_notional_pct)
        if entry <= 0 or stop <= 0 or equity <= 0 or lev <= 0 or notional_pct <= 0:
            return 0.0
        risk_amount = equity * float(settings.RISK_PER_TRADE_PCT)
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0 or risk_amount <= 0:
            return 0.0
        risk_size = risk_amount / risk_per_unit
        max_notional = equity * notional_pct * lev
        margin_capped_size = max_notional / entry
        return max(0.0, min(risk_size, margin_capped_size))

    def check_drawdown(self, current_equity: Optional[float] = None) -> bool:
        self._roll_daily_state()
        if current_equity is not None:
            self.update_equity(current_equity)
        if self.daily_start_equity <= 0:
            return True
        drawdown = (self.daily_start_equity - self.current_equity) / self.daily_start_equity
        breached = drawdown >= settings.MAX_DAILY_DRAWDOWN_PCT
        if breached:
            logger.critical("MAX DAILY DRAWDOWN REACHED: %.2f%%", drawdown * 100)
        return breached

    def can_open_trade(self) -> bool:
        if self.open_trades >= settings.MAX_OPEN_TRADES:
            logger.warning(
                "Max open trades reached (%s) open_trades=%d scope=%s",
                settings.MAX_OPEN_TRADES, self.open_trades, self.state_scope,
            )
            return False
        return not self.check_drawdown()

    def trade_opened(self) -> None:
        self.open_trades += 1
        logger.info(
            "trade_opened: open_trades=%d scope=%s",
            self.open_trades, self.state_scope,
        )

    def trade_closed(self, pnl: float = 0.0, event_id: Optional[str] = None) -> bool:
        """Apply one confirmed close event exactly once.

        Returns ``True`` only when this call actually consumed a close event.
        The return value lets account-wide coordination (such as the shared
        multi-symbol trade gate) release exactly once as well.

        ``event_id`` should be the stable position id when a caller can
        provide it.  The optional argument preserves compatibility with
        callers that only maintain a scalar portfolio counter.
        """
        if event_id is not None:
            key = str(event_id)
            if key in self._closed_event_ids:
                logger.debug(
                    "trade_closed: duplicate event_id=%s scope=%s, skipping",
                    event_id, self.state_scope,
                )
                return False
            self._closed_event_ids.add(key)
        self.open_trades = max(0, self.open_trades - 1)
        self.apply_pnl(pnl)
        logger.info(
            "trade_closed: open_trades=%d scope=%s event_id=%s pnl=%.4f",
            self.open_trades, self.state_scope, event_id, pnl,
        )
        return True
