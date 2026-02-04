"""Query execution tools for MCP server."""

import logging
import re
from pathlib import Path
from typing import Any

from ..audit import audit_logger, timed_operation
from ..config import get_query_dir
from ..errors import create_error_response, sanitize_error
from ..security import validate_query
from ..utils import get_db as _get_db

logger = logging.getLogger(__name__)


def _inject_top_clause(sql: str, limit: int) -> str:
    """Inject TOP clause into SELECT statement for server-side limiting.

    Wraps the original query in a subquery with TOP to limit results at the
    database level, avoiding fetching all rows then truncating client-side.

    Args:
        sql: The original SQL query
        limit: Maximum number of rows to return

    Returns:
        Modified SQL with TOP clause applied
    """
    # Fetch limit + 1 to detect if truncation occurred
    fetch_limit = limit + 1
    # Wrap query in a subquery with TOP to limit at database level
    return f"SELECT TOP {fetch_limit} * FROM ({sql}) AS _limited_query"


def execute_query(
    sql: str,
    params: list[str] | None = None,
    limit: int = 1000,
    database: str = "default",
) -> dict[str, Any]:
    """
    Execute a read-only SELECT query against the SQL Server database.

    Args:
        sql: The SELECT SQL query to execute (must start with SELECT or WITH)
        params: Optional list of parameter values for ? placeholders
        limit: Maximum number of rows to return (default: 1000, max: 10000)
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with 'columns', 'rows', 'row_count', and 'truncated' keys
    """
    is_valid, error = validate_query(sql, allow_modifications=False)
    if not is_valid:
        audit_logger.log_validation_failure(sql, error, database=database)
        return {"error": error, "success": False}

    # Clamp limit to reasonable bounds
    limit = max(1, min(limit, 10000))

    # Inject TOP clause to limit results at database level
    limited_sql = _inject_top_clause(sql, limit)

    with timed_operation() as timing:
        try:
            params_tuple = tuple(params) if params else None
            results = _get_db(database).execute_query(limited_sql, params_tuple)

            # Check if we got more than limit (meaning truncation occurred)
            truncated = len(results) > limit
            if truncated:
                results = results[:limit]

            audit_logger.log_query(
                sql=sql,
                duration_ms=timing.get("duration_ms", 0),
                row_count=len(results),
                success=True,
                truncated=truncated,
                database=database,
            )

            return {
                "success": True,
                "columns": list(results[0].keys()) if results else [],
                "rows": results,
                "row_count": len(results),
                "truncated": truncated,
            }
        except Exception as e:
            error_msg = sanitize_error(e)
            logger.error(f"Query execution error: {error_msg}")
            audit_logger.log_query(
                sql=sql,
                duration_ms=timing.get("duration_ms", 0),
                row_count=0,
                success=False,
                error=error_msg,
                database=database,
            )
            return create_error_response(e, context="query")


def execute_statement(
    sql: str,
    params: list[str] | None = None,
    database: str = "default",
) -> dict[str, Any]:
    """
    Execute a data modification statement (INSERT, UPDATE, DELETE).

    Args:
        sql: The SQL statement to execute
        params: Optional list of parameter values for ? placeholders
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with 'affected_rows' count and 'success' status
    """
    is_valid, error = validate_query(sql, allow_modifications=True)
    if not is_valid:
        audit_logger.log_validation_failure(sql, error, database=database)
        return {"error": error, "success": False}

    # Additional check: must be a modification statement
    first_word = sql.strip().upper().split()[0]
    if first_word not in {"INSERT", "UPDATE", "DELETE"}:
        return {"error": "Use execute_query for SELECT statements", "success": False}

    with timed_operation() as timing:
        try:
            params_tuple = tuple(params) if params else None
            affected = _get_db(database).execute_statement(sql, params_tuple)

            audit_logger.log_statement(
                sql=sql,
                statement_type=first_word,
                duration_ms=timing.get("duration_ms", 0),
                affected_rows=affected,
                success=True,
                database=database,
            )

            return {
                "success": True,
                "affected_rows": affected,
            }
        except Exception as e:
            error_msg = sanitize_error(e)
            logger.error(f"Statement execution error: {error_msg}")
            audit_logger.log_statement(
                sql=sql,
                statement_type=first_word,
                duration_ms=timing.get("duration_ms", 0),
                affected_rows=0,
                success=False,
                error=error_msg,
                database=database,
            )
            return create_error_response(e, context="statement")


def execute_query_file(
    filename: str,
    database: str = "default",
) -> dict[str, Any]:
    """
    Execute a SQL query from the query/ folder in the repository.

    Args:
        filename: Name of the .sql file in the query/ directory
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with query results
    """
    # Validate filename
    if not filename.endswith(".sql"):
        filename = f"{filename}.sql"

    # Security: only allow alphanumeric, underscore, dash, and .sql extension
    if not re.match(r"^[a-zA-Z0-9_-]+\.sql$", filename):
        return {"error": "Invalid filename", "success": False}

    # Find the query file with path traversal protection
    query_dir = get_query_dir()
    query_file = (query_dir / filename).resolve()

    # Verify file is within query directory (prevent path traversal)
    try:
        query_file.relative_to(query_dir)
    except ValueError:
        return {"error": "Invalid file path", "success": False}

    if not query_file.exists():
        return {"error": f"Query file not found: {filename}", "success": False}

    try:
        sql = query_file.read_text(encoding="utf-8")
        return execute_query(sql, database=database)
    except Exception as e:
        logger.error(f"Error reading query file: {e}")
        return {"error": str(e), "success": False}
