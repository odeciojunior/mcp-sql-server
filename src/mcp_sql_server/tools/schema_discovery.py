"""Schema discovery tools for MCP server."""

import logging
from typing import Any

from ..cache import cached
from ..security import sanitize_table_name, validate_identifier
from ..utils import get_db as _get_db

logger = logging.getLogger(__name__)

# Cache TTL for metadata queries (in seconds)
METADATA_CACHE_TTL = 60


def list_tables(
    schema: str | None = None,
    database: str = "default",
) -> dict[str, Any]:
    """
    List all tables in the database, optionally filtered by schema.

    Args:
        schema: Optional schema name to filter by
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with list of table names and their schemas
    """
    try:
        if schema:
            valid, error = validate_identifier(schema)
            if not valid:
                return {"error": error, "success": False}
            return _list_tables_cached(schema, database=database)
        else:
            return _list_tables_cached(None, database=database)
    except Exception as e:
        logger.error(f"Error listing tables: {e}")
        return {"error": str(e), "success": False}


@cached(ttl=METADATA_CACHE_TTL, key_prefix="list_tables")
def _list_tables_cached(
    schema: str | None,
    database: str = "default",
) -> dict[str, Any]:
    """Cached implementation of list_tables."""
    if schema:
        sql = """
        SELECT TABLE_SCHEMA as [schema], TABLE_NAME as name, TABLE_TYPE as type
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ?
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
        results = _get_db(database).execute_query(sql, (schema,))
    else:
        sql = """
        SELECT TABLE_SCHEMA as [schema], TABLE_NAME as name, TABLE_TYPE as type
        FROM INFORMATION_SCHEMA.TABLES
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
        results = _get_db(database).execute_query(sql)

    return {"success": True, "tables": results, "count": len(results)}


def describe_table(
    table_name: str,
    schema: str = "dbo",
    database: str = "default",
) -> dict[str, Any]:
    """
    Get detailed column information for a table.

    Args:
        table_name: Name of the table to describe
        schema: Schema name (default: dbo)
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with column definitions
    """
    try:
        sanitize_table_name(table_name, schema)
    except ValueError as e:
        return {"error": str(e), "success": False}

    try:
        return _describe_table_cached(table_name, schema, database=database)
    except Exception as e:
        logger.error(f"Error describing table: {e}")
        return {"error": str(e), "success": False}


@cached(ttl=METADATA_CACHE_TTL, key_prefix="describe_table")
def _describe_table_cached(
    table_name: str,
    schema: str,
    database: str = "default",
) -> dict[str, Any]:
    """Cached implementation of describe_table."""
    sql = """
    SELECT
        c.COLUMN_NAME as name,
        c.DATA_TYPE as type,
        c.CHARACTER_MAXIMUM_LENGTH as max_length,
        c.NUMERIC_PRECISION as precision,
        c.IS_NULLABLE as nullable,
        c.COLUMN_DEFAULT as default_value
    FROM INFORMATION_SCHEMA.COLUMNS c
    WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ?
    ORDER BY c.ORDINAL_POSITION
    """
    results = _get_db(database).execute_query(sql, (schema, table_name))
    return {"success": True, "columns": results, "table": f"{schema}.{table_name}"}
