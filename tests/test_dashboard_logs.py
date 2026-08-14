"""Tests for the /dashboard/logs endpoint and log capture mechanism."""
import logging
import time

from fastapi.testclient import TestClient

from backend.utils.logger import get_log_buffer, _DashboardLogHandler


class TestLogBuffer:
    """Tests for the in-memory log buffer."""

    def test_log_buffer_is_deque(self):
        from collections import deque
        buf = get_log_buffer()
        assert isinstance(buf, deque)

    def test_log_buffer_has_maxlen(self):
        buf = get_log_buffer()
        assert buf.maxlen == 500

    def test_log_buffer_captures_records(self):
        buf = get_log_buffer()
        initial_len = len(buf)
        test_logger = logging.getLogger("test.dashboard_logs.capture")
        test_logger.warning("test capture message")
        time.sleep(0.05)
        assert len(buf) > initial_len

    def test_log_buffer_record_has_required_fields(self):
        buf = get_log_buffer()
        test_logger = logging.getLogger("test.dashboard_logs.fields")
        test_logger.info("field check message")
        time.sleep(0.05)
        for record in buf:
            assert "time" in record
            assert "level" in record
            assert "logger" in record
            assert "message" in record
            break

    def test_log_buffer_record_level_lowercase(self):
        buf = get_log_buffer()
        test_logger = logging.getLogger("test.dashboard_logs.level")
        test_logger.error("level check error")
        time.sleep(0.05)
        for record in buf:
            if "level check error" in record["message"]:
                assert record["level"] == "error"
                break

    def test_log_buffer_maxlen_enforced(self):
        from collections import deque
        buf = deque(maxlen=3)
        for i in range(10):
            buf.appendleft({"msg": f"msg{i}"})
        assert len(buf) == 3
        assert buf[0]["msg"] == "msg9"

    def test_log_buffer_does_not_duplicate(self):
        buf = get_log_buffer()
        initial_len = len(buf)
        test_logger = logging.getLogger("test.dashboard_logs.dedup")
        test_logger.info("unique dedup message 12345")
        time.sleep(0.05)
        count = sum(1 for r in buf if "unique dedup message 12345" in r["message"])
        assert count == 1


class TestDashboardLogsEndpoint:
    """Tests for GET /dashboard/logs endpoint."""

    def test_dashboard_logs_returns_200(self):
        from backend.main import app
        with TestClient(app) as client:
            resp = client.get("/dashboard/logs")
        assert resp.status_code == 200

    def test_dashboard_logs_returns_json(self):
        from backend.main import app
        with TestClient(app) as client:
            resp = client.get("/dashboard/logs")
        data = resp.json()
        assert isinstance(data, dict)
        assert "logs" in data
        assert isinstance(data["logs"], list)

    def test_dashboard_logs_entry_has_required_fields(self):
        from backend.main import app
        with TestClient(app) as client:
            resp = client.get("/dashboard/logs")
        data = resp.json()
        if data["logs"]:
            entry = data["logs"][0]
            assert "time" in entry
            assert "level" in entry
            assert "logger" in entry
            assert "message" in entry

    def test_dashboard_logs_contains_level_values(self):
        from backend.main import app
        with TestClient(app) as client:
            resp = client.get("/dashboard/logs")
        data = resp.json()
        valid_levels = {"debug", "info", "warning", "error", "critical"}
        for entry in data["logs"]:
            assert entry["level"] in valid_levels

    def test_dashboard_logs_contains_real_backend_logs(self):
        from backend.main import app
        test_logger = logging.getLogger("test.backend.logs.real")
        test_logger.warning("real backend log test xyzzy")
        time.sleep(0.1)
        with TestClient(app) as client:
            resp = client.get("/dashboard/logs")
        data = resp.json()
        messages = [e["message"] for e in data["logs"]]
        assert any("real backend log test xyzzy" in m for m in messages)

    def test_dashboard_logs_multiple_entries_ordered(self):
        from backend.main import app
        test_logger = logging.getLogger("test.backend.logs.order")
        test_logger.info("order test first")
        test_logger.info("order test second")
        time.sleep(0.1)
        with TestClient(app) as client:
            resp = client.get("/dashboard/logs")
        data = resp.json()
        messages = [e["message"] for e in data["logs"]]
        idx_first = next((i for i, m in enumerate(messages) if "order test first" in m), None)
        idx_second = next((i for i, m in enumerate(messages) if "order test second" in m), None)
        assert idx_first is not None and idx_second is not None
        assert idx_second < idx_first

    def test_dashboard_logs_limit_enforced(self):
        from backend.main import app
        from backend.utils.logger import get_log_buffer
        buf = get_log_buffer()
        assert buf.maxlen <= 1000

    def test_dashboard_logs_does_not_affect_events_endpoint(self):
        from backend.main import app
        with TestClient(app) as client:
            logs_resp = client.get("/dashboard/logs")
            events_resp = client.get("/dashboard/events")
        assert logs_resp.status_code == 200
        assert events_resp.status_code == 200
        assert "logs" in logs_resp.json()
        assert "events" in events_resp.json()

    def test_dashboard_logs_time_is_formatted(self):
        from backend.main import app
        with TestClient(app) as client:
            resp = client.get("/dashboard/logs")
        data = resp.json()
        if data["logs"]:
            ts = data["logs"][0]["time"]
            assert isinstance(ts, str)
            assert ":" in ts


class TestDashboardHandler:
    """Tests for the _DashboardLogHandler class."""

    def test_handler_is_logging_handler(self):
        assert issubclass(_DashboardLogHandler, logging.Handler)

    def test_handler_emit_does_not_raise(self):
        handler = _DashboardLogHandler()
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="test.py",
            lineno=1, msg="test message", args=(), exc_info=None,
        )
        handler.emit(record)

    def test_handler_stores_in_buffer(self):
        from backend.utils.logger import _log_buffer
        initial_len = len(_log_buffer)
        handler = _DashboardLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test.handler", level=logging.INFO, pathname="test.py",
            lineno=1, msg="handler test message", args=(), exc_info=None,
        )
        handler.emit(record)
        time.sleep(0.05)
        assert len(_log_buffer) > initial_len
