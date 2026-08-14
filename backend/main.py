"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.dashboard import router as dashboard_router
from backend.api.endpoints import (
    router,
    set_state,
    start_engine_state,
    stop_engine_state,
)
from backend.config import settings
from backend.core.strategy.base import StrategyRegistry
from backend.db.session import dispose_engine
from backend.utils.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)
_bot_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task
    registry = StrategyRegistry(settings.STRATEGIES_DIR)
    registry.load_strategies()
    set_state(strategy_registry=registry)
    if settings.AUTO_START_ENGINE:
        await start_engine_state()
        _bot_task = __import__("backend.api.endpoints", fromlist=["_state"])._state.get("engine_task")
    try:
        yield
    finally:
        await stop_engine_state(reason="Завершение процесса")
        _bot_task = None
        set_state(engine_task=None, bot=None)
        try:
            await dispose_engine()
        except Exception:
            logger.exception("Failed to dispose database engine")


app = FastAPI(title=f"{settings.APP_NAME} – SMC Trading Platform", version="1.0.0", lifespan=lifespan)
allowed_origins = [x.strip() for x in settings.CORS_ORIGINS.split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=bool(allowed_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
