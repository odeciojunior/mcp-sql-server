"""Tests for security validation."""

import pytest

from mcp_sql_server.security import (
    sanitize_table_name,
    validate_identifier,
    validate_procedure_name,
    validate_query,
)


class TestValidateQuery:
    """Tests for validate_query function."""

    def test_valid_select(self):
        is_valid, error = validate_query("SELECT * FROM users")
        assert is_valid
        assert error == ""

    def test_valid_select_with_where(self):
        is_valid, error = validate_query("SELECT id, name FROM users WHERE id = 1")
        assert is_valid
        assert error == ""

    def test_valid_with_cte(self):
        sql = "WITH cte AS (SELECT * FROM users) SELECT * FROM cte"
        is_valid, error = validate_query(sql)
        assert is_valid
        assert error == ""

    def test_empty_query(self):
        is_valid, error = validate_query("")
        assert not is_valid
        assert "empty" in error.lower()

    def test_blocked_drop(self):
        is_valid, error = validate_query("DROP TABLE users")
        assert not is_valid
        assert "DROP" in error

    def test_blocked_truncate(self):
        is_valid, error = validate_query("TRUNCATE TABLE users")
        assert not is_valid
        assert "TRUNCATE" in error

    def test_blocked_alter(self):
        is_valid, error = validate_query("ALTER TABLE users ADD column1 INT")
        assert not is_valid
        assert "ALTER" in error

    def test_blocked_create(self):
        is_valid, error = validate_query("CREATE TABLE test (id INT)")
        assert not is_valid
        assert "CREATE" in error

    def test_blocked_xp_cmdshell(self):
        is_valid, error = validate_query("EXEC xp_cmdshell 'dir'")
        assert not is_valid
        assert "xp_" in error.lower()

    def test_blocked_sp_procedure(self):
        is_valid, error = validate_query("EXEC sp_executesql 'SELECT 1'")
        assert not is_valid
        assert "sp_" in error.lower()

    def test_insert_not_allowed_by_default(self):
        is_valid, error = validate_query("INSERT INTO users (name) VALUES ('test')")
        assert not is_valid
        assert "INSERT" in error

    def test_insert_allowed_with_modifications(self):
        is_valid, error = validate_query(
            "INSERT INTO users (name) VALUES ('test')", allow_modifications=True
        )
        assert is_valid
        assert error == ""

    def test_update_allowed_with_modifications(self):
        is_valid, error = validate_query(
            "UPDATE users SET name = 'test' WHERE id = 1", allow_modifications=True
        )
        assert is_valid
        assert error == ""

    def test_delete_allowed_with_modifications(self):
        is_valid, error = validate_query(
            "DELETE FROM users WHERE id = 1", allow_modifications=True
        )
        assert is_valid
        assert error == ""

    def test_drop_blocked_even_with_modifications(self):
        is_valid, error = validate_query("DROP TABLE users", allow_modifications=True)
        assert not is_valid
        assert "DROP" in error


class TestValidateIdentifier:
    """Tests for validate_identifier function."""

    def test_valid_simple_name(self):
        is_valid, error = validate_identifier("users")
        assert is_valid
        assert error == ""

    def test_valid_with_underscore(self):
        is_valid, error = validate_identifier("user_accounts")
        assert is_valid
        assert error == ""

    def test_valid_with_numbers(self):
        is_valid, error = validate_identifier("users2024")
        assert is_valid
        assert error == ""

    def test_valid_starts_with_underscore(self):
        is_valid, error = validate_identifier("_temp_table")
        assert is_valid
        assert error == ""

    def test_invalid_empty(self):
        is_valid, error = validate_identifier("")
        assert not is_valid
        assert "empty" in error.lower()

    def test_invalid_starts_with_number(self):
        is_valid, error = validate_identifier("123users")
        assert not is_valid
        assert "Invalid" in error

    def test_invalid_special_chars(self):
        is_valid, error = validate_identifier("users; DROP TABLE")
        assert not is_valid
        assert "Invalid" in error

    def test_invalid_spaces(self):
        is_valid, error = validate_identifier("user accounts")
        assert not is_valid
        assert "Invalid" in error

    def test_blocked_keyword_drop(self):
        is_valid, error = validate_identifier("DROP")
        assert not is_valid
        assert "Reserved" in error


class TestSanitizeTableName:
    """Tests for sanitize_table_name function."""

    def test_simple_table(self):
        result = sanitize_table_name("users")
        assert result == "[dbo].[users]"

    def test_custom_schema(self):
        result = sanitize_table_name("users", schema="custom")
        assert result == "[custom].[users]"

    def test_invalid_table_name(self):
        with pytest.raises(ValueError):
            sanitize_table_name("users; DROP TABLE")

    def test_invalid_schema(self):
        with pytest.raises(ValueError):
            sanitize_table_name("users", schema="bad; schema")


class TestValidateProcedureName:
    """Tests for validate_procedure_name function."""

    def test_valid_user_procedure(self):
        is_valid, error = validate_procedure_name("GetUserById")
        assert is_valid
        assert error == ""

    def test_valid_procedure_with_underscore(self):
        is_valid, error = validate_procedure_name("get_user_data")
        assert is_valid
        assert error == ""

    def test_blocked_xp_procedure(self):
        is_valid, error = validate_procedure_name("xp_cmdshell")
        assert not is_valid
        assert "System procedure not allowed" in error

    def test_blocked_xp_procedure_uppercase(self):
        is_valid, error = validate_procedure_name("XP_CMDSHELL")
        assert not is_valid
        assert "System procedure not allowed" in error

    def test_blocked_sp_procedure(self):
        is_valid, error = validate_procedure_name("sp_executesql")
        assert not is_valid
        assert "System procedure not allowed" in error

    def test_blocked_sp_procedure_mixed_case(self):
        is_valid, error = validate_procedure_name("Sp_ExecuteSql")
        assert not is_valid
        assert "System procedure not allowed" in error

    def test_procedure_starting_with_sp_but_not_system(self):
        # "special_report" starts with "sp" but not "sp_"
        is_valid, error = validate_procedure_name("special_report")
        assert is_valid
        assert error == ""

    def test_procedure_containing_xp_in_middle(self):
        # "export_data" contains "xp" but doesn't start with "xp_"
        is_valid, error = validate_procedure_name("export_data")
        assert is_valid
        assert error == ""
