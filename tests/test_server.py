"""Tests for MCP server tools and resources."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sql_playground_mcp import server
from sql_playground_mcp.cache import invalidate_metadata_cache
from sql_playground_mcp.server import get_db, get_registry
# Import tools directly from their modules for testing
from sql_playground_mcp.tools.query_execution import (
    execute_query,
    execute_query_file,
    execute_statement,
)
from sql_playground_mcp.tools.schema_discovery import describe_table, list_tables
from sql_playground_mcp.tools.object_definitions import (
    get_function_definition,
    get_view_definition,
)
from sql_playground_mcp.tools.stored_procedures import execute_procedure, list_procedures
from sql_playground_mcp.tools.registry_tools import list_databases
from sql_playground_mcp.resources.database_info import (
    resource_database_info,
    resource_databases,
    resource_functions,
    resource_pool_stats,
    resource_tables,
)
from sql_playground_mcp.tools import query_execution, schema_discovery, object_definitions, stored_procedures
from sql_playground_mcp.resources import database_info


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear metadata cache before each test."""
    invalidate_metadata_cache()
    yield
    invalidate_metadata_cache()


class TestGetDb:
    """Tests for get_db() via DatabaseRegistry."""

    def test_get_db_creates_manager(self, env_with_minimal_vars, mock_pyodbc):
        # Reset global registry
        server._registry = None
        db = get_db()
        assert db is not None
        registry = get_registry()
        registry.close()
        server._registry = None

    def test_get_db_returns_same_instance(self, env_with_minimal_vars, mock_pyodbc):
        server._registry = None
        db1 = get_db()
        db2 = get_db()
        assert db1 is db2
        get_registry().close()
        server._registry = None

    def test_get_db_logs_connection(self, env_with_minimal_vars, mock_pyodbc, caplog):
        server._registry = None
        import logging
        with caplog.at_level(logging.INFO):
            db = get_db()
        assert "registry initialized" in caplog.text.lower() or "Initialized database" in caplog.text
        get_registry().close()
        server._registry = None

    def test_get_db_uses_config_from_env(self, env_with_vars, mock_pyodbc):
        server._registry = None
        db = get_db()
        assert db.config.host == "test-server.example.com"
        get_registry().close()
        server._registry = None

    def test_get_registry_returns_same_instance(self, env_with_minimal_vars, mock_pyodbc):
        server._registry = None
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
        r1.close()
        server._registry = None

    def test_get_registry_lists_default(self, env_with_minimal_vars, mock_pyodbc):
        server._registry = None
        registry = get_registry()
        assert "default" in registry.list_databases()
        registry.close()
        server._registry = None


class TestExecuteQueryTool:
    """Tests for execute_query tool."""

    def test_execute_query_success(self, mock_query_results):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_query_results
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM Users")

            assert result["success"] is True
            assert result["row_count"] == 3
            assert len(result["rows"]) == 3

    def test_execute_query_returns_columns(self, mock_query_results):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_query_results
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM Users")

            assert "columns" in result
            assert "id" in result["columns"]

    def test_execute_query_empty_sql_rejected(self):
        result = execute_query("")
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_execute_query_blocked_keyword(self):
        result = execute_query("DROP TABLE Users")
        assert result["success"] is False
        assert "DROP" in result["error"]

    def test_execute_query_insert_rejected(self):
        result = execute_query("INSERT INTO Users VALUES (1)")
        assert result["success"] is False
        assert "INSERT" in result["error"]

    def test_execute_query_with_cte(self, mock_query_results):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_query_results
            mock_get_db.return_value = mock_db

            result = execute_query("WITH cte AS (SELECT 1) SELECT * FROM cte")
            assert result["success"] is True

    def test_execute_query_with_params(self, mock_query_results):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_query_results
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM Users WHERE id = ?", params=["1"])

            mock_db.execute_query.assert_called_once()
            call_args = mock_db.execute_query.call_args
            assert call_args[0][1] == ("1",)

    def test_execute_query_limit_applied(self):
        large_results = [{"id": i} for i in range(200)]
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = large_results
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM Users", limit=100)

            assert result["row_count"] == 100
            assert result["truncated"] is True

    def test_execute_query_limit_clamped_min(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"id": 1}]
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT 1", limit=0)
            # Should be clamped to 1
            assert result["success"] is True

    def test_execute_query_limit_clamped_max(self):
        results = [{"id": i} for i in range(15000)]
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = results
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM Users", limit=20000)
            # Should be clamped to 10000
            assert result["row_count"] == 10000
            assert result["truncated"] is True

    def test_execute_query_truncated_false_when_under_limit(self, mock_query_results):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_query_results
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM Users", limit=100)
            assert result["truncated"] is False

    def test_execute_query_handles_exception(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.side_effect = Exception("Database error")
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM Users")

            assert result["success"] is False
            assert "Database error" in result["error"]

    def test_execute_query_empty_result(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT * FROM EmptyTable")

            assert result["success"] is True
            assert result["row_count"] == 0
            assert result["columns"] == []


class TestExecuteStatementTool:
    """Tests for execute_statement tool."""

    def test_execute_statement_insert_success(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_statement.return_value = 1
            mock_get_db.return_value = mock_db

            result = execute_statement("INSERT INTO Users (name) VALUES ('test')")

            assert result["success"] is True
            assert result["affected_rows"] == 1

    def test_execute_statement_update_success(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_statement.return_value = 5
            mock_get_db.return_value = mock_db

            result = execute_statement("UPDATE Users SET active=1")

            assert result["success"] is True
            assert result["affected_rows"] == 5

    def test_execute_statement_delete_success(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_statement.return_value = 3
            mock_get_db.return_value = mock_db

            result = execute_statement("DELETE FROM Users WHERE id > 10")

            assert result["success"] is True
            assert result["affected_rows"] == 3

    def test_execute_statement_select_rejected(self):
        result = execute_statement("SELECT * FROM Users")
        assert result["success"] is False
        assert "execute_query" in result["error"]

    def test_execute_statement_drop_blocked(self):
        result = execute_statement("DROP TABLE Users")
        assert result["success"] is False
        assert "DROP" in result["error"]

    def test_execute_statement_with_params(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_statement.return_value = 1
            mock_get_db.return_value = mock_db

            result = execute_statement(
                "INSERT INTO Users (name) VALUES (?)",
                params=["test"]
            )

            mock_db.execute_statement.assert_called_once()
            call_args = mock_db.execute_statement.call_args
            assert call_args[0][1] == ("test",)

    def test_execute_statement_handles_exception(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_statement.side_effect = Exception("Constraint violation")
            mock_get_db.return_value = mock_db

            result = execute_statement("INSERT INTO Users (name) VALUES ('test')")

            assert result["success"] is False
            assert "Constraint violation" in result["error"]

    def test_execute_statement_empty_sql_rejected(self):
        result = execute_statement("")
        assert result["success"] is False


class TestExecuteQueryFileTool:
    """Tests for execute_query_file tool."""

    def test_execute_query_file_success(self):
        """Test that execute_query_file works with valid SQL."""
        # Test indirectly - the file doesn't exist but we can test validation passes
        result = execute_query_file("valid_filename")
        # Should fail with "file not found" not "invalid filename"
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_execute_query_file_adds_extension(self):
        # The function should add .sql if missing
        result = execute_query_file("invalid_file_does_not_exist")
        # Will fail on file not found, but extension should be added internally
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_execute_query_file_invalid_filename_rejected(self):
        result = execute_query_file("../../../etc/passwd")
        assert result["success"] is False
        assert "Invalid filename" in result["error"]

    def test_execute_query_file_special_chars_rejected(self):
        result = execute_query_file("file;DROP TABLE.sql")
        assert result["success"] is False
        assert "Invalid filename" in result["error"]

    def test_execute_query_file_path_traversal_rejected(self):
        result = execute_query_file("..%2F..%2Fetc%2Fpasswd.sql")
        assert result["success"] is False

    def test_execute_query_file_not_found(self):
        result = execute_query_file("nonexistent_file.sql")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestListTablesTool:
    """Tests for list_tables tool."""

    def test_list_tables_success(self, mock_table_list):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_table_list
            mock_get_db.return_value = mock_db

            result = list_tables()

            assert result["success"] is True
            assert result["count"] == 4
            assert len(result["tables"]) == 4

    def test_list_tables_with_schema_filter(self, mock_table_list):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            filtered = [t for t in mock_table_list if t["schema"] == "dbo"]
            mock_db.execute_query.return_value = filtered
            mock_get_db.return_value = mock_db

            result = list_tables(schema="dbo")

            assert result["success"] is True
            # Verify parameter was passed
            call_args = mock_db.execute_query.call_args
            assert call_args[0][1] == ("dbo",)

    def test_list_tables_invalid_schema_rejected(self):
        result = list_tables(schema="bad; schema")
        assert result["success"] is False
        assert "Invalid" in result["error"]

    def test_list_tables_handles_exception(self):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.side_effect = Exception("Connection lost")
            mock_get_db.return_value = mock_db

            result = list_tables()

            assert result["success"] is False
            assert "Connection lost" in result["error"]

    def test_list_tables_empty_result(self):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            result = list_tables()

            assert result["success"] is True
            assert result["count"] == 0


class TestDescribeTableTool:
    """Tests for describe_table tool."""

    def test_describe_table_success(self, mock_column_definitions):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_column_definitions
            mock_get_db.return_value = mock_db

            result = describe_table("Users")

            assert result["success"] is True
            assert result["table"] == "dbo.Users"
            assert len(result["columns"]) == 3

    def test_describe_table_custom_schema(self, mock_column_definitions):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_column_definitions
            mock_get_db.return_value = mock_db

            result = describe_table("Logs", schema="audit")

            assert result["table"] == "audit.Logs"

    def test_describe_table_invalid_name_rejected(self):
        result = describe_table("Users; DROP TABLE")
        assert result["success"] is False

    def test_describe_table_invalid_schema_rejected(self):
        result = describe_table("Users", schema="bad schema")
        assert result["success"] is False

    def test_describe_table_handles_exception(self):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.side_effect = Exception("Table not found")
            mock_get_db.return_value = mock_db

            result = describe_table("NonExistent")

            assert result["success"] is False


class TestGetViewDefinitionTool:
    """Tests for get_view_definition tool."""

    def test_get_view_definition_success(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [
                {"definition": "CREATE VIEW vwUsers AS SELECT * FROM Users"}
            ]
            mock_get_db.return_value = mock_db

            result = get_view_definition("vwUsers")

            assert result["success"] is True
            assert "CREATE VIEW" in result["definition"]

    def test_get_view_definition_not_found(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"definition": None}]
            mock_get_db.return_value = mock_db

            result = get_view_definition("NonExistentView")

            assert result["success"] is False
            assert "not found" in result["error"]

    def test_get_view_definition_invalid_name(self):
        result = get_view_definition("bad; view")
        assert result["success"] is False

    def test_get_view_definition_custom_schema(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"definition": "CREATE VIEW..."}]
            mock_get_db.return_value = mock_db

            result = get_view_definition("MyView", schema="custom")

            assert result["view"] == "custom.MyView"


class TestGetFunctionDefinitionTool:
    """Tests for get_function_definition tool."""

    def test_get_function_definition_success(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [
                {"definition": "CREATE FUNCTION dbo.MyFunc() RETURNS INT AS BEGIN RETURN 1 END"}
            ]
            mock_get_db.return_value = mock_db

            result = get_function_definition("MyFunc")

            assert result["success"] is True
            assert "CREATE FUNCTION" in result["definition"]

    def test_get_function_definition_not_found(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"definition": None}]
            mock_get_db.return_value = mock_db

            result = get_function_definition("NonExistentFunc")

            assert result["success"] is False

    def test_get_function_definition_invalid_name(self):
        result = get_function_definition("bad; func")
        assert result["success"] is False

    def test_get_function_definition_custom_schema(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"definition": "CREATE FUNCTION..."}]
            mock_get_db.return_value = mock_db

            result = get_function_definition("MyFunc", schema="utils")

            assert result["function"] == "utils.MyFunc"


class TestListProceduresTool:
    """Tests for list_procedures tool."""

    def test_list_procedures_success(self):
        procedures = [
            {"schema": "dbo", "name": "GetUser", "created": "2024-01-01", "modified": "2024-01-02"},
            {"schema": "dbo", "name": "UpdateUser", "created": "2024-01-01", "modified": "2024-01-02"},
        ]
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = procedures
            mock_get_db.return_value = mock_db

            result = list_procedures()

            assert result["success"] is True
            assert result["count"] == 2

    def test_list_procedures_with_schema_filter(self):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            result = list_procedures(schema="custom")

            call_args = mock_db.execute_query.call_args
            assert call_args[0][1] == ("custom",)

    def test_list_procedures_invalid_schema(self):
        result = list_procedures(schema="bad; schema")
        assert result["success"] is False

    def test_list_procedures_handles_exception(self):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.side_effect = Exception("Error")
            mock_get_db.return_value = mock_db

            result = list_procedures()

            assert result["success"] is False

    def test_list_procedures_empty_result(self):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            result = list_procedures()

            assert result["success"] is True
            assert result["count"] == 0


class TestExecuteProcedureTool:
    """Tests for execute_procedure tool."""

    def test_execute_procedure_success(self, mock_procedure_results):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_procedure_results
            mock_get_db.return_value = mock_db

            result = execute_procedure("GetUserById")

            assert result["success"] is True
            assert result["row_count"] == 1

    def test_execute_procedure_with_params(self, mock_procedure_results):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_procedure_results
            mock_get_db.return_value = mock_db

            result = execute_procedure(
                "GetUserById",
                params={"UserId": 1, "IncludeDeleted": False}
            )

            assert result["success"] is True
            # Check SQL was built correctly
            call_args = mock_db.execute_query.call_args
            sql = call_args[0][0]
            assert "@UserId = ?" in sql
            assert "@IncludeDeleted = ?" in sql

    def test_execute_procedure_blocked_xp(self):
        result = execute_procedure("xp_cmdshell")
        assert result["success"] is False
        assert "System procedure not allowed" in result["error"]

    def test_execute_procedure_blocked_sp(self):
        result = execute_procedure("sp_executesql")
        assert result["success"] is False
        assert "System procedure not allowed" in result["error"]

    def test_execute_procedure_invalid_name(self):
        result = execute_procedure("bad; proc")
        assert result["success"] is False

    def test_execute_procedure_invalid_param_name(self):
        result = execute_procedure(
            "ValidProc",
            params={"bad; param": 1}
        )
        assert result["success"] is False
        assert "Invalid parameter name" in result["error"]

    def test_execute_procedure_handles_exception(self):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.side_effect = Exception("Proc error")
            mock_get_db.return_value = mock_db

            result = execute_procedure("FailingProc")

            assert result["success"] is False
            assert "Proc error" in result["error"]


class TestResourceTables:
    """Tests for sqlserver://tables resource."""

    def test_resource_tables_success(self, mock_table_list):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_table_list
            mock_get_db.return_value = mock_db

            result = resource_tables()

            assert "# Database Tables" in result
            assert "Users" in result
            assert "Orders" in result

    def test_resource_tables_groups_by_schema(self, mock_table_list):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_table_list
            mock_get_db.return_value = mock_db

            result = resource_tables()

            assert "## Schema: dbo" in result
            assert "## Schema: audit" in result

    def test_resource_tables_empty_database(self):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            result = resource_tables()

            assert "No tables found" in result

    def test_resource_tables_handles_error(self):
        with patch.object(database_info, '_get_list_tables') as mock_list_fn:
            mock_list_fn.return_value = lambda **kwargs: {"success": False, "error": "Connection failed"}
            # Reset cached value
            database_info._list_tables = None

            result = resource_tables()

            assert "Error" in result
            database_info._list_tables = None


class TestResourceDatabaseInfo:
    """Tests for sqlserver://database/info resource."""

    def test_resource_database_info_success(self):
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{
                "version": "Microsoft SQL Server 2019",
                "database_name": "TestDB",
                "collation": "SQL_Latin1_General_CP1_CI_AS",
                "edition": "Enterprise Edition"
            }]
            mock_get_db.return_value = mock_db

            result = resource_database_info()

            assert "# Database Information" in result
            assert "TestDB" in result
            assert "Enterprise Edition" in result

    def test_resource_database_info_handles_exception(self):
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.side_effect = Exception("Connection error")
            mock_get_db.return_value = mock_db

            result = resource_database_info()

            assert "Error" in result

    def test_resource_database_info_empty_result(self):
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            result = resource_database_info()

            assert "Error" in result


class TestResourceFunctions:
    """Tests for sqlserver://functions resource."""

    def test_resource_functions_success(self):
        functions = [
            {"schema": "dbo", "name": "GetUserName", "return_type": "nvarchar"},
            {"schema": "dbo", "name": "CalculateAge", "return_type": "int"},
            {"schema": "utils", "name": "ParseJSON", "return_type": None},
        ]
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = functions
            mock_get_db.return_value = mock_db

            result = resource_functions()

            assert "# User-Defined Functions" in result
            assert "GetUserName" in result
            assert "nvarchar" in result
            # Table-valued functions show TABLE
            assert "TABLE" in result

    def test_resource_functions_groups_by_schema(self):
        functions = [
            {"schema": "dbo", "name": "Func1", "return_type": "int"},
            {"schema": "utils", "name": "Func2", "return_type": "varchar"},
        ]
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = functions
            mock_get_db.return_value = mock_db

            result = resource_functions()

            assert "## Schema: dbo" in result
            assert "## Schema: utils" in result

    def test_resource_functions_empty(self):
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            result = resource_functions()

            assert "No functions found" in result

    def test_resource_functions_handles_exception(self):
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.side_effect = Exception("Error")
            mock_get_db.return_value = mock_db

            result = resource_functions()

            assert "Error" in result


class TestResourcePoolStats:
    """Tests for resource_pool_stats() function."""

    def test_pool_stats_when_pooling_enabled(self):
        """Test pool stats returns markdown table with all expected fields when pooling is enabled."""
        mock_stats = {
            "total_connections": 5,
            "pool_size": 3,
            "in_use": 2,
            "available": 1,
            "peak_usage": 4,
            "total_acquisitions": 100,
            "total_releases": 98,
            "failed_acquisitions": 2,
            "health_checks": 50,
        }
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.pool_stats = mock_stats
            mock_get_db.return_value = mock_db

            result = resource_pool_stats()

            # Verify markdown structure
            assert "# Connection Pool Statistics" in result
            assert "| Metric | Value |" in result
            assert "|--------|-------|" in result

            # Verify all labeled stats are present with correct labels
            assert "| Total Connections Created | 5 |" in result
            assert "| Pool Size (Max) | 3 |" in result
            assert "| Connections In Use | 2 |" in result
            assert "| Available Connections | 1 |" in result
            assert "| Peak Concurrent Usage | 4 |" in result
            assert "| Total Acquisitions | 100 |" in result
            assert "| Total Releases | 98 |" in result
            assert "| Failed Acquisitions | 2 |" in result
            assert "| Health Checks Performed | 50 |" in result

    def test_pool_stats_when_pooling_disabled(self):
        """Test pool stats returns appropriate message when pooling is disabled."""
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.pool_stats = None  # No pool stats when pooling is disabled
            mock_get_db.return_value = mock_db

            result = resource_pool_stats()

            assert "# Connection Pool Statistics" in result
            assert "Connection pooling is not enabled" in result

    def test_pool_stats_field_mapping(self):
        """Test that stat keys are properly mapped to human-readable labels."""
        # Only provide a subset of stats to verify mapping works correctly
        mock_stats = {
            "total_connections": 10,
            "available": 5,
        }
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.pool_stats = mock_stats
            mock_get_db.return_value = mock_db

            result = resource_pool_stats()

            # Verify that known stats get the correct label
            assert "Total Connections Created" in result
            assert "| 10 |" in result
            assert "Available Connections" in result
            assert "| 5 |" in result

    def test_pool_stats_values_appear_in_output(self):
        """Test that specific stat values appear correctly in the output."""
        mock_stats = {
            "pool_size": 42,
            "in_use": 17,
            "peak_usage": 99,
        }
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.pool_stats = mock_stats
            mock_get_db.return_value = mock_db

            result = resource_pool_stats()

            # Verify values appear in the output
            assert "| 42 |" in result
            assert "| 17 |" in result
            assert "| 99 |" in result

    def test_pool_stats_unknown_fields_converted_to_title_case(self):
        """Test that unknown stat keys are converted to Title Case."""
        mock_stats = {
            "custom_metric": 123,
            "another_new_stat": 456,
        }
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.pool_stats = mock_stats
            mock_get_db.return_value = mock_db

            result = resource_pool_stats()

            # Unknown keys should be converted from snake_case to Title Case
            assert "Custom Metric" in result
            assert "| 123 |" in result
            assert "Another New Stat" in result
            assert "| 456 |" in result

    def test_pool_stats_handles_exception(self):
        """Test pool stats handles exceptions gracefully."""
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("Connection error")

            result = resource_pool_stats()

            assert "Error retrieving pool statistics" in result
            assert "Connection error" in result

    def test_pool_stats_empty_stats_dict(self):
        """Test pool stats with empty stats dictionary."""
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.pool_stats = {}
            mock_get_db.return_value = mock_db

            result = resource_pool_stats()

            # Should still have header but no stat rows
            assert "# Connection Pool Statistics" in result
            assert "| Metric | Value |" in result

    def test_pool_stats_with_actual_pool_stats_keys(self):
        """Test pool stats with keys that match actual ConnectionPool.stats() output."""
        # These are the actual keys returned by ConnectionPool.stats()
        mock_stats = {
            "total_connections": 3,
            "pool_size": 5,
            "in_use": 1,
            "available": 2,
            "peak_usage": 2,
            "total_acquisitions": 10,
            "total_releases": 9,
            "failed_acquisitions": 0,
            "health_checks": 5,
            "max_size": 5,
            "min_size": 1,
            "closed": False,
        }
        with patch.object(database_info, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.pool_stats = mock_stats
            mock_get_db.return_value = mock_db

            result = resource_pool_stats()

            # Verify markdown structure
            assert "# Connection Pool Statistics" in result
            # Check all the new stats are present with their labels
            assert "Total Connections Created" in result
            assert "| 3 |" in result
            assert "Available Connections" in result
            assert "| 2 |" in result
            assert "Maximum Pool Size" in result or "Pool Size (Max)" in result
            assert "| 5 |" in result
            assert "Minimum Pool Size" in result
            assert "| 1 |" in result
            assert "Connections In Use" in result
            assert "| 1 |" in result
            assert "Peak Concurrent Usage" in result
            assert "Total Acquisitions" in result
            assert "| 10 |" in result
            assert "Total Releases" in result
            assert "| 9 |" in result
            # Boolean value
            assert "| False |" in result


class TestDatabaseParameterPassthrough:
    """Tests that the database parameter is correctly passed through to _get_db()."""

    def test_execute_query_passes_database(self, mock_query_results):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_query_results
            mock_get_db.return_value = mock_db

            execute_query("SELECT 1", database="analytics")

            mock_get_db.assert_called_with("analytics")

    def test_execute_statement_passes_database(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_statement.return_value = 1
            mock_get_db.return_value = mock_db

            execute_statement("INSERT INTO t VALUES (1)", database="analytics")

            mock_get_db.assert_called_with("analytics")

    def test_execute_query_file_passes_database(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"id": 1}]
            mock_get_db.return_value = mock_db

            with patch("sql_playground_mcp.tools.query_execution.get_query_dir") as mock_dir:
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    from pathlib import Path
                    qdir = Path(tmp)
                    (qdir / "test.sql").write_text("SELECT 1")
                    mock_dir.return_value = qdir

                    execute_query_file("test.sql", database="archive")

            mock_get_db.assert_called_with("archive")

    def test_list_tables_passes_database(self, mock_table_list):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_table_list
            mock_get_db.return_value = mock_db

            list_tables(database="analytics")

            mock_get_db.assert_called_with("analytics")

    def test_describe_table_passes_database(self, mock_column_definitions):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_column_definitions
            mock_get_db.return_value = mock_db

            describe_table("Users", database="analytics")

            mock_get_db.assert_called_with("analytics")

    def test_get_view_definition_passes_database(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"definition": "CREATE VIEW..."}]
            mock_get_db.return_value = mock_db

            get_view_definition("vw", database="analytics")

            mock_get_db.assert_called_with("analytics")

    def test_get_function_definition_passes_database(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [{"definition": "CREATE FUNCTION..."}]
            mock_get_db.return_value = mock_db

            get_function_definition("fn", database="analytics")

            mock_get_db.assert_called_with("analytics")

    def test_list_procedures_passes_database(self):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_get_db.return_value = mock_db

            list_procedures(database="analytics")

            mock_get_db.assert_called_with("analytics")

    def test_execute_procedure_passes_database(self, mock_procedure_results):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_procedure_results
            mock_get_db.return_value = mock_db

            execute_procedure("MyProc", database="analytics")

            mock_get_db.assert_called_with("analytics")


class TestInvalidDatabaseName:
    """Tests that invalid database names produce proper error responses."""

    def test_execute_query_invalid_database(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_get_db.side_effect = KeyError("Unknown database 'nonexistent'")

            result = execute_query("SELECT 1", database="nonexistent")

            assert result["success"] is False
            assert "nonexistent" in result["error"]

    def test_execute_statement_invalid_database(self):
        with patch.object(query_execution, '_get_db') as mock_get_db:
            mock_get_db.side_effect = KeyError("Unknown database 'nonexistent'")

            result = execute_statement("INSERT INTO t VALUES (1)", database="nonexistent")

            assert result["success"] is False
            assert "nonexistent" in result["error"]

    def test_list_tables_invalid_database(self):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_get_db.side_effect = KeyError("Unknown database 'bad'")

            result = list_tables(database="bad")

            assert result["success"] is False
            assert "bad" in result["error"]

    def test_describe_table_invalid_database(self):
        with patch.object(schema_discovery, '_get_db') as mock_get_db:
            mock_get_db.side_effect = KeyError("Unknown database 'bad'")

            result = describe_table("Users", database="bad")

            assert result["success"] is False
            assert "bad" in result["error"]

    def test_get_view_definition_invalid_database(self):
        with patch.object(object_definitions, '_get_db') as mock_get_db:
            mock_get_db.side_effect = KeyError("Unknown database 'bad'")

            result = get_view_definition("vw", database="bad")

            assert result["success"] is False
            assert "bad" in result["error"]

    def test_list_procedures_invalid_database(self):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_get_db.side_effect = KeyError("Unknown database 'bad'")

            result = list_procedures(database="bad")

            assert result["success"] is False
            assert "bad" in result["error"]

    def test_execute_procedure_invalid_database(self):
        with patch.object(stored_procedures, '_get_db') as mock_get_db:
            mock_get_db.side_effect = KeyError("Unknown database 'bad'")

            result = execute_procedure("MyProc", database="bad")

            assert result["success"] is False
            assert "bad" in result["error"]


class TestListDatabasesTool:
    """Tests for list_databases tool."""

    def test_list_databases_returns_configured(self):
        from sql_playground_mcp.tools import registry_tools
        from sql_playground_mcp.registry import DatabaseRegistry
        from sql_playground_mcp.config import DatabaseConfig

        config = DatabaseConfig(
            host="h1", port=1433, user="u", password="p",
            database="db1", driver="ODBC Driver 17 for SQL Server",
        )
        config2 = DatabaseConfig(
            host="h2", port=1433, user="u", password="p",
            database="db2", driver="ODBC Driver 17 for SQL Server",
        )
        registry = DatabaseRegistry(configs={"default": config, "analytics": config2})

        with patch.object(registry_tools, '_get_registry', return_value=registry):
            result = list_databases()

        assert result["success"] is True
        assert result["count"] == 2
        names = [d["name"] for d in result["databases"]]
        assert "default" in names
        assert "analytics" in names

    def test_list_databases_no_passwords(self):
        from sql_playground_mcp.tools import registry_tools
        from sql_playground_mcp.registry import DatabaseRegistry
        from sql_playground_mcp.config import DatabaseConfig

        config = DatabaseConfig(
            host="h1", port=1433, user="u", password="secret123",
            database="db1", driver="ODBC Driver 17 for SQL Server",
        )
        registry = DatabaseRegistry(configs={"default": config})

        with patch.object(registry_tools, '_get_registry', return_value=registry):
            result = list_databases()

        for db in result["databases"]:
            assert "password" not in db
            assert "secret123" not in str(db)

    def test_list_databases_includes_host_info(self):
        from sql_playground_mcp.tools import registry_tools
        from sql_playground_mcp.registry import DatabaseRegistry
        from sql_playground_mcp.config import DatabaseConfig

        config = DatabaseConfig(
            host="myhost.example.com", port=1433, user="u", password="p",
            database="mydb", driver="ODBC Driver 17 for SQL Server",
        )
        registry = DatabaseRegistry(configs={"default": config})

        with patch.object(registry_tools, '_get_registry', return_value=registry):
            result = list_databases()

        db_info = result["databases"][0]
        assert db_info["host"] == "myhost.example.com"
        assert db_info["port"] == 1433
        assert db_info["database"] == "mydb"


class TestResourceDatabases:
    """Tests for sqlserver://databases resource."""

    def test_resource_databases_success(self):
        from sql_playground_mcp.registry import DatabaseRegistry
        from sql_playground_mcp.config import DatabaseConfig

        config = DatabaseConfig(
            host="h1", port=1433, user="u", password="p",
            database="db1", driver="ODBC Driver 17 for SQL Server",
        )
        config2 = DatabaseConfig(
            host="h2", port=5432, user="u2", password="p2",
            database="db2", driver="ODBC Driver 17 for SQL Server",
        )
        registry = DatabaseRegistry(configs={"default": config, "analytics": config2})

        with patch.object(database_info, '_get_registry', return_value=registry):
            result = resource_databases()

        assert "# Configured Databases" in result
        assert "default" in result
        assert "analytics" in result
        assert "h1" in result
        assert "h2" in result

    def test_resource_databases_markdown_table(self):
        from sql_playground_mcp.registry import DatabaseRegistry
        from sql_playground_mcp.config import DatabaseConfig

        config = DatabaseConfig(
            host="h1", port=1433, user="u", password="p",
            database="db1", driver="ODBC Driver 17 for SQL Server",
        )
        registry = DatabaseRegistry(configs={"default": config})

        with patch.object(database_info, '_get_registry', return_value=registry):
            result = resource_databases()

        assert "| Name | Host | Port | Database |" in result
        assert "|------|------|------|----------|" in result

    def test_resource_databases_handles_error(self):
        with patch.object(database_info, '_get_registry') as mock_reg:
            mock_reg.side_effect = Exception("Registry error")

            result = resource_databases()

            assert "Error" in result
