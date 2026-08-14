"""Async SQLAlchemy session management."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings


def _async_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


DATABASE_URL = _async_url(settings.DATABASE_URL)
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def dispose_engine() -> None:
    await engine.dispose()
