"""Tests for logging configuration and request IDs."""

import inspect
import json
import logging

import pytest

from mcp_sql_server import logging_config
from mcp_sql_server.logging_config import (
    RequestIdFilter,
    request_id_var,
    setup_logging,
    with_request_id,
)


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


class TestSetupLogging:
    def test_text_format_to_stderr(self, capsys):
        setup_logging(level="INFO", log_format="text")
        logging.getLogger("t").info("hello")
        err = capsys.readouterr().err
        assert "hello" in err
        assert "[-]" in err
        assert capsys.readouterr().out == ""

    def test_json_format(self, capsys):
        setup_logging(level="INFO", log_format="json")
        logging.getLogger("t").info("hello")
        record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert record["message"] == "hello"
        assert "request_id" not in record

    def test_env_vars(self, capsys, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        monkeypatch.setenv("LOG_FORMAT", "json")
        setup_logging()
        logging.getLogger("t").info("quiet")
        logging.getLogger("t").warning("loud")
        err = capsys.readouterr().err
        assert "quiet" not in err
        assert json.loads(err.strip().splitlines()[-1])["message"] == "loud"

    def test_replaces_existing_handlers(self):
        root = logging.getLogger()
        root.addHandler(logging.NullHandler())
        setup_logging(level="INFO", log_format="text")
        assert len(root.handlers) == 1


class TestRequestId:
    def test_id_set_during_call_and_cleared_after(self):
        seen = []

        @with_request_id
        def tool() -> str:
            seen.append(request_id_var.get())
            return "ok"

        assert tool() == "ok"
        assert seen[0] is not None and len(seen[0]) == 12
        assert request_id_var.get() is None

    def test_cleared_after_exception(self):
        @with_request_id
        def tool() -> None:
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            tool()
        assert request_id_var.get() is None

    def test_each_call_gets_new_id(self):
        @with_request_id
        def tool() -> str | None:
            return request_id_var.get()

        assert tool() != tool()

    def test_signature_preserved(self):
        def original(sql: str, limit: int = 10, database: str = "default") -> dict[str, int]:
            """Doc."""
            return {}

        wrapped = with_request_id(original)
        assert inspect.signature(wrapped) == inspect.signature(original)
        assert wrapped.__doc__ == "Doc."
        assert wrapped.__name__ == "original"

    def test_text_log_includes_id(self, capsys):
        setup_logging(level="INFO", log_format="text")

        @with_request_id
        def tool() -> str | None:
            logging.getLogger("t").info("inside")
            return request_id_var.get()

        rid = tool()
        assert f"[{rid}] inside" in capsys.readouterr().err

    def test_json_log_includes_id(self, capsys):
        setup_logging(level="INFO", log_format="json")

        @with_request_id
        def tool() -> str | None:
            logging.getLogger("t").info("inside")
            return request_id_var.get()

        rid = tool()
        record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert record["request_id"] == rid

    def test_filter_default(self):
        record = logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None)
        RequestIdFilter().filter(record)
        assert record.request_id == "-"


@pytest.mark.parametrize(
    "name",
    ["get_logger", "LoggerAdapter", "get_logger_with_context", "set_request_id", "clear_request_id"],
)
def test_unused_helpers_removed(name):
    assert not hasattr(logging_config, name)
