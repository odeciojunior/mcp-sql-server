"""Database information resources for MCP server."""

import logging
from typing import Any

from ..utils import get_db as _get_db, get_registry as _get_registry

logger = logging.getLogger(__name__)

# Lazy import for list_tables to avoid circular dependency
_list_tables = None


def _get_list_tables() -> Any:
    """Get list_tables function lazily."""
    global _list_tables
    if _list_tables is None:
        from ..tools.schema_discovery import list_tables
        _list_tables = list_tables
    return _list_tables


def resource_tables(database: str = "default") -> str:
    """List all tables in the database.

    Args:
        database: Target database alias name (default: "default").
    """
    list_tables_fn = _get_list_tables()
    result = list_tables_fn(database=database)
    if not result.get("success"):
        return f"Error: {result.get('error', 'Unknown error')}"

    if not result["tables"]:
        return "# Database Tables\n\nNo tables found in database."

    lines = ["# Database Tables\n"]
    current_schema = None
    for table in result["tables"]:
        if table["schema"] != current_schema:
            current_schema = table["schema"]
            lines.append(f"\n## Schema: {current_schema}\n")
        lines.append(f"- {table['name']} ({table['type']})")

    return "\n".join(lines)


def resource_database_info(database: str = "default") -> str:
    """Get database metadata and version information.

    Args:
        database: Target database alias name (default: "default").
    """
    sql = """
    SELECT
        @@VERSION as version,
        DB_NAME() as database_name,
        SERVERPROPERTY('Collation') as collation,
        SERVERPROPERTY('Edition') as edition
    """
    try:
        results = _get_db(database).execute_query(sql)
        if results:
            info = results[0]
            return f"""# Database Information

**Database:** {info['database_name']}
**Edition:** {info['edition']}
**Collation:** {info['collation']}

**Version:**
{info['version']}
"""
    except Exception as e:
        return f"Error retrieving database information: {e}"

    return "Error retrieving database information"


def resource_functions(database: str = "default") -> str:
    """List all user-defined functions.

    Args:
        database: Target database alias name (default: "default").
    """
    sql = """
    SELECT
        ROUTINE_SCHEMA as [schema],
        ROUTINE_NAME as name,
        DATA_TYPE as return_type
    FROM INFORMATION_SCHEMA.ROUTINES
    WHERE ROUTINE_TYPE = 'FUNCTION'
    ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
    """
    try:
        results = _get_db(database).execute_query(sql)

        if not results:
            return "# User-Defined Functions\n\nNo functions found in database."

        lines = ["# User-Defined Functions\n"]
        current_schema = None
        for func in results:
            if func["schema"] != current_schema:
                current_schema = func["schema"]
                lines.append(f"\n## Schema: {current_schema}\n")
            return_type = func.get("return_type") or "TABLE"
            lines.append(f"- {func['name']} -> {return_type}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing functions: {e}"


def resource_pool_stats(database: str = "default") -> str:
    """Get connection pool statistics.

    Args:
        database: Target database alias name (default: "default").

    Returns metrics about the connection pool including:
    - Total connections created
    - Current pool size
    - Connections in use
    - Available connections
    - Peak concurrent usage
    """
    try:
        db = _get_db(database)
        stats = db.pool_stats

        if stats is None:
            return "# Connection Pool Statistics\n\nConnection pooling is not enabled."

        lines = [
            "# Connection Pool Statistics\n",
            "| Metric | Value |",
            "|--------|-------|",
        ]

        # Format stats as a markdown table
        # These labels map to the actual fields returned by ConnectionPool.stats()
        stat_labels = {
            "total_connections": "Total Connections Created",
            "pool_size": "Pool Size (Max)",
            "in_use": "Connections In Use",
            "available": "Available Connections",
            "peak_usage": "Peak Concurrent Usage",
            "total_acquisitions": "Total Acquisitions",
            "total_releases": "Total Releases",
            "failed_acquisitions": "Failed Acquisitions",
            "health_checks": "Health Checks Performed",
            "max_size": "Maximum Pool Size",
            "min_size": "Minimum Pool Size",
            "closed": "Pool Closed",
        }

        # Output stats in a specific order for readability
        ordered_keys = [
            "total_connections",
            "pool_size",
            "in_use",
            "available",
            "peak_usage",
            "total_acquisitions",
            "total_releases",
            "failed_acquisitions",
            "health_checks",
            "min_size",
            "max_size",
            "closed",
        ]

        # First, output stats in preferred order
        for key in ordered_keys:
            if key in stats:
                label = stat_labels.get(key, key.replace("_", " ").title())
                lines.append(f"| {label} | {stats[key]} |")

        # Add any additional stats not in our predefined order
        for key, value in stats.items():
            if key not in ordered_keys:
                label = stat_labels.get(key, key.replace("_", " ").title())
                lines.append(f"| {label} | {value} |")

        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving pool statistics: {e}"


def resource_databases() -> str:
    """List all configured database connections."""
    try:
        registry = _get_registry()
        databases = registry.get_database_info()

        if not databases:
            return "# Configured Databases\n\nNo databases configured."

        lines = [
            "# Configured Databases\n",
            "| Name | Host | Port | Database |",
            "|------|------|------|----------|",
        ]
        for db in databases:
            lines.append(f"| {db['name']} | {db['host']} | {db['port']} | {db['database']} |")

        return "\n".join(lines)
    except Exception as e:
        return f"Error listing databases: {e}"
