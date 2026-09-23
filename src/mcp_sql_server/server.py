"""MCP SQL Server - Main MCPServer implementation."""

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from dotenv import dotenv_values
from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import DEFAULT_ENV_PATH, describe_config_error
from .database import DatabaseManager
from .logging_config import setup_logging, with_request_id
from .registry import DatabaseRegistry

# Import tools and resources for registration
from .tools import (
    describe_table,
    execute_procedure,
    execute_query,
    execute_query_file,
    execute_statement,
    get_function_definition,
    get_view_definition,
    list_databases,
    list_procedures,
    list_tables,
)
from .resources import (
    resource_database_info,
    resource_databases,
    resource_functions,
    resource_pool_stats,
    resource_tables,
)

# Logging is configured in main(). MCPServer's constructor installs the
# SDK's default stderr handler at import; setup_logging() replaces it.
logger = logging.getLogger(__name__)

# Global database registry with thread-safe initialization
_registry: DatabaseRegistry | None = None
_registry_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: MCPServer) -> AsyncIterator[None]:
    """Manage server lifecycle - cleanup on shutdown."""
    yield
    # Shutdown cleanup
    global _registry
    if _registry:
        _registry.close()
        logger.info("Database registry closed")


# Initialize the MCP server.
# Only `name` is passed positionally: MCPServer's positional order is
# (name, title, description, instructions, ...), so a positional argument
# after the name silently lands in `title` instead.
mcp = MCPServer(
    "MCP SQL Server",
    version=__version__,
    dependencies=["pyodbc", "python-dotenv", "pydantic"],
    lifespan=lifespan,
)


def get_registry() -> DatabaseRegistry:
    """Get database registry, initializing if needed (thread-safe)."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = DatabaseRegistry.from_env()
                logger.info(
                    f"Database registry initialized with: "
                    f"{', '.join(_registry.list_databases())}"
                )
    return _registry


def get_db(database: str = "default") -> DatabaseManager:
    """Get database manager for the named database (thread-safe).

    Args:
        database: Database alias name (default: "default").

    Returns:
        The DatabaseManager for the named database.
    """
    return get_registry().get(database)


# Register tools
@mcp.tool()
@with_request_id
def _execute_query(
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
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with 'columns', 'rows', 'row_count', and 'truncated' keys
    """
    return execute_query(sql, params, limit, database=database)


@mcp.tool()
@with_request_id
def _execute_statement(
    sql: str,
    params: list[str] | None = None,
    database: str = "default",
) -> dict[str, Any]:
    """
    Execute a data modification statement (INSERT, UPDATE, DELETE).

    Args:
        sql: The SQL statement to execute
        params: Optional list of parameter values for ? placeholders
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with 'affected_rows' count and 'success' status
    """
    return execute_statement(sql, params, database=database)


@mcp.tool()
@with_request_id
def _execute_query_file(
    filename: str,
    database: str = "default",
) -> dict[str, Any]:
    """
    Execute a SQL query from the query/ folder in the repository.

    Args:
        filename: Name of the .sql file in the query/ directory
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with query results
    """
    return execute_query_file(filename, database=database)


@mcp.tool()
@with_request_id
def _list_tables(
    schema: str | None = None,
    database: str = "default",
) -> dict[str, Any]:
    """
    List all tables in the database, optionally filtered by schema.

    Args:
        schema: Optional schema name to filter by
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with list of table names and their schemas
    """
    return list_tables(schema, database=database)


@mcp.tool()
@with_request_id
def _describe_table(
    table_name: str,
    schema: str = "dbo",
    database: str = "default",
) -> dict[str, Any]:
    """
    Get detailed column information for a table.

    Args:
        table_name: Name of the table to describe
        schema: Schema name (default: dbo)
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with column definitions
    """
    return describe_table(table_name, schema, database=database)


@mcp.tool()
@with_request_id
def _get_view_definition(
    view_name: str,
    schema: str = "dbo",
    database: str = "default",
) -> dict[str, Any]:
    """
    Get the SQL definition of a database view.

    Args:
        view_name: Name of the view
        schema: Schema name (default: dbo)
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with view definition
    """
    return get_view_definition(view_name, schema, database=database)


@mcp.tool()
@with_request_id
def _get_function_definition(
    function_name: str,
    schema: str = "dbo",
    database: str = "default",
) -> dict[str, Any]:
    """
    Get the SQL definition of a user-defined function.

    Args:
        function_name: Name of the function
        schema: Schema name (default: dbo)
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with function definition
    """
    return get_function_definition(function_name, schema, database=database)


@mcp.tool()
@with_request_id
def _list_procedures(
    schema: str | None = None,
    database: str = "default",
) -> dict[str, Any]:
    """
    List all stored procedures in the database.

    Args:
        schema: Optional schema filter
        database: Target database connection name (default: "default")

    Returns:
        List of procedure metadata
    """
    return list_procedures(schema, database=database)


@mcp.tool()
@with_request_id
def _execute_procedure(
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
        database: Target database connection name (default: "default")

    Returns:
        Dictionary with result sets
    """
    return execute_procedure(proc_name, schema, params, database=database)


@mcp.tool()
@with_request_id
def _list_databases() -> dict[str, Any]:
    """
    List all configured database connections.

    Returns:
        Dictionary with configured database names and their connection info
    """
    return list_databases()


# Register resources
@mcp.resource("sqlserver://tables")
def _resource_tables() -> str:
    """List all tables in the database."""
    return resource_tables()


@mcp.resource("sqlserver://database/info")
def _resource_database_info() -> str:
    """Get database metadata and version information."""
    return resource_database_info()


@mcp.resource("sqlserver://functions")
def _resource_functions() -> str:
    """List all user-defined functions."""
    return resource_functions()


@mcp.resource("sqlserver://pool/stats")
def _resource_pool_stats() -> str:
    """Get connection pool statistics."""
    return resource_pool_stats()


@mcp.resource("sqlserver://databases")
def _resource_databases() -> str:
    """List all configured database connections."""
    return resource_databases()


def _logging_settings() -> tuple[str | None, str | None]:
    """LOG_LEVEL/LOG_FORMAT from the process env, else from .env.

    Reads .env without modifying os.environ; no loader in this package ever
    merges .env into the process environment.
    """
    file_values = dotenv_values(DEFAULT_ENV_PATH)  # {} when the file is missing
    level = os.environ.get("LOG_LEVEL") or file_values.get("LOG_LEVEL")
    log_format = os.environ.get("LOG_FORMAT") or file_values.get("LOG_FORMAT")
    return level, log_format


def _report_config_errors() -> None:
    """Log invalid database configs at startup without stopping the server."""
    try:
        registry = get_registry()
    except ValueError as e:
        logger.error("Database configuration invalid: %s", describe_config_error(e))
        return
    for name, message in registry.config_errors.items():
        logger.error("Database '%s' configuration invalid: %s", name, message)


def main() -> None:
    """Entry point for the MCP server."""
    setup_logging(*_logging_settings())
    _report_config_errors()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
