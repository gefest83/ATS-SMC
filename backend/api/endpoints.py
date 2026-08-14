"""REST API for the trading platform (status, analysis, strategies, stats)."""
from collections import defaultdict, deque
import asyncio
import contextlib
from decimal import Decimal
import inspect
import logging
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from backend.config import settings
from backend.core.analysis.market_analyzer import MarketAnalyzer
from backend.core.analysis.signal_generator import SignalGenerator
from backend.core.exchange.factory import ADAPTERS, create_exchange

router = APIRouter()
_rate_windows: dict[str, deque[float]] = defaultdict(deque)


def _sanitize_signal(sig):
    """Convert Decimal values in last_signal to float for JSON serialization."""
    if sig is None:
        return None
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in sig.items()}
_state: dict = {
    "bot": None,
    "strategy_registry": None,
    "engine_task": None,
    "multi_engine": None,
    "multi_tasks": {},
}
_engine_lock = asyncio.Lock()
_paper_market_data_provider = None


class PaperMarketDataError(RuntimeError):
    """Raised when paper mode has no valid local market-data source."""


def _json_safe(value):
    """Encode Decimal values as exact strings before FastAPI's float encoder."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _require_api_access(authorization: Optional[str]) -> None:
    if not settings.API_AUTH_ENABLED:
        return
    expected = settings.API_ACCESS_TOKEN.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="API authentication is not configured")
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid API credentials")


def _enforce_rate_limit(request: Request) -> None:
    limit = max(1, int(settings.API_RATE_LIMIT_PER_MINUTE))
    now = time.monotonic()
    key = request.client.host if request.client else "unknown"
    window = _rate_windows[key]
    cutoff = now - 60.0
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    window.append(now)


def _protect(request: Request, authorization: Optional[str]) -> None:
    _require_api_access(authorization)
    _enforce_rate_limit(request)


def set_state(**kwargs) -> None:
    _state.update(kwargs)


async def stop_engine_state(reason: str = "Остановка через API") -> None:
    """Run the single coordinated engine shutdown path."""
    async with _engine_lock:
        # Stop multi-symbol engine if running
        multi_engine = _state.get("multi_engine")
        if multi_engine is not None:
            try:
                await multi_engine.stop(reason=reason)
            except Exception:
                logging.getLogger(__name__).exception("Multi-engine stop failed")
            _state["multi_engine"] = None
            _state["multi_tasks"] = {}

        bot = _state.get("bot")
        task = _state.get("engine_task")

        if bot is not None:
            stop = getattr(bot, "stop", None)
            if callable(stop):
                try:
                    result = stop()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logging.getLogger(__name__).exception("Engine stop failed")
            else:
                bot.running = False

        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        _state["engine_task"] = None
        _state["bot"] = None


async def start_engine_state() -> bool:
    """Validate and start at most one engine task under the lifecycle lock."""
    async with _engine_lock:
        multi_engine = _state.get("multi_engine")
        if multi_engine is not None and multi_engine.running:
            return False

        task = _state.get("engine_task")
        if task and not task.done():
            return False

        symbols = settings.symbols_list

        if len(symbols) > 1:
            from backend.core.engine.multi_symbol import MultiSymbolEngine

            if settings.TRADING_MODE.lower().strip() == "paper" and _paper_market_data_provider is not None:
                multi_engine = MultiSymbolEngine(market_data_provider=_paper_market_data_provider)
            else:
                multi_engine = MultiSymbolEngine()

            await multi_engine.start()
            _state["multi_engine"] = multi_engine

            if multi_engine.symbols:
                first_symbol = multi_engine.symbols[0]
                _state["bot"] = multi_engine.get_bot(first_symbol)

            return True
        else:
            bot = _state.get("bot")
            if bot is None:
                module = __import__("backend.core.engine.smc_bot", fromlist=["SMCBot"])
                bot_class = module.SMCBot
                if settings.TRADING_MODE.lower().strip() == "paper" and _paper_market_data_provider is not None:
                    bot = bot_class(market_data_provider=_paper_market_data_provider)
                else:
                    bot = bot_class()
                _state["bot"] = bot

            validate_startup = getattr(bot, "validate_startup", None)
            if callable(validate_startup):
                try:
                    result = validate_startup()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    _state["bot"] = None
                    raise

            task = asyncio.create_task(bot.run(), name="smc-engine")
            _state["engine_task"] = task
            return True


async def _local_analysis_ohlcv(symbol: str, timeframe: str, limit: int) -> list:
    """Read paper analysis data only from an explicit local provider."""
    provider = _paper_market_data_provider
    if provider is None:
        raise PaperMarketDataError(
            "Paper analysis requires an explicit local market-data provider"
        )
    fetcher = getattr(provider, "fetch_ohlcv", None)
    if not callable(fetcher):
        raise PaperMarketDataError(
            "Paper market-data provider must define fetch_ohlcv"
        )
    result = fetcher(symbol, timeframe, limit=limit)
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise PaperMarketDataError(
            "Paper market-data provider must expose a synchronous fetch_ohlcv method"
        )
    if not isinstance(result, list):
        raise PaperMarketDataError(
            "Paper market-data provider fetch_ohlcv must return a list"
        )
    return result


def set_paper_analysis_provider(provider) -> None:
    """Inject a local provider for paper-only API analysis/tests."""
    global _paper_market_data_provider
    _paper_market_data_provider = provider


def clear_paper_analysis_provider() -> None:
    """Remove the injected paper provider after an isolated test or run."""
    global _paper_market_data_provider
    _paper_market_data_provider = None


def _reset_engine_state() -> None:
    """Reset API state for local tests without touching remote services."""
    clear_paper_analysis_provider()
    _state.update(bot=None, strategy_registry=None, engine_task=None)


def _analysis_response(analysis: dict, symbol: str, timeframe: str, generator):
    return _json_safe({
        "symbol": symbol,
        "timeframe": timeframe,
        "current_price": analysis["current_price"],
        "atr": analysis["atr"],
        "fvgs": analysis["fvgs"][-10:],
        "order_blocks": analysis["order_blocks"][-10:],
        "structure": analysis["structure"],
        "score": generator.score(analysis),
        "signal": generator.generate_signal(analysis),
    })


async def _fetch_analysis_ohlcv(symbol: str, timeframe: str, limit: int):
    if settings.TRADING_MODE.lower().strip() == "paper":
        return await _local_analysis_ohlcv(symbol, timeframe, limit), None
    exchange = create_exchange()
    return await asyncio.to_thread(exchange.fetch_ohlcv, symbol, timeframe, limit), exchange


@router.post("/engine/start")
async def start_engine(request: Request, authorization: Optional[str] = Header(default=None)):
    """Start the SMC engine exactly once and expose the task for clean shutdown."""
    _protect(request, authorization)
    try:
        started = await start_engine_state()
    except (PaperMarketDataError, RuntimeError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "running": True,
        "message": "Engine started" if started else "Engine already running",
    }


@router.post("/engine/stop")
async def stop_engine(request: Request, authorization: Optional[str] = Header(default=None)):
    """Stop the engine and its background resources without leaving open tasks."""
    _protect(request, authorization)
    await stop_engine_state(reason="Остановка через API")
    return {"running": False, "message": "Engine stopped"}


@router.get("/status")
async def get_status(request: Request, authorization: Optional[str] = Header(default=None)):
    _protect(request, authorization)
    bot = _state.get("bot")
    multi_engine = _state.get("multi_engine")

    if multi_engine is not None and multi_engine.running:
        engine_diag = multi_engine.get_engine_diagnostics()
        return {
            "app": settings.APP_NAME,
            "trading_mode": settings.TRADING_MODE,
            "exchange": settings.EXCHANGE,
            "exchange_mode": settings.EXCHANGE_MODE,
            "symbol": settings.SYMBOL,
            "timeframe": settings.TIMEFRAME,
            "running": True,
            "multi_symbol": True,
            "symbols": multi_engine.symbols,
            "last_signal": _sanitize_signal(bot.last_signal) if bot else None,
            "engine": engine_diag,
        }

    task = _state.get("engine_task")
    return {
        "app": settings.APP_NAME,
        "trading_mode": settings.TRADING_MODE,
        "exchange": settings.EXCHANGE,
        "exchange_mode": settings.EXCHANGE_MODE,
        "symbol": settings.SYMBOL,
        "timeframe": settings.TIMEFRAME,
        "running": bool(bot and bot.running),
        "multi_symbol": False,
        "symbols": [settings.SYMBOL],
        "last_signal": _sanitize_signal(bot.last_signal) if bot else None,
        "engine": {
            "type": "SMCBot",
            "status": "RUNNING" if bot and bot.running else "STOPPED",
            "bot_count": 1 if bot else 0,
            "task_count": 1 if task and not task.done() else 0,
            "healthy_count": 1 if bot and bot.running and not getattr(bot, "_last_error", None) else 0,
            "error_count": 1 if bot and getattr(bot, "_last_error", None) else 0,
            "started_at": getattr(bot, "_started_at", None) if bot else None,
            "uptime": (time.time() - bot._started_at) if bot and getattr(bot, "_started_at", None) else None,
            "last_heartbeat": getattr(bot, "_last_loop_time", None) if bot else None,
            "loop_count": getattr(bot, "_loop_count", 0) if bot else 0,
            "last_error": getattr(bot, "_last_error", None) if bot else None,
        },
    }


@router.get("/symbols")
async def list_symbols(request: Request, authorization: Optional[str] = Header(default=None)):
    """List all configured trading symbols."""
    _protect(request, authorization)
    return {"symbols": settings.symbols_list}


@router.get("/status/{symbol:path}")
async def get_symbol_status(
    symbol: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """Get status for a specific symbol."""
    _protect(request, authorization)
    multi_engine = _state.get("multi_engine")

    if multi_engine is not None and multi_engine.running:
        bot = multi_engine.get_bot(symbol)
        if bot is None:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        diag = multi_engine.get_symbol_diagnostics(symbol)

        def _calc_pnl_ep(side: str, entry_price: float, current_price: float, size: float) -> float:
            if side == "BUY":
                return (current_price - entry_price) * size
            elif side == "SELL":
                return (entry_price - current_price) * size
            return 0.0

        position = None
        has_position_fn = getattr(bot, "has_active_position", None)
        has_position = has_position_fn() if callable(has_position_fn) else (bot._paper_position is not None)
        current_price_ep = None
        try:
            exchange = getattr(bot, "exchange", None)
            if exchange is not None:
                ticker = getattr(exchange, "fetch_ticker", None)
                if callable(ticker):
                    t = ticker(symbol)
                    if hasattr(t, "last") and t.last:
                        current_price_ep = float(t.last)
                    elif isinstance(t, dict) and t.get("last"):
                        current_price_ep = float(t["last"])
        except Exception:
            pass
        if bot._paper_position is not None:
            pos = bot._paper_position
            entry = pos.get("entry") if pos else None
            size = pos.get("size") if pos else None
            side = pos.get("side", "").upper() if pos else ""
            entry_f = float(entry) if entry else None
            size_f = float(size) if size else None
            pnl_value = None
            if current_price_ep is not None and entry_f is not None and size_f is not None and size_f > 0:
                pnl_value = _calc_pnl_ep(side, entry_f, current_price_ep, size_f)
            position = {
                "side": side,
                "entry": str(entry) if entry else None,
                "sl": str(pos.get("sl")) if pos.get("sl") else None,
                "tp1": str(pos.get("tp1")) if pos.get("tp1") else None,
                "tp2": str(pos.get("tp2")) if pos.get("tp2") else None,
                "tp3": str(pos.get("tp3")) if pos.get("tp3") else None,
                "size": str(size) if size else None,
                "unrealized_pnl": str(pnl_value) if pnl_value is not None else "0",
                "opened_at": pos.get("opened_at") if pos else None,
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
                        pnl_value = 0.0
                        if current_price_ep is not None and entry_f is not None and qty_f is not None and qty_f > 0:
                            pnl_value = _calc_pnl_ep(side_str, entry_f, current_price_ep, qty_f)
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

        return {
            "symbol": symbol,
            "running": bot.running,
            "health": diag["health"],
            "last_signal": _sanitize_signal(bot.last_signal),
            "has_position": has_position,
            "paper_position": bot._paper_position,
            "position": position,
            "runtime": {
                "loop_count": diag["loop_count"],
                "last_loop_time": diag["last_loop_time"],
                "last_error": diag["last_error"],
                "bot_object_id": diag["bot_object_id"],
                "task_state": diag["task_state"],
                "task_name": diag["task_name"],
            },
        }

    bot = _state.get("bot")
    if bot and bot.symbol == symbol:
        has_position_fn = getattr(bot, "has_active_position", None)
        has_position = has_position_fn() if callable(has_position_fn) else (bot._paper_position is not None)
        position = None
        current_price_ep_single = None
        try:
            exchange = getattr(bot, "exchange", None)
            if exchange is not None:
                ticker = getattr(exchange, "fetch_ticker", None)
                if callable(ticker):
                    t = ticker(symbol)
                    if hasattr(t, "last") and t.last:
                        current_price_ep_single = float(t.last)
                    elif isinstance(t, dict) and t.get("last"):
                        current_price_ep_single = float(t["last"])
        except Exception:
            pass
        if bot._paper_position is not None:
            pos = bot._paper_position
            entry = pos.get("entry") if pos else None
            size = pos.get("size") if pos else None
            side = pos.get("side", "").upper() if pos else ""
            entry_f = float(entry) if entry else None
            size_f = float(size) if size else None
            pnl_value = None
            if current_price_ep_single is not None and entry_f is not None and size_f is not None and size_f > 0:
                if side == "BUY":
                    pnl_value = (current_price_ep_single - entry_f) * size_f
                elif side == "SELL":
                    pnl_value = (entry_f - current_price_ep_single) * size_f
            position = {
                "side": side,
                "entry": str(entry) if entry else None,
                "sl": str(pos.get("sl")) if pos.get("sl") else None,
                "tp1": str(pos.get("tp1")) if pos.get("tp1") else None,
                "tp2": str(pos.get("tp2")) if pos.get("tp2") else None,
                "tp3": str(pos.get("tp3")) if pos.get("tp3") else None,
                "size": str(size) if size else None,
                "unrealized_pnl": str(pnl_value) if pnl_value is not None else "0",
                "opened_at": pos.get("opened_at") if pos else None,
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
                        pnl_value = 0.0
                        if current_price_ep_single is not None and entry_f is not None and qty_f is not None and qty_f > 0:
                            if side_str == "BUY":
                                pnl_value = (current_price_ep_single - entry_f) * qty_f
                            elif side_str == "SELL":
                                pnl_value = (entry_f - current_price_ep_single) * qty_f
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

        return {
            "symbol": symbol,
            "running": bot.running,
            "health": "HEALTHY" if bot.running else "STOPPED",
            "last_signal": _sanitize_signal(bot.last_signal),
            "has_position": has_position,
            "paper_position": bot._paper_position,
            "position": position,
            "runtime": {
                "loop_count": bot._loop_count,
                "last_loop_time": getattr(bot, "_last_loop_time", None),
                "last_error": bot._last_error,
                "bot_object_id": id(bot),
                "task_state": task_state,
                "task_name": task.get_name() if task else None,
            },
        }

    raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")


@router.get("/exchanges")
async def list_exchanges(request: Request, authorization: Optional[str] = Header(default=None)):
    _protect(request, authorization)
    return {"supported": sorted(ADAPTERS)}


@router.get("/strategies")
async def list_strategies(request: Request, authorization: Optional[str] = Header(default=None)):
    _protect(request, authorization)
    registry = _state.get("strategy_registry")
    if not registry:
        return {"strategies": []}
    return {
        "strategies": [
            {"name": name, "parameters": strategy.parameters}
            for name, strategy in registry.strategies.items()
        ]
    }


@router.get("/analysis")
async def get_analysis(
    request: Request,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    limit: int = 200,
    authorization: Optional[str] = Header(default=None),
):
    """Run the SMC analysis on demand for any symbol/timeframe."""
    _protect(request, authorization)
    symbol = symbol or settings.SYMBOL
    timeframe = timeframe or settings.TIMEFRAME
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000")

    exchange = None
    try:
        ohlcv, exchange = await _fetch_analysis_ohlcv(symbol, timeframe, limit)
        analysis = MarketAnalyzer(symbol, timeframe).analyze(ohlcv)
        if not analysis:
            raise HTTPException(status_code=404, detail="No market data available")
        generator = SignalGenerator(min_rr=settings.MIN_RR_RATIO)
        return _analysis_response(analysis, symbol, timeframe, generator)
    except HTTPException:
        raise
    except PaperMarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Exchange error: {exc}") from exc
    finally:
        close = getattr(exchange, "close", None) if exchange is not None else None
        if callable(close):
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to close analysis exchange"
                )


@router.get("/risk")
async def get_risk(request: Request, authorization: Optional[str] = Header(default=None)):
    _protect(request, authorization)
    bot = _state.get("bot")
    multi_engine = _state.get("multi_engine")
    risk = bot.risk_manager if bot else None

    result = {
        "risk_per_trade_pct": settings.RISK_PER_TRADE_PCT,
        "max_daily_drawdown_pct": settings.MAX_DAILY_DRAWDOWN_PCT,
        "max_open_trades": settings.MAX_OPEN_TRADES,
        "min_rr_ratio": settings.MIN_RR_RATIO,
        "current_equity": risk.current_equity if risk else settings.INITIAL_EQUITY,
        "open_trades": risk.open_trades if risk else 0,
    }

    if multi_engine is not None and multi_engine.running:
        gate = multi_engine._open_trade_gate
        result["gate_count"] = gate.count
        result["gate_limit"] = gate.limit
        result["gate_registered"] = gate.registered
        per_symbol = {}
        for sym in multi_engine.symbols:
            sym_bot = multi_engine.get_bot(sym)
            if sym_bot is not None:
                per_symbol[sym] = {
                    "open_trades": sym_bot.risk_manager.open_trades,
                    "live_positions": sym_bot.get_live_position_count(),
                }
        result["per_symbol"] = per_symbol

    return result
