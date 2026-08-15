"""
ATS-SMT Pro Trading Bot - Main Entry Point.

This module initializes and starts the trading system.
"""

import asyncio
import sys
import signal
from datetime import datetime
from typing import Optional

import structlog

from backend.config.settings import config, get_config, is_live_trading_allowed
from backend.core.strategy.smt_pro import SMTProStrategy, StrategyConfig
from backend.core.exchange.base_adapter import get_adapter, list_available_adapters
from backend.models.schemas import EngineState, TradingMode


# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("ats_smt")


class TradingEngine:
    """
    Main trading engine orchestrator.
    
    Coordinates all components:
    - Market data collection
    - Strategy calculation
    - Risk management
    - Order execution
    - Position management
    """
    
    def __init__(self):
        self.config = get_config()
        self.state = EngineState.STOPPED
        self.started_at: Optional[datetime] = None
        
        # Initialize strategy
        strategy_config = StrategyConfig(
            structure_period=self.config.structure_period,
            adx_period=self.config.adx_period,
            adx_dead=self.config.adx_dead,
            adx_range=self.config.adx_range,
            adx_vote=self.config.adx_vote,
            atr_period=self.config.atr_period,
            min_atr_pct=self.config.min_atr_pct,
            max_bos_dist_atr=self.config.max_bos_dist_atr,
            cooldown_bars=self.config.cooldown_bars,
            impulse_mult=self.config.impulse_mult,
            bb_period=self.config.bb_period,
            bb_stddev=self.config.bb_stddev,
            max_bounces=self.config.max_bounces,
            volume_sma=self.config.volume_sma,
            volume_mult=self.config.volume_mult,
            filter_mode=self.config.filter_mode.value,
            risk_pct=self.config.risk_pct
        )
        self.strategy = SMTProStrategy(strategy_config)
        
        # Exchange adapters
        self.adapters = {}
        
        # State tracking
        self.portfolio_value = 10000.0  # Starting virtual balance for paper mode
        self.active_positions = {}
        self.open_orders = {}
        
        # Shutdown handling
        self.shutdown_event = asyncio.Event()
    
    async def initialize(self) -> bool:
        """Initialize all components."""
        logger.info("engine_initializing", config={
            "trading_mode": self.config.trading_mode.value,
            "exchanges": self.config.exchanges,
            "symbols": self.config.symbols,
            "risk_pct": self.config.risk_pct
        })
        
        # Validate configuration
        if self.config.trading_mode == TradingMode.LIVE:
            if not is_live_trading_allowed():
                logger.error("live_trading_not_enabled", message="LIVE trading requires explicit enablement in config")
                return False
        
        # Initialize exchange adapters
        for exchange_name in self.config.exchanges:
            try:
                adapter_kwargs = {
                    'testnet': self.config.trading_mode == TradingMode.TESTNET
                }
                
                # Add API keys if available
                api_key_attr = f"{exchange_name}_api_key"
                api_secret_attr = f"{exchange_name}_api_secret"
                
                if hasattr(self.config, api_key_attr):
                    api_key = getattr(self.config, api_key_attr)
                    if api_key:
                        adapter_kwargs['api_key'] = api_key
                
                if hasattr(self.config, api_secret_attr):
                    api_secret = getattr(self.config, api_secret_attr)
                    if api_secret:
                        adapter_kwargs['api_secret'] = api_secret
                
                adapter = get_adapter(exchange_name, **adapter_kwargs)
                if adapter:
                    await adapter.connect()
                    self.adapters[exchange_name] = adapter
                    logger.info("exchange_connected", exchange=exchange_name)
                else:
                    logger.warning("exchange_adapter_not_found", exchange=exchange_name)
            
            except Exception as e:
                logger.error("exchange_connection_failed", exchange=exchange_name, error=str(e))
        
        if not self.adapters:
            logger.error("no_exchanges_connected", message="Failed to connect to any exchange")
            return False
        
        logger.info("engine_initialized", adapters=list(self.adapters.keys()))
        return True
    
    async def start(self):
        """Start the trading engine."""
        if self.state == EngineState.RUNNING:
            logger.warning("engine_already_running")
            return
        
        logger.info("engine_starting")
        self.state = EngineState.RUNNING
        self.started_at = datetime.utcnow()
        
        # Start main loop
        await self._main_loop()
    
    async def stop(self, reason: str = "user_requested"):
        """Stop the trading engine."""
        logger.info("engine_stopping", reason=reason)
        self.state = EngineState.STOPPED
        self.shutdown_event.set()
        
        # Disconnect adapters
        for adapter in self.adapters.values():
            try:
                await adapter.disconnect()
            except Exception as e:
                logger.error("adapter_disconnect_error", error=str(e))
        
        logger.info("engine_stopped")
    
    async def _main_loop(self):
        """Main trading loop."""
        logger.info("main_loop_started")
        
        while self.state == EngineState.RUNNING and not self.shutdown_event.is_set():
            try:
                # Process each symbol
                for symbol in self.config.symbols:
                    for exchange, adapter in self.adapters.items():
                        await self._process_symbol(symbol, exchange, adapter)
                
                # Wait before next iteration
                await asyncio.sleep(30)  # Check every 30 seconds
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("main_loop_error", error=str(e))
                await asyncio.sleep(5)
        
        logger.info("main_loop_ended")
    
    async def _process_symbol(self, symbol: str, exchange: str, adapter):
        """Process a single symbol on an exchange."""
        try:
            # Fetch market data
            candles_m30 = await adapter.fetch_ohlcv(symbol, "30m", limit=100)
            candles_4h = await adapter.fetch_ohlcv(symbol, "4h", limit=100)
            candles_1d = await adapter.fetch_ohlcv(symbol, "1d", limit=100)
            
            if len(candles_m30) < 60:  # Need enough data
                return
            
            # Generate signal
            signal = self.strategy.generate_signal(
                symbol=symbol,
                exchange=exchange,
                candles_m30=candles_m30,
                candles_4h=candles_4h,
                candles_1d=candles_1d,
                portfolio_value=self.portfolio_value
            )
            
            if signal and signal.is_valid:
                logger.info("signal_generated", signal={
                    "symbol": signal.symbol,
                    "action": signal.action.value,
                    "entry": signal.entry_price,
                    "sl": signal.stop_loss,
                    "tp1": signal.tp1,
                    "regime": signal.regime.value,
                    "votes": f"{signal.votes}/{signal.votes_required}"
                })
                
                # In paper mode, just log the signal
                # In live mode, would send to order manager
                if self.config.trading_mode == TradingMode.PAPER:
                    logger.info("paper_signal", signal_id=signal.signal_id)
        
        except Exception as e:
            logger.error("process_symbol_error", symbol=symbol, exchange=exchange, error=str(e))
    
    def get_status(self) -> dict:
        """Get current engine status."""
        return {
            "state": self.state.value,
            "mode": self.config.trading_mode.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "uptime_seconds": (datetime.utcnow() - self.started_at).total_seconds() if self.started_at else 0,
            "active_exchanges": list(self.adapters.keys()),
            "symbols": self.config.symbols,
            "portfolio_value": self.portfolio_value,
            "active_positions": len(self.active_positions),
            "open_orders": len(self.open_orders)
        }


async def main():
    """Main entry point."""
    logger.info("ats_smt_starting", version="1.0.0")
    
    engine = TradingEngine()
    
    # Initialize
    if not await engine.initialize():
        logger.error("initialization_failed")
        sys.exit(1)
    
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    
    def signal_handler(sig):
        logger.info("signal_received", signal=sig.name)
        asyncio.create_task(engine.stop(reason=f"signal_{sig.name}"))
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
    
    # Auto-start if configured
    if config.auto_start_engine:
        logger.info("auto_start_enabled")
        await engine.start()
    else:
        logger.info("engine_initialized_manual_start_required")
        logger.info("use_api_to_start", endpoints=[
            "POST /engine/start",
            "POST /engine/stop"
        ])
    
    # Keep running until shutdown
    await engine.shutdown_event.wait()
    
    logger.info("ats_smt_shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
