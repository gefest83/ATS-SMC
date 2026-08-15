"""Alembic migrations module."""

from alembic import command
from alembic.config import Config
from pathlib import Path


def get_alembic_config():
    """Get Alembic config."""
    return Config(Path(__file__).parent / "alembic.ini")


def upgrade(revision="head"):
    """Upgrade database to revision."""
    cfg = get_alembic_config()
    command.upgrade(cfg, revision)


def downgrade(revision="-1"):
    """Downgrade database by one revision."""
    cfg = get_alembic_config()
    command.downgrade(cfg, revision)


def current():
    """Get current revision."""
    cfg = get_alembic_config()
    command.current(cfg)


def stamp(revision):
    """Stamp database with revision."""
    cfg = get_alembic_config()
    command.stamp(cfg, revision)
