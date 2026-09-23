"""Structured logging configuration for MCP server."""

import functools
import json
import logging
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

# Context variable for request tracking
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add request ID if available
        request_id = request_id_var.get()
        if request_id:
            log_data["request_id"] = request_id

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add any extra fields
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        return json.dumps(log_data)


class StandardFormatter(logging.Formatter):
    """Standard text formatter for console output."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


class RequestIdFilter(logging.Filter):
    """Attach the current request ID (or "-") to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def with_request_id(func: Callable[P, R]) -> Callable[P, R]:
    """Run func with a fresh request ID so its log lines can be correlated.

    Apply directly beneath @mcp.tool(). functools.wraps keeps the signature
    the MCP SDK reads to build the tool schema.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        token = request_id_var.set(uuid.uuid4().hex[:12])
        try:
            return func(*args, **kwargs)
        finally:
            request_id_var.reset(token)

    return wrapper


def setup_logging(
    level: str | None = None,
    log_format: str | None = None,
) -> None:
    """Configure logging for the MCP server.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to LOG_LEVEL env var or INFO.
        log_format: Output format ('json' or 'text'). Defaults to LOG_FORMAT env var or 'text'.
    """
    # Get configuration from environment or defaults, normalized so a
    # lowercase/mixed-case value (e.g. LOG_LEVEL=debug) doesn't crash
    # setLevel or silently fall back to text formatting.
    level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    log_format = (log_format or os.getenv("LOG_FORMAT") or "text").lower()

    # Convert level string to logging constant; fall back to INFO if it
    # isn't a valid level name (getattr would otherwise resolve arbitrary
    # attribute names, e.g. a helper function, that aren't int levels).
    numeric_level = getattr(logging, level, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    # Create root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.addFilter(RequestIdFilter())

    # Set formatter based on format preference
    if log_format == "json":
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(StandardFormatter())

    root_logger.addHandler(console_handler)

    # Set levels for noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pyodbc").setLevel(logging.WARNING)
