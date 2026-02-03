"""MCP Tools for SQL Server database interaction."""

from .query_execution import execute_query, execute_query_file, execute_statement
from .schema_discovery import describe_table, list_tables
from .object_definitions import get_function_definition, get_view_definition
from .stored_procedures import execute_procedure, list_procedures
from .registry_tools import list_databases

ALL_TOOLS = [
    execute_query,
    execute_statement,
    execute_query_file,
    list_tables,
    describe_table,
    get_view_definition,
    get_function_definition,
    list_procedures,
    execute_procedure,
    list_databases,
]

__all__ = [
    "execute_query",
    "execute_statement",
    "execute_query_file",
    "list_tables",
    "describe_table",
    "get_view_definition",
    "get_function_definition",
    "list_procedures",
    "execute_procedure",
    "list_databases",
    "ALL_TOOLS",
]
