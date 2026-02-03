"""Stored procedure tools for MCP server."""

import logging
from typing import Any

from ..audit import audit_logger, timed_operation
from ..cache import cached
from ..errors import create_error_response, sanitize_error
from ..security import sanitize_table_name, validate_identifier, validate_procedure_name
from ..utils import get_db as _get_db

logger = logging.getLogger(__name__)

# Cache TTL for metadata queries (in seconds)
METADATA_CACHE_TTL = 60


def list_procedures(
    schema: str | None = None,
    database: str = "default",
) -> dict[str, Any]:
    """
    List all stored procedures in the database.

    Args:
        schema: Optional schema filter
        database: Target database alias name (default: "default")

    Returns:
        List of procedure metadata
    """
    try:
        if schema:
            valid, error = validate_identifier(schema)
            if not valid:
                return {"error": error, "success": False}
            return _list_procedures_cached(schema, database=database)
        else:
            return _list_procedures_cached(None, database=database)
    except Exception as e:
        logger.error(f"Error listing procedures: {e}")
        return {"error": str(e), "success": False}


@cached(ttl=METADATA_CACHE_TTL, key_prefix="list_procedures")
def _list_procedures_cached(
    schema: str | None,
    database: str = "default",
) -> dict[str, Any]:
    """Cached implementation of list_procedures."""
    if schema:
        sql = """
        SELECT
            ROUTINE_SCHEMA as [schema],
            ROUTINE_NAME as name,
            CREATED as created,
            LAST_ALTERED as modified
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE = 'PROCEDURE' AND ROUTINE_SCHEMA = ?
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """
        results = _get_db(database).execute_query(sql, (schema,))
    else:
        sql = """
        SELECT
            ROUTINE_SCHEMA as [schema],
            ROUTINE_NAME as name,
            CREATED as created,
            LAST_ALTERED as modified
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE = 'PROCEDURE'
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """
        results = _get_db(database).execute_query(sql)

    return {"success": True, "procedures": results, "count": len(results)}


def execute_procedure(
    proc_name: str,
    schema: str = "dbo",
    params: dict[str, Any] | None = None,
    database: str = "default",
) -> dict[str, Any]:
    """
    Execute a stored procedure with optional parameters.

    Args:
        proc_name: Name of the stored procedure
        schema: Schema name (default: dbo)
        params: Dictionary of parameter_name: value pairs
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with result sets
    """
    # Validate procedure name isn't a system procedure
    valid, error = validate_procedure_name(proc_name)
    if not valid:
        return {"error": error, "success": False}

    try:
        sanitize_table_name(proc_name, schema)
    except ValueError as e:
        return {"error": str(e), "success": False}

    # Validate parameter names if provided
    if params:
        for param_name in params.keys():
            valid, error = validate_identifier(param_name)
            if not valid:
                return {"error": f"Invalid parameter name '{param_name}': {error}", "success": False}

    with timed_operation() as timing:
        try:
            # Build the EXEC statement with parameters
            full_name = f"[{schema}].[{proc_name}]"

            if params:
                # Build parameterized call
                param_placeholders = ", ".join(f"@{k} = ?" for k in params.keys())
                sql = f"EXEC {full_name} {param_placeholders}"
                param_values = tuple(params.values())
                results = _get_db(database).execute_query(sql, param_values)
            else:
                sql = f"EXEC {full_name}"
                results = _get_db(database).execute_query(sql)

            audit_logger.log_procedure(
                proc_name=proc_name,
                schema=schema,
                duration_ms=timing.get("duration_ms", 0),
                row_count=len(results),
                success=True,
                database=database,
            )

            return {"success": True, "results": results, "row_count": len(results)}
        except Exception as e:
            error_msg = sanitize_error(e)
            logger.error(f"Error executing procedure: {error_msg}")
            audit_logger.log_procedure(
                proc_name=proc_name,
                schema=schema,
                duration_ms=timing.get("duration_ms", 0),
                row_count=0,
                success=False,
                error=error_msg,
                database=database,
            )
            return create_error_response(e, context="procedure")
