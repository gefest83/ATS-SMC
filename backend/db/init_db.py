"""Initialize the configured database schema.

Prefers Alembic migrations when available.  Falls back to
``Base.metadata.create_all()`` for backward compatibility with existing
deployments and development environments that have not yet run Alembic.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from sqlalchemy import text

from backend.db.models import Base
from backend.db.session import engine

logger = logging.getLogger(__name__)


async def _run_alembic_upgrade() -> bool:
    """Attempt an ``alembic upgrade head`` and return True on success."""
    try:
        from alembic.config import Config
        from alembic import command

        ini_path = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
        if not os.path.isfile(ini_path):
            logger.info("alembic.ini not found at %s; falling back to create_all", ini_path)
            return False

        alembic_cfg = Config(ini_path)
        # Run Alembic synchronously; the caller already manages the event loop.
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic upgrade head completed successfully")
        return True
    except Exception as exc:
        logger.warning("Alembic upgrade failed (%s); falling back to create_all", exc)
        return False


async def _run_create_all() -> None:
    """Legacy schema creation via SQLAlchemy metadata."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            await conn.execute(text(
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS "
                "position_id UUID REFERENCES positions(id)"
            ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_position_id "
                "ON trades(position_id) WHERE position_id IS NOT NULL"
            ))
    logger.info("Database schema created via create_all()")


async def init_db() -> None:
    """Create or migrate the database schema.

    Tries Alembic first.  If Alembic is not configured or fails, falls back
    to the original ``create_all()`` approach so existing deployments are not
    broken.
    """
    alembic_ok = await _run_alembic_upgrade()
    if not alembic_ok:
        await _run_create_all()
    print("Database schema initialized successfully.")


if __name__ == "__main__":
    asyncio.run(init_db())
