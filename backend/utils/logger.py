"""Centralised logging configuration for the whole platform."""
import logging
import sys
from collections import deque
from pathlib import Path

from backend.config import settings

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

# In-memory log buffer for dashboard (last 500 records)
_log_buffer: deque = deque(maxlen=500)

_DASHBOARD_HANDLER_ADDED = False


class _DashboardLogHandler(logging.Handler):
    """Logging handler that stores records in an in-memory deque for the dashboard."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            _log_buffer.appendleft({
                "time": record.created,
                "level": level,
                "logger": record.name,
                "message": msg,
            })
        except Exception:
            pass


def get_log_buffer() -> deque:
    """Return the in-memory log buffer. Ensures the dashboard handler is attached."""
    if not _DASHBOARD_HANDLER_ADDED:
        setup_logging()
    root = logging.getLogger()
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return _log_buffer


def setup_logging(log_file: str = "logs/bot_execution.log") -> logging.Logger:
    global _DASHBOARD_HANDLER_ADDED
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        formatter = logging.Formatter(_FORMAT)

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)

        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if not _DASHBOARD_HANDLER_ADDED:
        dashboard_handler = _DashboardLogHandler()
        dashboard_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(dashboard_handler)
        _DASHBOARD_HANDLER_ADDED = True

    return root
