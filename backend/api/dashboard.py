"""Read-only web dashboard for monitoring the SMC trading engine."""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_dashboard_html: Optional[str] = None

# Activity log buffer (in-memory, last 100 events)
_activity_log: deque = deque(maxlen=100)
_prev_signals: dict = {}
_prev_positions: dict = {}


def _sanitize_signal(sig: Optional[dict]) -> Optional[dict]:
    """Convert Decimal values in last_signal to float for JSON serialization."""
    if sig is None:
        return None
    out = {}
    for k, v in sig.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _get_html() -> str:
    global _dashboard_html
    if _dashboard_html is None:
        html_path = _TEMPLATE_DIR / "dashboard.html"
        _dashboard_html = html_path.read_text(encoding="utf-8")
    return _dashboard_html


# UTC+3 offset for user-facing timestamps
_UTC3_OFFSET_SECONDS = 3 * 3600


def _now_utc3_str() -> str:
    """Return current time as HH:MM:SS in UTC+3."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc) + timedelta(seconds=_UTC3_OFFSET_SECONDS)
    return now.strftime("%H:%M:%S")


def _record_event(symbol: str, event_type: str, details: str = "") -> None:
    """Record an activity event for the dashboard log."""
    ts = _now_utc3_str()
    _activity_log.appendleft({"time": ts, "symbol": symbol, "event": event_type, "details": details})


def _check_state_changes(symbol: str, signal: Optional[dict], has_position: bool) -> None:
    """Detect signal/position changes and record events."""
    prev_sig = _prev_signals.get(symbol)
    prev_pos = _prev_positions.get(symbol)

    if signal and signal != prev_sig:
        side = signal.get("side", "NONE") if isinstance(signal, dict) else "NONE"
        _record_event(symbol, "SIGNAL", f"side={side}")
    _prev_signals[symbol] = signal

    if has_position != prev_pos:
        if has_position:
            _record_event(symbol, "POSITION_OPEN", "")
        else:
            _record_event(symbol, "POSITION_CLOSE", "")
    _prev_positions[symbol] = has_position


@router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the read-only monitoring dashboard."""
    return HTMLResponse(content=_get_html())


@router.get("/dashboard/ohlcv")
async def get_dashboard_ohlcv(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    limit: int = 200,
):
    """Return OHLCV candle data for the dashboard chart."""
    symbol = symbol or settings.SYMBOL
    timeframe = timeframe or settings.TIMEFRAME
    limit = max(1, min(limit, 1000))

    from backend.api.endpoints import _state

    multi_engine = _state.get("multi_engine")
    bot = None
    if multi_engine is not None and multi_engine.running:
        bot = multi_engine.get_bot(symbol)
    if bot is None:
        bot = _state.get("bot")
    if bot is None:
        return {"symbol": symbol, "timeframe": timeframe, "candles": []}

    try:
        ohlcv = await _fetch_candles(bot, symbol, timeframe, limit)
    except Exception as exc:
        logger.warning("Dashboard OHLCV fetch failed: %s", exc)
        return {"symbol": symbol, "timeframe": timeframe, "candles": []}

    candles = []
    for row in ohlcv:
        if len(row) < 6:
            continue
        ts_ms = int(row[0])
        candles.append({
            "time": ts_ms // 1000,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })

    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}


@router.get("/dashboard/symbols")
async def get_dashboard_symbols():
    """Get status overview for all configured symbols."""
    from backend.api.endpoints import _state
    from backend.core.analysis.market_analyzer import MarketAnalyzer
    from backend.core.analysis.signal_generator import SignalGenerator

    multi_engine = _state.get("multi_engine")
    bot = _state.get("bot")

    symbols_data = []

    if multi_engine is not None and multi_engine.running:
        # Fetch candles for all symbols in parallel to avoid sequential blocking
        async def _fetch_symbol_data(symbol):
            symbol_bot = multi_engine.get_bot(symbol)
            if symbol_bot is None:
                return symbol, None, None, None

            analysis = None
            try:
                ohlcv = await _fetch_candles(symbol_bot, symbol, settings.TIMEFRAME, 200)
                if ohlcv:
                    analyzer = MarketAnalyzer(symbol, settings.TIMEFRAME)
                    analysis = analyzer.analyze(ohlcv)
            except Exception:
                pass

            generator = SignalGenerator(min_rr=settings.MIN_RR_RATIO)
            score = generator.score(analysis) if analysis else 0
            signal = generator.generate_signal(analysis) if analysis else None
            return symbol, analysis, score, signal

        fetched = await asyncio.gather(
            *[_fetch_symbol_data(s) for s in multi_engine.symbols],
            return_exceptions=True,
        )
        fetched_map = {}
        for item in fetched:
            if isinstance(item, Exception):
                continue
            sym, analysis, score, signal = item
            fetched_map[sym] = (analysis, score, signal)

        for symbol in multi_engine.symbols:
            symbol_bot = multi_engine.get_bot(symbol)
            if symbol_bot is None:
                continue

            analysis, score, signal = fetched_map.get(symbol, (None, 0, None))

            has_position = symbol_bot.has_active_position()
            _check_state_changes(symbol, _sanitize_signal(symbol_bot.last_signal), has_position)

            # Build position info — ALWAYS recalculate PnL from current price
            def _calc_pnl(side: str, entry_price: float, current_price: float, size: float) -> float:
                if side == "BUY":
                    return (current_price - entry_price) * size
                elif side == "SELL":
                    return (entry_price - current_price) * size
                return 0.0

            position = None
            current_price = float(analysis["current_price"]) if analysis and analysis.get("current_price") else None
            if symbol_bot._paper_position is not None:
                pos = symbol_bot._paper_position
                entry = pos.get("entry") if isinstance(pos, dict) else None
                size = pos.get("size") if isinstance(pos, dict) else None
                side = pos.get("side", "").upper() if isinstance(pos, dict) else ""
                entry_f = float(entry) if entry else None
                size_f = float(size) if size else None
                # ALWAYS recalculate PnL from current price
                pnl_value = None
                if current_price is not None and entry_f is not None and size_f is not None and size_f > 0:
                    pnl_value = _calc_pnl(side, entry_f, current_price, size_f)
                position = {
                    "side": side,
                    "entry": str(entry) if entry else None,
                    "sl": str(pos.get("sl")) if isinstance(pos, dict) and pos.get("sl") else None,
                    "tp1": str(pos.get("tp1")) if isinstance(pos, dict) and pos.get("tp1") else None,
                    "tp2": str(pos.get("tp2")) if isinstance(pos, dict) and pos.get("tp2") else None,
                    "tp3": str(pos.get("tp3")) if isinstance(pos, dict) and pos.get("tp3") else None,
                    "size": str(size) if size else None,
                    "unrealized_pnl": str(pnl_value) if pnl_value is not None else "0",
                    "opened_at": pos.get("opened_at") if isinstance(pos, dict) else None,
                }
            else:
                pm = getattr(symbol_bot, "position_manager", None)
                if pm is not None:
                    live_positions = getattr(pm, "positions", {})
                    if live_positions:
                        for pid, lpos in live_positions.items():
                            entry_f = float(lpos.entry_price) if lpos.entry_price else None
                            qty_f = float(lpos.quantity) if lpos.quantity else None
                            side_str = (lpos.side or "").upper()
                            # ALWAYS recalculate PnL from current price
                            pnl_value = 0.0
                            if current_price is not None and entry_f is not None and qty_f is not None and qty_f > 0:
                                pnl_value = _calc_pnl(side_str, entry_f, current_price, qty_f)
                            position = {
                                "side": side_str,
                                "entry": str(lpos.entry_price) if lpos.entry_price else None,
                                "sl": str(lpos.sl_price) if lpos.sl_price else None,
                                "tp1": str(lpos.tp_prices[0]) if lpos.tp_prices and len(lpos.tp_prices) > 0 else None,
                                "tp2": str(lpos.tp_prices[1]) if lpos.tp_prices and len(lpos.tp_prices) > 1 else None,
                                "tp3": str(lpos.tp_prices[2]) if lpos.tp_prices and len(lpos.tp_prices) > 2 else None,
                                "size": str(lpos.quantity) if lpos.quantity else None,
                                "unrealized_pnl": str(pnl_value),
                                "opened_at": lpos.entry_time if hasattr(lpos, 'entry_time') else None,
                            }
                            break

            diag = multi_engine.get_symbol_diagnostics(symbol)

            symbols_data.append({
                "symbol": symbol,
                "running": symbol_bot.running,
                "health": diag["health"],
                "has_position": has_position,
                "position": position,
                "last_signal": _sanitize_signal(symbol_bot.last_signal),
                "loop_count": symbol_bot._loop_count,
                "last_loop_time": diag["last_loop_time"],
                "last_error": diag["last_error"],
                "analysis": {
                    "current_price": float(analysis["current_price"]) if analysis and analysis.get("current_price") else None,
                    "atr": float(analysis["atr"]) if analysis and analysis.get("atr") else None,
                    "score": score,
                    "signal": signal,
                    "structure": analysis.get("structure") if analysis else None,
                    "fvgs_count": len(analysis.get("fvgs", [])) if analysis else 0,
                    "ob_count": len(analysis.get("order_blocks", [])) if analysis else 0,
                },
            })
    elif bot is not None:
        # Single symbol mode
        analysis = None
        try:
            ohlcv = await _fetch_candles(bot, bot.symbol, settings.TIMEFRAME, 200)
            if ohlcv:
                analyzer = MarketAnalyzer(bot.symbol, settings.TIMEFRAME)
                analysis = analyzer.analyze(ohlcv)
        except Exception:
            pass

        generator = SignalGenerator(min_rr=settings.MIN_RR_RATIO)
        score = generator.score(analysis) if analysis else 0
        signal = generator.generate_signal(analysis) if analysis else None

        has_position = bot.has_active_position()
        _check_state_changes(bot.symbol, _sanitize_signal(bot.last_signal), has_position)

        def _calc_pnl_single(side: str, entry_price: float, current_price: float, size: float) -> float:
            if side == "BUY":
                return (current_price - entry_price) * size
            elif side == "SELL":
                return (entry_price - current_price) * size
            return 0.0

        position = None
        current_price_single = float(analysis["current_price"]) if analysis and analysis.get("current_price") else None
        if bot._paper_position is not None:
            pos = bot._paper_position
            entry = pos.get("entry") if isinstance(pos, dict) else None
            size = pos.get("size") if isinstance(pos, dict) else None
            side = pos.get("side", "").upper() if isinstance(pos, dict) else ""
            entry_f = float(entry) if entry else None
            size_f = float(size) if size else None
            # ALWAYS recalculate PnL from current price
            pnl_value = None
            if current_price_single is not None and entry_f is not None and size_f is not None and size_f > 0:
                pnl_value = _calc_pnl_single(side, entry_f, current_price_single, size_f)
            position = {
                "side": side,
                "entry": str(entry) if entry else None,
                "sl": str(pos.get("sl")) if isinstance(pos, dict) and pos.get("sl") else None,
                "tp1": str(pos.get("tp1")) if isinstance(pos, dict) and pos.get("tp1") else None,
                "tp2": str(pos.get("tp2")) if isinstance(pos, dict) and pos.get("tp2") else None,
                "tp3": str(pos.get("tp3")) if isinstance(pos, dict) and pos.get("tp3") else None,
                "size": str(size) if size else None,
                "unrealized_pnl": str(pnl_value) if pnl_value is not None else "0",
                "opened_at": pos.get("opened_at") if isinstance(pos, dict) else None,
            }
        else:
            # Live/testnet: check position_manager.positions
            pm = getattr(bot, "position_manager", None)
            if pm is not None:
                live_positions = getattr(pm, "positions", {})
                if live_positions:
                    for pid, lpos in live_positions.items():
                        entry_f = float(lpos.entry_price) if lpos.entry_price else None
                        qty_f = float(lpos.quantity) if lpos.quantity else None
                        side_str = (lpos.side or "").upper()
                        # ALWAYS recalculate PnL from current price
                        pnl_value = 0.0
                        if current_price_single is not None and entry_f is not None and qty_f is not None and qty_f > 0:
                            pnl_value = _calc_pnl_single(side_str, entry_f, current_price_single, qty_f)
                        position = {
                            "side": side_str,
                            "entry": str(lpos.entry_price) if lpos.entry_price else None,
                            "sl": str(lpos.sl_price) if lpos.sl_price else None,
                            "tp1": str(lpos.tp_prices[0]) if lpos.tp_prices and len(lpos.tp_prices) > 0 else None,
                            "tp2": str(lpos.tp_prices[1]) if lpos.tp_prices and len(lpos.tp_prices) > 1 else None,
                            "tp3": str(lpos.tp_prices[2]) if lpos.tp_prices and len(lpos.tp_prices) > 2 else None,
                            "size": str(lpos.quantity) if lpos.quantity else None,
                            "unrealized_pnl": str(pnl_value),
                            "opened_at": lpos.entry_time if hasattr(lpos, 'entry_time') else None,
                        }
                        break

        task = _state.get("engine_task")
        task_state = "UNKNOWN"
        if task is not None:
            if task.cancelled():
                task_state = "CANCELLED"
            elif task.done():
                task_state = "DONE"
            else:
                task_state = "RUNNING"

        symbols_data.append({
            "symbol": bot.symbol,
            "running": bot.running,
            "health": "HEALTHY" if bot.running else "STOPPED",
            "has_position": has_position,
            "position": position,
            "last_signal": _sanitize_signal(bot.last_signal),
            "loop_count": bot._loop_count,
            "last_loop_time": bot._last_loop_time,
            "last_error": bot._last_error,
            "analysis": {
                "current_price": float(analysis["current_price"]) if analysis and analysis.get("current_price") else None,
                "atr": float(analysis["atr"]) if analysis and analysis.get("atr") else None,
                "score": score,
                "signal": signal,
                "structure": analysis.get("structure") if analysis else None,
                "fvgs_count": len(analysis.get("fvgs", [])) if analysis else 0,
                "ob_count": len(analysis.get("order_blocks", [])) if analysis else 0,
            },
        })
    else:
        # No engine running - show configured symbols
        for symbol in settings.symbols_list:
            symbols_data.append({
                "symbol": symbol,
                "running": False,
                "health": "STOPPED",
                "has_position": False,
                "position": None,
                "last_signal": None,
                "loop_count": 0,
            "last_loop_time": None,
                "last_error": None,
                "analysis": None,
            })

    return {"symbols": symbols_data}


@router.get("/dashboard/position")
async def get_dashboard_position(symbol: Optional[str] = None):
    """Fast endpoint: return current position + price for one symbol without fetching candles.

    Current price is fetched from the market data provider (paper) or exchange
    ticker so it reflects the real-time price independent of signal data.
    Unrealized PnL is always recalculated from entry/current/size to avoid
    stale or zero values.
    """
    from backend.api.endpoints import _state
    from decimal import Decimal

    symbol = symbol or settings.SYMBOL
    multi_engine = _state.get("multi_engine")
    bot = None
    if multi_engine is not None and multi_engine.running:
        bot = multi_engine.get_bot(symbol)
    if bot is None:
        bot = _state.get("bot")

    if bot is None:
        return {"symbol": symbol, "has_position": False, "position": None, "current_price": None}

    has_position = bot.has_active_position()
    position = None
    current_price = None

    # Fetch actual current price from market data (paper) or exchange ticker.
    # NEVER use last_signal["entry"] as current price — it is a signal level,
    # not the live market price.
    try:
        mode = settings.TRADING_MODE.lower().strip()
        if mode == "paper":
            provider = getattr(bot, "market_data_provider", None)
            if provider is not None:
                fetcher = getattr(provider, "fetch_ohlcv", None)
                if callable(fetcher):
                    candles = fetcher(symbol, settings.TIMEFRAME, limit=1)
                    if candles and len(candles) > 0:
                        last_candle = candles[-1]
                        if len(last_candle) >= 5:
                            current_price = float(last_candle[4])
        else:
            exchange = getattr(bot, "exchange", None)
            if exchange is not None:
                ticker = getattr(exchange, "fetch_ticker", None)
                if callable(ticker):
                    t = ticker(symbol)
                    if hasattr(t, "last") and t.last:
                        current_price = float(t.last)
                    elif isinstance(t, dict) and t.get("last"):
                        current_price = float(t["last"])
    except Exception:
        pass

    # Fallback: try to get price from last analysis of the current bot
    if current_price is None:
        try:
            ohlcv = bot.fetch_ohlcv(limit=1)
            if ohlcv and len(ohlcv) > 0:
                last_candle = ohlcv[-1]
                if len(last_candle) >= 5:
                    current_price = float(last_candle[4])
        except Exception:
            pass

    def _calc_pnl(side: str, entry_price: float, current_price: float, size: float) -> float:
        """Calculate unrealized PnL. ALWAYS use this instead of stored value."""
        if side == "BUY":
            return (current_price - entry_price) * size
        elif side == "SELL":
            return (entry_price - current_price) * size
        return 0.0

    if bot._paper_position is not None:
        pos = bot._paper_position
        entry = pos.get("entry") if isinstance(pos, dict) else None
        size = pos.get("size") if isinstance(pos, dict) else None
        side = pos.get("side", "").upper() if isinstance(pos, dict) else ""

        entry_f = float(entry) if entry else None
        size_f = float(size) if size else None

        # ALWAYS recalculate PnL from current price, never trust stored value
        pnl_value = None
        if current_price is not None and entry_f is not None and size_f is not None and size_f > 0:
            pnl_value = _calc_pnl(side, entry_f, current_price, size_f)

        position = {
            "side": side,
            "entry": str(entry) if entry else None,
            "sl": str(pos.get("sl")) if isinstance(pos, dict) and pos.get("sl") else None,
            "tp1": str(pos.get("tp1")) if isinstance(pos, dict) and pos.get("tp1") else None,
            "tp2": str(pos.get("tp2")) if isinstance(pos, dict) and pos.get("tp2") else None,
            "tp3": str(pos.get("tp3")) if isinstance(pos, dict) and pos.get("tp3") else None,
            "size": str(size) if size else None,
            "unrealized_pnl": str(pnl_value) if pnl_value is not None else "0",
            "opened_at": pos.get("opened_at") if isinstance(pos, dict) else None,
        }
    else:
        pm = getattr(bot, "position_manager", None)
        if pm is not None:
            live_positions = getattr(pm, "positions", {})
            if live_positions:
                for pid, lpos in live_positions.items():
                    entry_f = float(lpos.entry_price) if lpos.entry_price else None
                    qty_f = float(lpos.quantity) if lpos.quantity else None
                    side_str = (lpos.side or "").upper()

                    # ALWAYS recalculate PnL from current price
                    pnl_value = 0.0
                    if current_price is not None and entry_f is not None and qty_f is not None and qty_f > 0:
                        pnl_value = _calc_pnl(side_str, entry_f, current_price, qty_f)

                    position = {
                        "side": side_str,
                        "entry": str(lpos.entry_price) if lpos.entry_price else None,
                        "sl": str(lpos.sl_price) if lpos.sl_price else None,
                        "tp1": str(lpos.tp_prices[0]) if lpos.tp_prices and len(lpos.tp_prices) > 0 else None,
                        "tp2": str(lpos.tp_prices[1]) if lpos.tp_prices and len(lpos.tp_prices) > 1 else None,
                        "tp3": str(lpos.tp_prices[2]) if lpos.tp_prices and len(lpos.tp_prices) > 2 else None,
                        "size": str(lpos.quantity) if lpos.quantity else None,
                        "unrealized_pnl": str(pnl_value),
                        "opened_at": lpos.entry_time if hasattr(lpos, 'entry_time') else None,
                    }
                    break

    return {"symbol": symbol, "has_position": has_position, "position": position, "current_price": current_price}


@router.get("/dashboard/events")
async def get_dashboard_events():
    """Return recent activity events for the dashboard log."""
    return {"events": list(_activity_log)}


@router.get("/dashboard/diagnostics")
async def get_dashboard_diagnostics():
    """Return per-symbol and account-wide diagnostic data for debugging."""
    from backend.api.endpoints import _state
    from backend.config import settings

    multi_engine = _state.get("multi_engine")
    bot = _state.get("bot")

    symbols_diag = []

    if multi_engine is not None and multi_engine.running:
        gate = multi_engine._open_trade_gate
        for symbol in multi_engine.symbols:
            sym_bot = multi_engine.get_bot(symbol)
            if sym_bot is None:
                symbols_diag.append({"symbol": symbol, "exists": False})
                continue

            pm = getattr(sym_bot, "position_manager", None)
            positions = getattr(pm, "positions", {}) if pm else {}
            risk = sym_bot.risk_manager

            # Count protective orders from open_orders
            protective_count = 0
            order_mgr = getattr(sym_bot, "order_manager", None)
            if order_mgr is not None:
                for oid, o in order_mgr.open_orders.items():
                    pos = await pm.get_position_by_order(oid) if pm else None
                    if pos is not None:
                        protective_count += 1

            # Free base (spot only)
            free_base = None
            reserved_base = None
            if settings.EXCHANGE_MARKET_TYPE.lower() == "spot" and order_mgr is not None:
                try:
                    fb, tb = await order_mgr._get_spot_balance(symbol)
                    free_base = float(fb)
                    reserved_base = float(tb - fb)
                except Exception:
                    pass

            symbols_diag.append({
                "symbol": symbol,
                "position_exists": len(positions) > 0,
                "position_id": list(positions.keys())[0] if positions else None,
                "position_side": list(positions.values())[0].side if positions else None,
                "position_quantity": str(list(positions.values())[0].quantity) if positions else None,
                "risk_open_trades": risk.open_trades,
                "gate_count": gate.count,
                "gate_limit": gate.limit,
                "protective_orders": protective_count,
                "free_base": free_base,
                "reserved_base": reserved_base,
                "last_error": sym_bot._last_error,
            })

        return {
            "risk_open_trades": sum(
                multi_engine.get_bot(s).risk_manager.open_trades
                for s in multi_engine.symbols
                if multi_engine.get_bot(s) is not None
            ),
            "gate_count": gate.count,
            "gate_limit": gate.limit,
            "position_count": sum(
                multi_engine.get_bot(s).get_live_position_count()
                for s in multi_engine.symbols
                if multi_engine.get_bot(s) is not None
            ),
            "symbols_running": len([
                s for s in multi_engine.symbols
                if multi_engine.get_bot(s) is not None and multi_engine.get_bot(s).running
            ]),
            "symbols": symbols_diag,
        }

    # Single symbol fallback
    if bot is not None:
        pm = getattr(bot, "position_manager", None)
        positions = getattr(pm, "positions", {}) if pm else {}
        risk = bot.risk_manager
        return {
            "risk_open_trades": risk.open_trades,
            "gate_count": 0,
            "gate_limit": settings.MAX_OPEN_TRADES,
            "position_count": len(positions),
            "symbols_running": 1 if bot.running else 0,
            "symbols": [{
                "symbol": bot.symbol,
                "position_exists": len(positions) > 0,
                "position_id": list(positions.keys())[0] if positions else None,
                "position_side": list(positions.values())[0].side if positions else None,
                "position_quantity": str(list(positions.values())[0].quantity) if positions else None,
                "risk_open_trades": risk.open_trades,
                "protective_orders": 0,
                "last_error": bot._last_error,
            }],
        }

    return {"risk_open_trades": 0, "gate_count": 0, "gate_limit": settings.MAX_OPEN_TRADES, "position_count": 0, "symbols_running": 0, "symbols": []}


@router.get("/dashboard/logs")
async def get_dashboard_logs():
    """Return recent log records captured from Python logging."""
    from backend.utils.logger import get_log_buffer

    entries = []
    for record in get_log_buffer():
        try:
            from datetime import datetime, timezone, timedelta
            utc_time = datetime.fromtimestamp(record["time"], tz=timezone.utc)
            local_time = utc_time + timedelta(seconds=_UTC3_OFFSET_SECONDS)
            ts = local_time.strftime("%H:%M:%S")
        except Exception:
            ts = "??"
        entries.append({
            "time": ts,
            "level": record["level"],
            "logger": record["logger"],
            "message": record["message"],
        })
    return {"logs": entries}


async def _fetch_candles(bot, symbol: str, timeframe: str, limit: int) -> list:
    if symbol == bot.symbol:
        fetcher = getattr(bot, "fetch_ohlcv", None)
        if not callable(fetcher):
            raise RuntimeError("Bot does not expose fetch_ohlcv")
        result = fetcher(limit=limit, timeframe=timeframe)
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise RuntimeError("fetch_ohlcv must be synchronous")
        if not isinstance(result, list):
            raise RuntimeError("fetch_ohlcv must return a list")
        return result
    mode = settings.TRADING_MODE.lower().strip()
    if mode == "paper":
        provider = getattr(bot, "market_data_provider", None)
        if provider is None:
            raise RuntimeError("Paper mode requires an explicit local market-data provider")
        fetcher = getattr(provider, "fetch_ohlcv", None)
        if not callable(fetcher):
            raise TypeError("Paper market-data provider must define fetch_ohlcv")
        result = fetcher(symbol, timeframe, limit=limit)
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise TypeError("Paper market-data provider must expose a synchronous fetch_ohlcv method")
        if not isinstance(result, list):
            raise TypeError("Paper market-data provider fetch_ohlcv must return a list")
        return result
    exchange = bot._ensure_exchange()
    return exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
