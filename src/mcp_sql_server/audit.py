"""Audit logging for database operations."""

import hashlib
import logging
import time
from contextlib import contextmanager
from typing import Any, Generator

from .sql_lexer import LexError, TokenKind, tokenize

logger = logging.getLogger(__name__)

_MASKED_KINDS = (TokenKind.STRING, TokenKind.NUMBER)


def _mask_sql(sql: str) -> str | None:
    """Rebuild SQL with literals replaced by '?' and comments removed.

    String and numeric literals, and "double-quoted" tokens (strings under
    QUOTED_IDENTIFIER OFF), become '?'. Any gap between tokens (whitespace or
    comments) becomes one space; adjacent tokens stay adjacent.

    Returns:
        The masked SQL, or None if the SQL cannot be tokenized.
    """
    try:
        tokens = tokenize(sql)
    except LexError:
        return None
    parts: list[str] = []
    prev_end: int | None = None
    for tok in tokens:
        if prev_end is not None and tok.start > prev_end:
            parts.append(" ")
        masked = tok.kind in _MASKED_KINDS or (
            tok.kind is TokenKind.QUOTED_IDENT and tok.text.startswith('"')
        )
        parts.append("?" if masked else tok.text)
        prev_end = tok.end
    return "".join(parts)


def _hash_sql(sql: str) -> str:
    """Fingerprint the query shape for audit logs.

    Hashes the masked SQL, so queries differing only in literals share a
    hash and literal values cannot be brute-forced from it.

    Returns:
        First 16 characters of the SHA-256 hex digest
    """
    masked = _mask_sql(sql)
    # Hash a constant placeholder, never the raw SQL, when masking fails:
    # hashing raw literals would let them be brute-forced from the audit log.
    source = masked if masked is not None else "<unparseable>"
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def _get_sql_preview(sql: str, max_length: int = 100) -> str:
    """Get a masked, truncated preview of SQL for logging."""
    masked = _mask_sql(sql)
    if masked is None:
        return "<unparseable>"
    if len(masked) > max_length:
        return masked[:max_length] + "..."
    return masked


class AuditLogger:
    """Audit logger for tracking database operations."""

    def __init__(self, logger_name: str = "mcp_sql_server.audit"):
        """Initialize the audit logger.

        Args:
            logger_name: Name for the audit logger
        """
        self._logger = logging.getLogger(logger_name)

    def log_query(
        self,
        sql: str,
        duration_ms: float,
        row_count: int,
        success: bool,
        truncated: bool = False,
        error: str | None = None,
        database: str = "default",
    ) -> None:
        """Log a query execution.

        Args:
            sql: The SQL query executed
            duration_ms: Execution time in milliseconds
            row_count: Number of rows returned
            success: Whether the query succeeded
            truncated: Whether results were truncated
            error: Error message if failed
            database: Target database alias name
        """
        log_data = {
            "event": "QUERY_EXECUTED",
            "database": database,
            "sql_hash": _hash_sql(sql),
            "sql_preview": _get_sql_preview(sql),
            "duration_ms": round(duration_ms, 2),
            "row_count": row_count,
            "success": success,
            "truncated": truncated,
        }

        if error:
            log_data["error"] = error
            self._logger.warning("Query failed", extra={"extra_fields": log_data})
        else:
            self._logger.info("Query executed", extra={"extra_fields": log_data})

    def log_statement(
        self,
        sql: str,
        statement_type: str,
        duration_ms: float,
        affected_rows: int,
        success: bool,
        error: str | None = None,
        database: str = "default",
    ) -> None:
        """Log a data modification statement.

        Args:
            sql: The SQL statement executed
            statement_type: Type of statement (INSERT, UPDATE, DELETE)
            duration_ms: Execution time in milliseconds
            affected_rows: Number of rows affected
            success: Whether the statement succeeded
            error: Error message if failed
            database: Target database alias name
        """
        log_data = {
            "event": "STATEMENT_EXECUTED",
            "database": database,
            "sql_hash": _hash_sql(sql),
            "sql_preview": _get_sql_preview(sql),
            "statement_type": statement_type,
            "duration_ms": round(duration_ms, 2),
            "affected_rows": affected_rows,
            "success": success,
        }

        if error:
            log_data["error"] = error
            self._logger.warning("Statement failed", extra={"extra_fields": log_data})
        else:
            # Data modifications logged at WARNING for visibility
            self._logger.warning("Statement executed", extra={"extra_fields": log_data})

    def log_procedure(
        self,
        proc_name: str,
        schema: str,
        duration_ms: float,
        row_count: int,
        success: bool,
        error: str | None = None,
        database: str = "default",
    ) -> None:
        """Log a stored procedure execution.

        Args:
            proc_name: Name of the procedure
            schema: Schema containing the procedure
            duration_ms: Execution time in milliseconds
            row_count: Number of rows returned
            success: Whether the procedure succeeded
            error: Error message if failed
            database: Target database alias name
        """
        log_data = {
            "event": "PROCEDURE_EXECUTED",
            "database": database,
            "procedure": f"{schema}.{proc_name}",
            "duration_ms": round(duration_ms, 2),
            "row_count": row_count,
            "success": success,
        }

        if error:
            log_data["error"] = error
            self._logger.warning("Procedure failed", extra={"extra_fields": log_data})
        else:
            self._logger.info("Procedure executed", extra={"extra_fields": log_data})

    def log_validation_failure(
        self,
        sql: str,
        error: str,
        blocked_keyword: str | None = None,
        database: str = "default",
    ) -> None:
        """Log a SQL validation failure.

        Args:
            sql: The SQL that failed validation
            error: Validation error message
            blocked_keyword: The blocked keyword that caused failure
            database: Target database alias name
        """
        log_data = {
            "event": "VALIDATION_FAILED",
            "database": database,
            "sql_hash": _hash_sql(sql),
            "sql_preview": _get_sql_preview(sql, max_length=50),
            "error": error,
        }

        if blocked_keyword:
            log_data["blocked_keyword"] = blocked_keyword

        self._logger.warning("SQL validation failed", extra={"extra_fields": log_data})


# Global audit logger instance
audit_logger = AuditLogger()


@contextmanager
def timed_operation() -> Generator[dict[str, Any], None, None]:
    """Context manager for timing operations.

    Yields:
        Dictionary that will contain 'duration_ms' after context exits

    Usage:
        with timed_operation() as timing:
            # do something
        print(f"Took {timing['duration_ms']}ms")
    """
    timing: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        yield timing
    finally:
        end = time.perf_counter()
        timing["duration_ms"] = (end - start) * 1000
