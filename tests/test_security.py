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


# pytest and validate_query are already imported at the top of this module.

READ_ACCEPTED = [
    "WITH c AS (SELECT 1 x) SELECT * FROM c",
    "SELECT * FROM t ORDER BY id",
    "SELECT a FROM t UNION ALL SELECT b FROM u",
    "SELECT a FROM t EXCEPT SELECT b FROM u",
    "SELECT * FROM t ORDER BY id OFFSET 0 ROWS FETCH NEXT 5 ROWS ONLY",
    "-- note\nSELECT 1",
    "SELECT * FROM t WHERE x = 'DROP'",
    "SELECT [Create], Situação FROM t",
    "SELECT 1 /* DROP */",
    "SELECT 1;",
    "SELECT 1; -- done",
    "SELECT * FROM t WHERE a = ? AND b = ?",
    "SELECT Situação FROM Req WHERE Descrição = N'Não'",
    "SELECT * FROM t WITH (NOLOCK)",
    "SELECT CASE WHEN a = 1 THEN 'x' END FROM t",
    "SELECT * FROM t WHERE name = 'xp_cmdshell'",
    "SELECT [sp_who] FROM t",
    "SELECT * FROM t WHERE a IN (SELECT a FROM u)",
]

READ_REJECTED = [
    ("SELECT 1; SELECT 2", "Multiple statements"),
    ("SELECT 1 SELECT 2", "Multiple statements"),
    ("SELECT 1 WAITFOR DELAY '00:01'", "WAITFOR"),
    ("SELECT 1 USE master", "USE"),
    ("SELECT 1 SET ROWCOUNT 0", "SET"),
    ("WITH c AS (SELECT 1 x) DELETE FROM t", "DELETE"),
    ("SELECT * INTO NewTable FROM t", "INTO"),
    ("SELECT NEXT VALUE FOR dbo.Seq", "NEXT VALUE"),
    ("SELECT * FROM sys.fn_trace_gettable('x', 1)", "FN_TRACE_GETTABLE"),
    ("SELECT * FROM sys.[fn_trace_gettable]('x', 1)", "FN_TRACE_GETTABLE"),
    ('SELECT * FROM "fn_get_audit_file"(\'x\', NULL, NULL)', "FN_GET_AUDIT_FILE"),
    ("SELECT 'unterminated", "Invalid SQL"),
    ("SELECT 1 --\rDELETE FROM t", "DELETE"),
    ("SELECT 1 DECLARE @p int", "DECLARE"),
    ("SELECT 1e--'\nEXEC('x') --'", "Invalid SQL"),
    ("SELECT 1e-- \nDELETE FROM t", "Invalid SQL"),
    ("SELECT 1 BEGIN TRAN", "BEGIN"),
    ("SELECT 1 COMMIT", "COMMIT"),
    ("SELECT 1 DENY SELECT ON t TO u", "DENY"),
    ("SELECT 1 EXEC('x')", "EXEC"),
    ("SELECT 1 WRITETEXT t.c @p 'x'", "WRITETEXT"),
    ("SELECT 1 DISABLE TRIGGER trg ON t", "DISABLE TRIGGER"),
    ("SELECT 1 ADD SIGNATURE TO p BY CERTIFICATE c", "ADD"),
    ("-- only a comment", "empty"),
    ("(SELECT 1)", "Statement type"),
    (r"SELECT * FROM sys.dm_os_file_exists('\\h\s\x')", "DM_OS_FILE_EXISTS"),
    (r"SELECT * FROM sys.dm_os_enumerate_filesystem('\\h\s', '*')", "DM_OS_ENUMERATE_FILESYSTEM"),
    ("SELECT 1eEXEC('select 1')", "EXEC"),
    ("SELECT 1eDELETE FROM t", "DELETE"),
    ("SELECT 1.eWAITFOR DELAY '00:00:05'", "WAITFOR"),
    ("SELECT .5eUSE master", "USE"),
]

STATEMENT_ACCEPTED = [
    "INSERT INTO t (a) SELECT a FROM u WHERE b = 1",
    "INSERT INTO t (a) VALUES (?)",
    "UPDATE t SET a = (SELECT MAX(b) FROM u) WHERE id = 1",
    "UPDATE t SET a = 1 FROM t JOIN u ON t.id = u.id",
    "DELETE FROM t OUTPUT deleted.id INTO audit WHERE id = 1",
    "INSERT INTO t SELECT a FROM u UNION ALL SELECT b FROM v",
    "update t set a = 1 where id = ?",
]

STATEMENT_REJECTED = [
    ("INSERT INTO t VALUES (1); EXEC('x')", "Multiple statements"),
    ("INSERT INTO t VALUES (1) EXEC('x')", "EXEC"),
    ("INSERT INTO t VALUES (1) DELETE FROM u", "Multiple statements"),
    ("UPDATE t SET a=1 SELECT * INTO x FROM y", "Multiple statements"),
    ("UPDATE t SET a=1 DISABLE TRIGGER trg ON t", "DISABLE TRIGGER"),
    ("UPDATE t SET a=1 SET b=2", "Multiple statements"),
    ("INSERT INTO t VALUES (1) SELECT 1", "Multiple statements"),
    ("INSERT INTO t SELECT 1 SELECT 2", "Multiple statements"),
    ("INSERT INTO t SELECT * INTO x FROM y", "INTO not allowed"),
    ("DELETE FROM t SET a = 1", "Multiple statements"),
    ("SELECT 1", "Statement type"),
    ("WITH c AS (SELECT 1 x) DELETE FROM t", "Statement type"),
    ("MERGE t USING u ON 1=1 WHEN MATCHED THEN DELETE;", "Statement type"),
]


class TestTokenValidationReadMode:
    @pytest.mark.parametrize("sql", READ_ACCEPTED)
    def test_accepted(self, sql):
        assert validate_query(sql) == (True, "")

    @pytest.mark.parametrize("sql,fragment", READ_REJECTED)
    def test_rejected(self, sql, fragment):
        valid, error = validate_query(sql)
        assert not valid
        assert fragment.lower() in error.lower()


class TestTokenValidationModifyMode:
    @pytest.mark.parametrize("sql", STATEMENT_ACCEPTED)
    def test_accepted(self, sql):
        assert validate_query(sql, allow_modifications=True) == (True, "")

    @pytest.mark.parametrize("sql,fragment", STATEMENT_REJECTED)
    def test_rejected(self, sql, fragment):
        valid, error = validate_query(sql, allow_modifications=True)
        assert not valid
        assert fragment.lower() in error.lower()


class TestNonAsciiKeywordBypass:
    def test_dotless_i_union_is_rejected(self):
        """F5: 'unıon'.upper() == 'UNION' in Python; must not be treated as UNION."""
        valid, error = validate_query("SELECT 1 unıon SELECT 2")
        assert not valid
        assert "multiple statements" in error.lower()


class TestStatementTypeErrorDoesNotLeakLiteral:
    def test_string_literal_not_echoed(self):
        """F6: the error names the token kind, not the literal's raw text."""
        valid, error = validate_query("'123.456.789-00' x")
        assert not valid
        assert "123" not in error


class TestIdentifierUnaffectedByStatementWords:
    def test_table_named_print_still_valid(self):
        from mcp_sql_server.security import validate_identifier

        assert validate_identifier("Print") == (True, "")
