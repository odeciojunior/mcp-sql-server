"""MCP Resources for SQL Server database information."""

from .database_info import (
    resource_database_info,
    resource_databases,
    resource_functions,
    resource_pool_stats,
    resource_tables,
)

ALL_RESOURCES = [
    ("sqlserver://tables", resource_tables),
    ("sqlserver://database/info", resource_database_info),
    ("sqlserver://functions", resource_functions),
    ("sqlserver://pool/stats", resource_pool_stats),
    ("sqlserver://databases", resource_databases),
]

__all__ = [
    "resource_tables",
    "resource_database_info",
    "resource_functions",
    "resource_pool_stats",
    "resource_databases",
    "ALL_RESOURCES",
]
