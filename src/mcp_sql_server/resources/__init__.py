"""MCP Resources for SQL Server database information."""

from .database_info import (
    resource_database_info,
    resource_databases,
    resource_functions,
    resource_pool_stats,
    resource_tables,
)

__all__ = [
    "resource_tables",
    "resource_database_info",
    "resource_functions",
    "resource_pool_stats",
    "resource_databases",
]
