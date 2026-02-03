"""SQL Playground MCP Server - Main FastMCP server implementation."""

import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .database import DatabaseManager
from .registry import DatabaseRegistry

# Import tools and resources for registration
from .tools import (
    ALL_TOOLS,
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
    ALL_RESOURCES,
    resource_database_info,
    resource_databases,
    resource_functions,
    resource_pool_stats,
    resource_tables,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global database registry with thread-safe initialization
_registry: DatabaseRegistry | None = None
_registry_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[None]:
    """Manage server lifecycle - cleanup on shutdown."""
    yield
    # Shutdown cleanup
    global _registry
    if _registry:
        _registry.close()
        logger.info("Database registry closed")


# Initialize FastMCP server
mcp = FastMCP(
    "SQL Playground MCP Server",
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


# Register tools with FastMCP
@mcp.tool()
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
def _list_databases() -> dict[str, Any]:
    """
    List all configured database connections.

    Returns:
        Dictionary with configured database names and their connection info
    """
    return list_databases()


# Register resources with FastMCP
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


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
