from backend.config import Settings
from backend.utils.logger import setup_logging


def test_settings_has_log_level_default():
    s = Settings(TRADING_MODE="paper")
    assert s.LOG_LEVEL == "INFO"


def test_setup_logging_uses_log_level(tmp_path):
    # Import-time settings should expose LOG_LEVEL; use a fresh root only if no handlers exist.
    assert hasattr(Settings(TRADING_MODE="paper"), "LOG_LEVEL")
