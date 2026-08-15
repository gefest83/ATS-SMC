"""
FastAPI REST API for ATS-SMT Pro Trading Bot.
"""
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Dict, Any, List, Set
from datetime import datetime
import logging
import json

from backend.config.settings import config, TradingMode
from backend.core.persistence.database import AsyncSessionLocal, get_db_session
from backend.core.persistence.models import (
    Position, Symbol as SymbolModel, Exchange, Signal, Order, 
    StrategySettings, SystemEvent, RiskEvent
)
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.services.websocket_service import websocket_service

logger = logging.getLogger(__name__)

app = FastAPI(
    title="ATS-SMT Pro Trading Bot API",
    description="REST API for Smart Money Trades Pro trading system",
    version="2.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global engine state
engine_state = {
    "running": False,
    "paused": False,
    "emergency_stop": False,
    "started_at": None,
}


# ============== Health & Status ==============

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0"
    }


@app.get("/status")
async def get_status(session: AsyncSession = Depends(get_db_session)):
    """Get comprehensive system status."""
    
    # Get counts
    positions_stmt = select(func.count()).select_from(Position).where(Position.is_open == True)
    result = await session.execute(positions_stmt)
    open_positions = result.scalar() or 0
    
    signals_stmt = select(func.count()).select_from(Signal)
    result = await session.execute(signals_stmt)
    total_signals = result.scalar() or 0
    
    # Get exchange status
    exchanges_stmt = select(Exchange).where(Exchange.enabled == True)
    result = await session.execute(exchanges_stmt)
    exchanges = result.scalars().all()
    
    exchange_status = []
    for ex in exchanges:
        exchange_status.append({
            "name": ex.name,
            "connected": ex.is_connected,
            "testnet": ex.testnet,
            "last_error": ex.last_error,
            "last_sync": ex.last_sync.isoformat() if ex.last_sync else None
        })
    
    return {
        "engine": {
            "running": engine_state["running"],
            "paused": engine_state["paused"],
            "emergency_stop": engine_state["emergency_stop"],
            "uptime_seconds": (datetime.utcnow() - engine_state["started_at"]).total_seconds() if engine_state["started_at"] else 0,
            "started_at": engine_state["started_at"].isoformat() if engine_state["started_at"] else None
        },
        "trading_mode": config.trading_mode.value,
        "live_enabled": config.live_trading_enabled,
        "open_positions": open_positions,
        "total_signals": total_signals,
        "exchanges": exchange_status,
        "health": "HEALTHY" if not engine_state["emergency_stop"] else "ERROR"
    }


@app.get("/config")
async def get_config():
    """Get current configuration."""
    return {
        "trading_mode": config.trading_mode.value,
        "live_trading_enabled": config.live_trading_enabled,
        "symbols": config.symbols,
        "timeframe": config.timeframe,
        "htf1": config.htf1,
        "htf2": config.htf2,
        "risk_pct": config.risk_pct,
        "max_open_trades": config.max_open_trades,
        "max_daily_drawdown": config.max_daily_drawdown,
        "structure_period": config.structure_period,
        "adx_th": config.adx_th,
        "adx_trend": config.adx_trend,
        "adx_dead": config.adx_dead,
        "filter_mode": config.filter_mode,
        "use_cooldown": config.use_cooldown,
        "cooldown_bars": config.cooldown_bars,
        "use_breakeven": config.use_breakeven,
        "use_trailing": config.use_trailing,
    }


# ============== Markets & Symbols ==============

@app.get("/markets")
async def get_markets(session: AsyncSession = Depends(get_db_session)):
    """Get all available markets."""
    stmt = select(Exchange).where(Exchange.enabled == True)
    result = await session.execute(stmt)
    exchanges = result.scalars().all()
    
    markets = []
    for ex in exchanges:
        markets.append({
            "exchange": ex.name,
            "enabled": ex.enabled,
            "testnet": ex.testnet,
            "connected": ex.is_connected
        })
    
    return {"markets": markets}


@app.get("/symbols")
async def get_symbols(
    exchange: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session)
):
    """Get all trading symbols."""
    conditions = []
    
    if exchange:
        ex_stmt = select(Exchange.id).where(Exchange.name == exchange)
        ex_result = await session.execute(ex_stmt)
        ex_id = ex_result.scalar_one_or_none()
        if ex_id:
            conditions.append(SymbolModel.exchange_id == ex_id)
    
    stmt = select(SymbolModel)
    if conditions:
        from sqlalchemy import and_
        stmt = stmt.where(and_(*conditions))
    
    result = await session.execute(stmt)
    symbols = result.scalars().all()
    
    return {
        "symbols": [
            {
                "id": s.id,
                "canonical": s.canonical_symbol,
                "exchange_symbol": s.exchange_symbol,
                "exchange_id": s.exchange_id,
                "is_available": s.is_available,
                "is_active": s.is_active,
                "unavailable_reason": s.unavailable_reason,
                "min_qty": s.min_qty,
                "qty_step": s.qty_step,
                "min_notional": s.min_notional,
                "price_precision": s.price_precision,
                "qty_precision": s.qty_precision
            }
            for s in symbols
        ]
    }


# ============== Positions ==============

@app.get("/positions")
async def get_positions(
    status: str = "open",
    session: AsyncSession = Depends(get_db_session)
):
    """Get trading positions."""
    conditions = []
    
    if status == "open":
        conditions.append(Position.is_open == True)
    elif status == "closed":
        conditions.append(Position.is_open == False)
    
    stmt = select(Position)
    if conditions:
        from sqlalchemy import and_
        stmt = stmt.where(and_(*conditions))
    
    stmt = stmt.order_by(Position.opened_at.desc())
    result = await session.execute(stmt)
    positions = result.scalars().all()
    
    return {
        "positions": [
            {
                "position_id": p.position_id,
                "exchange_id": p.exchange_id,
                "symbol_id": p.symbol_id,
                "side": p.side.value,
                "quantity": p.quantity,
                "remaining_quantity": p.remaining_quantity,
                "signal_entry": p.signal_entry,
                "actual_entry": p.actual_entry,
                "sl_price": p.sl_price,
                "tp1_price": p.tp1_price,
                "tp2_price": p.tp2_price,
                "tp3_price": p.tp3_price,
                "tp1_hit": p.tp1_hit,
                "tp2_hit": p.tp2_hit,
                "tp3_hit": p.tp3_hit,
                "breakeven_active": p.breakeven_active,
                "trailing_active": p.trailing_active,
                "is_open": p.is_open,
                "realized_pnl": p.realized_pnl,
                "unrealized_pnl": p.unrealized_pnl,
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                "exit_reason": p.exit_reason
            }
            for p in positions
        ]
    }


# ============== Orders ==============

@app.get("/orders")
async def get_orders(
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session)
):
    """Get recent orders."""
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    orders = result.scalars().all()
    
    return {
        "orders": [
            {
                "order_id": o.order_id,
                "client_order_id": o.client_order_id,
                "exchange_order_id": o.exchange_order_id,
                "symbol_id": o.symbol_id,
                "exchange_id": o.exchange_id,
                "side": o.side,
                "order_type": o.order_type,
                "quantity": o.quantity,
                "price": o.price,
                "avg_fill_price": o.avg_fill_price,
                "filled_quantity": o.filled_quantity,
                "status": o.status.value,
                "fees": o.fees,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                "filled_at": o.filled_at.isoformat() if o.filled_at else None
            }
            for o in orders
        ]
    }


# ============== Signals ==============

@app.get("/signals")
async def get_signals(
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session)
):
    """Get recent trading signals."""
    stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    signals = result.scalars().all()
    
    return {
        "signals": [
            {
                "signal_id": s.signal_id,
                "symbol_id": s.symbol_id,
                "exchange_id": s.exchange_id,
                "timeframe": s.timeframe,
                "action": s.action,
                "entry_price": s.entry_price,
                "sl_price": s.sl_price,
                "tp1_price": s.tp1_price,
                "tp2_price": s.tp2_price,
                "tp3_price": s.tp3_price,
                "regime": s.regime,
                "votes": s.votes,
                "htf_4h": s.htf_4h,
                "htf_1d": s.htf_1d,
                "adx_value": s.adx_value,
                "atr_value": s.atr_value,
                "bos_detected": s.bos_detected,
                "choch_detected": s.choch_detected,
                "status": s.status,
                "processed": s.processed,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }
            for s in signals
        ]
    }


# ============== Risk ==============

@app.get("/risk")
async def get_risk_status(session: AsyncSession = Depends(get_db_session)):
    """Get risk management status."""
    from backend.core.risk.risk_manager import risk_manager
    
    # Get open positions count
    positions_stmt = select(func.count()).select_from(Position).where(Position.is_open == True)
    result = await session.execute(positions_stmt)
    open_positions = result.scalar() or 0
    
    risk_status = risk_manager.get_risk_status()
    risk_status["open_positions"] = open_positions
    
    return risk_status


# ============== Logs ==============

@app.get("/logs")
async def get_logs(
    level: Optional[str] = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session)
):
    """Get system events/logs."""
    conditions = []
    
    if level:
        conditions.append(SystemEvent.severity == level.upper())
    
    stmt = select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(limit)
    if conditions:
        from sqlalchemy import and_
        stmt = stmt.where(and_(*conditions))
    
    result = await session.execute(stmt)
    events = result.scalars().all()
    
    return {
        "logs": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "component": e.component,
                "severity": e.severity,
                "message": e.message,
                "details": e.details,
                "created_at": e.created_at.isoformat() if e.created_at else None
            }
            for e in events
        ]
    }


# ============== Engine Control ==============

class EngineControlResponse(BaseModel):
    success: bool
    message: str
    state: Dict[str, Any]


@app.post("/engine/start", response_model=EngineControlResponse)
async def start_engine():
    """Start the trading engine."""
    if engine_state["running"]:
        return EngineControlResponse(
            success=False,
            message="Engine is already running",
            state=engine_state
        )
    
    engine_state["running"] = True
    engine_state["paused"] = False
    engine_state["emergency_stop"] = False
    engine_state["started_at"] = datetime.utcnow()
    
    logger.info("Engine started via API")
    
    return EngineControlResponse(
        success=True,
        message="Engine started successfully",
        state=engine_state
    )


@app.post("/engine/stop", response_model=EngineControlResponse)
async def stop_engine():
    """Stop the trading engine."""
    if not engine_state["running"]:
        return EngineControlResponse(
            success=False,
            message="Engine is not running",
            state=engine_state
        )
    
    engine_state["running"] = False
    engine_state["paused"] = False
    
    logger.info("Engine stopped via API")
    
    return EngineControlResponse(
        success=True,
        message="Engine stopped successfully",
        state=engine_state
    )


@app.post("/engine/pause", response_model=EngineControlResponse)
async def pause_engine():
    """Pause trading (no new positions)."""
    if not engine_state["running"]:
        return EngineControlResponse(
            success=False,
            message="Engine is not running",
            state=engine_state
        )
    
    engine_state["paused"] = True
    
    logger.info("Engine paused via API")
    
    return EngineControlResponse(
        success=True,
        message="Engine paused - no new positions will be opened",
        state=engine_state
    )


@app.post("/engine/resume", response_model=EngineControlResponse)
async def resume_engine():
    """Resume trading after pause."""
    if not engine_state["running"]:
        return EngineControlResponse(
            success=False,
            message="Engine is not running",
            state=engine_state
        )
    
    engine_state["paused"] = False
    
    logger.info("Engine resumed via API")
    
    return EngineControlResponse(
        success=True,
        message="Engine resumed - trading active",
        state=engine_state
    )


@app.post("/engine/emergency-stop", response_model=EngineControlResponse)
async def emergency_stop(background_tasks: BackgroundTasks):
    """Emergency stop - halt all trading immediately."""
    engine_state["emergency_stop"] = True
    engine_state["paused"] = True
    
    logger.critical("EMERGENCY STOP ACTIVATED via API")
    
    # TODO: Close all positions, cancel all orders
    # This would be handled by the main engine loop
    
    return EngineControlResponse(
        success=True,
        message="EMERGENCY STOP activated - all trading halted",
        state=engine_state
    )


# ============== Strategy Settings ==============

class StrategySettingsSchema(BaseModel):
    structure_period: int = Field(default=20, ge=1)
    confirmation_type: str = Field(default="Body")
    htf1: str = Field(default="4h")
    htf2: str = Field(default="1d")
    adx_th: int = Field(default=20, ge=0)
    adx_trend: int = Field(default=25, ge=0)
    adx_dead: int = Field(default=15, ge=0)
    filter_mode: str = Field(default="2of3")
    vol_mult: float = Field(default=1.5, gt=0)
    use_impulse: bool = Field(default=True)
    impulse_mult: float = Field(default=1.0, gt=0)
    use_range_bounce: bool = Field(default=True)
    bb_lookback: int = Field(default=10, ge=1)
    max_bounces: int = Field(default=2, ge=0)
    min_atr_pct: float = Field(default=0.3, ge=0)
    max_bos_dist_atr: float = Field(default=0.5, ge=0)
    use_cooldown: bool = Field(default=True)
    cooldown_bars: int = Field(default=6, ge=0)
    risk_pct: float = Field(default=1.0, gt=0, le=5.0)
    tp1_pct: int = Field(default=40, gt=0)
    tp2_pct: int = Field(default=30, gt=0)
    tp3_pct: int = Field(default=30, gt=0)
    use_breakeven: bool = Field(default=True)
    use_trail: bool = Field(default=False)
    trailing_offset: float = Field(default=0.25, gt=0)
    
    @field_validator('confirmation_type')
    @classmethod
    def validate_confirmation_type(cls, v):
        if v not in ["Body", "Wick"]:
            raise ValueError("confirmation_type must be 'Body' or 'Wick'")
        return v
    
    @field_validator('filter_mode')
    @classmethod
    def validate_filter_mode(cls, v):
        if v not in ["2of3", "ALL"]:
            raise ValueError("filter_mode must be '2of3' or 'ALL'")
        return v
    
    @field_validator('htf1', 'htf2')
    @classmethod
    def validate_htf(cls, v):
        if v not in ["1h", "2h", "4h", "1d", "1w"]:
            raise ValueError("HTF must be one of: 1h, 2h, 4h, 1d, 1w")
        return v
    
    @model_validator(mode='after')
    def validate_adx_relationships(self):
        if self.adx_trend <= self.adx_dead:
            raise ValueError("adx_trend must be greater than adx_dead")
        return self
    
    @model_validator(mode='after')
    def validate_tp_percentages(self):
        total = self.tp1_pct + self.tp2_pct + self.tp3_pct
        if total != 100:
            raise ValueError(f"TP percentages must sum to 100. Got: {total}")
        return self


@app.get("/strategy/settings")
async def get_strategy_settings(session: AsyncSession = Depends(get_db_session)):
    """Get current strategy settings."""
    stmt = select(StrategySettings).where(StrategySettings.is_active == True)
    result = await session.execute(stmt)
    settings = result.scalars().first()
    
    if not settings:
        # Return defaults
        return {
            "settings": {
                "structure_period": 20,
                "confirmation_type": "Body",
                "htf1": "4h",
                "htf2": "1d",
                "adx_th": 20,
                "adx_trend": 25,
                "adx_dead": 15,
                "filter_mode": "2of3",
                "vol_mult": 1.5,
                "use_impulse": True,
                "impulse_mult": 1.0,
                "use_range_bounce": True,
                "bb_lookback": 10,
                "max_bounces": 2,
                "min_atr_pct": 0.3,
                "max_bos_dist_atr": 0.5,
                "use_cooldown": True,
                "cooldown_bars": 6,
                "risk_pct": 1.0,
                "tp1_pct": 40,
                "tp2_pct": 30,
                "tp3_pct": 30,
                "use_breakeven": True,
                "use_trail": False,
                "trailing_offset": 0.25
            },
            "version": 0
        }
    
    return {
        "settings": {
            "structure_period": settings.structure_period,
            "confirmation_type": settings.confirmation_type,
            "htf1": settings.htf1,
            "htf2": settings.htf2,
            "adx_th": settings.adx_th,
            "adx_trend": settings.adx_trend,
            "adx_dead": settings.adx_dead,
            "filter_mode": settings.filter_mode,
            "vol_mult": settings.vol_mult,
            "use_impulse": settings.use_impulse,
            "impulse_mult": settings.impulse_mult,
            "use_range_bounce": settings.use_range_bounce,
            "bb_lookback": settings.bb_lookback,
            "max_bounces": settings.max_bounces,
            "min_atr_pct": settings.min_atr_pct,
            "max_bos_dist_atr": settings.max_bos_dist_atr,
            "use_cooldown": settings.use_cooldown,
            "cooldown_bars": settings.cooldown_bars,
            "risk_pct": settings.risk_pct,
            "tp1_pct": settings.tp1_pct,
            "tp2_pct": settings.tp2_pct,
            "tp3_pct": settings.tp3_pct,
            "use_breakeven": settings.use_breakeven,
            "use_trail": settings.use_trail,
            "trailing_offset": settings.trailing_offset
        },
        "version": settings.version,
        "updated_at": settings.updated_at.isoformat() if settings.updated_at else None
    }


@app.post("/strategy/settings")
async def update_strategy_settings(
    new_settings: StrategySettingsSchema,
    session: AsyncSession = Depends(get_db_session)
):
    """Update strategy settings."""
    
    # Validate TP percentages sum to 100
    if new_settings.tp1_pct + new_settings.tp2_pct + new_settings.tp3_pct != 100:
        raise HTTPException(
            status_code=400,
            detail=f"TP percentages must sum to 100. Got: {new_settings.tp1_pct + new_settings.tp2_pct + new_settings.tp3_pct}"
        )
    
    # Get current settings
    stmt = select(StrategySettings).where(StrategySettings.is_active == True)
    result = await session.execute(stmt)
    current_settings = result.scalars().first()
    
    if current_settings:
        # Update existing settings and log changes
        changes = []
        
        fields_to_check = [
            'structure_period', 'confirmation_type', 'htf1', 'htf2',
            'adx_th', 'adx_trend', 'adx_dead', 'filter_mode', 'vol_mult',
            'use_impulse', 'impulse_mult', 'use_range_bounce', 'bb_lookback',
            'max_bounces', 'min_atr_pct', 'max_bos_dist_atr', 'use_cooldown',
            'cooldown_bars', 'risk_pct', 'tp1_pct', 'tp2_pct', 'tp3_pct',
            'use_breakeven', 'use_trail', 'trailing_offset'
        ]
        
        for field_name in fields_to_check:
            old_val = getattr(current_settings, field_name)
            new_val = getattr(new_settings, field_name)
            if old_val != new_val:
                changes.append({
                    "parameter": field_name,
                    "old_value": str(old_val),
                    "new_value": str(new_val)
                })
                setattr(current_settings, field_name, new_val)
        
        current_settings.version += 1
        current_settings.updated_at = datetime.utcnow()
        
        # Log history
        from backend.core.persistence.models import StrategySettingsHistory
        for change in changes:
            history = StrategySettingsHistory(
                settings_id=current_settings.id,
                parameter=change["parameter"],
                old_value=change["old_value"],
                new_value=change["new_value"],
                changed_by="api"
            )
            session.add(history)
        
        await session.commit()
        
        logger.info(f"Strategy settings updated: {len(changes)} changes, version {current_settings.version}")
        
        return {
            "success": True,
            "message": f"Settings updated ({len(changes)} changes)",
            "version": current_settings.version,
            "changes": changes
        }
    else:
        # Create new settings
        settings = StrategySettings(**new_settings.dict())
        settings.version = 1
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
        
        logger.info(f"Strategy settings created, version {settings.version}")
        
        return {
            "success": True,
            "message": "Settings created",
            "version": settings.version,
            "changes": []
        }


@app.post("/strategy/settings/reset")
async def reset_strategy_settings(session: AsyncSession = Depends(get_db_session)):
    """Reset strategy settings to defaults."""
    
    # Deactivate current settings
    stmt = select(StrategySettings).where(StrategySettings.is_active == True)
    result = await session.execute(stmt)
    current = result.scalars().first()
    
    if current:
        current.is_active = False
        await session.commit()
    
    # Create default settings
    defaults = StrategySettings(
        structure_period=20,
        confirmation_type="Body",
        htf1="4h",
        htf2="1d",
        adx_th=20,
        adx_trend=25,
        adx_dead=15,
        filter_mode="2of3",
        vol_mult=1.5,
        use_impulse=True,
        impulse_mult=1.0,
        use_range_bounce=True,
        bb_lookback=10,
        max_bounces=2,
        min_atr_pct=0.3,
        max_bos_dist_atr=0.5,
        use_cooldown=True,
        cooldown_bars=6,
        risk_pct=1.0,
        tp1_pct=40,
        tp2_pct=30,
        tp3_pct=30,
        use_breakeven=True,
        use_trail=False,
        trailing_offset=0.25,
        version=1,
        is_active=True
    )
    
    session.add(defaults)
    await session.commit()
    await session.refresh(defaults)
    
    logger.info("Strategy settings reset to defaults")
    
    return {
        "success": True,
        "message": "Settings reset to defaults",
        "version": defaults.version
    }


@app.get("/strategy/settings/history")
async def get_settings_history(
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session)
):
    """Get strategy settings change history."""
    from backend.core.persistence.models import StrategySettingsHistory
    
    stmt = select(StrategySettingsHistory).order_by(
        StrategySettingsHistory.created_at.desc()
    ).limit(limit)
    
    result = await session.execute(stmt)
    history = result.scalars().all()
    
    return {
        "history": [
            {
                "id": h.id,
                "settings_id": h.settings_id,
                "parameter": h.parameter,
                "old_value": h.old_value,
                "new_value": h.new_value,
                "changed_by": h.changed_by,
                "reason": h.reason,
                "created_at": h.created_at.isoformat() if h.created_at else None
            }
            for h in history
        ]
    }



# ============== WebSocket Endpoint ==============

@app.websocket("/ws")
async def websocket_endpoint_handler(websocket: WebSocket, channels: str = "*"):
    """WebSocket endpoint for real-time dashboard updates."""
    await websocket_service.manager.connect(websocket, [channels] if channels != "*" else ["*"])
    
    try:
        while True:
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                action = message.get("action")
                
                if action == "subscribe":
                    channels_to_sub = message.get("channels", [])
                    websocket_service.manager.subscribe(websocket, channels_to_sub)
                    await websocket_service.manager.send_personal(
                        websocket,
                        {"type": "subscribed", "channels": channels_to_sub}
                    )
                    
                elif action == "unsubscribe":
                    channels_to_unsub = message.get("channels", [])
                    websocket_service.manager.unsubscribe(websocket, channels_to_unsub)
                    
                elif action == "ping":
                    await websocket_service.manager.send_personal(
                        websocket,
                        {"type": "pong", "timestamp": datetime.utcnow().isoformat()}
                    )
                    
            except json.JSONDecodeError:
                pass
                
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        websocket_service.manager.disconnect(websocket)


def setup_api():
    """Setup and configure the API."""
    logger.info("API configured successfully with WebSocket support")
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
