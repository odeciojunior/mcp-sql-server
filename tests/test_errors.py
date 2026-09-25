"""Tests for error sanitization and simplification."""

import pytest

from mcp_sql_server import errors
from mcp_sql_server.errors import create_error_response, sanitize_error


class TestValueRedaction:
    def test_duplicate_key_value(self):
        msg = (
            "Violation of PRIMARY KEY constraint 'PK_Cliente'. Cannot insert duplicate key "
            "in object 'dbo.Cliente'. The duplicate key value is (123.456.789-00)."
        )
        out = sanitize_error(msg)
        assert "123.456.789-00" not in out
        assert "The duplicate key value is ([REDACTED])" in out

    def test_duplicate_key_value_with_nested_parens(self):
        out = sanitize_error("The duplicate key value is (a, (b)).")
        assert "(b)" not in out

    def test_truncated_value(self):
        msg = (
            "String or binary data would be truncated in table 'db.dbo.T', column 'Nome'. "
            "Truncated value: 'Maria da Silva'."
        )
        out = sanitize_error(msg)
        assert "Maria" not in out
        assert "Truncated value: '[REDACTED]'" in out

    def test_conversion_failed(self):
        msg = "Conversion failed when converting the varchar value 'abc''d' to data type int."
        out = sanitize_error(msg)
        assert "abc" not in out
        assert "the varchar value '[REDACTED]' to data type int" in out

    def test_error_248_overflow_value_redacted(self):
        msg = "The conversion of the varchar value '99999999999' overflowed an int column."
        out = sanitize_error(msg)
        assert "99999999999" not in out
        assert "the varchar value '[REDACTED]' overflowed an int column" in out

    def test_object_names_kept(self):
        assert "dbo.Req" in sanitize_error("Invalid object name 'dbo.Req'.")

    def test_credentials_still_redacted(self):
        out = sanitize_error("Login failed for user 'admin'. SERVER=10.0.0.5;PWD=x;")
        assert "admin" not in out
        assert "10.0.0.5" not in out

    def test_response_uses_redaction(self):
        resp = create_error_response("Truncated value: 'secret'.")
        assert "secret" not in resp["error"]

    def test_duplicate_key_value_with_embedded_crlf(self):
        """S4: DOTALL is needed per-pattern so an embedded \\r\\n doesn't stop the match."""
        msg = "The duplicate key value is (Nome='O\r\nBrien', Id=1)."
        out = sanitize_error(msg)
        assert "Brien" not in out
        assert "The duplicate key value is ([REDACTED])" in out

    def test_conversion_failed_apostrophe_and_digits_not_leaked(self):
        msg = (
            "Conversion failed when converting the varchar value "
            "'O''Brien 123.456.789-00' to data type int."
        )
        out = sanitize_error(msg)
        assert "Brien" not in out
        assert "123" not in out

    def test_truncated_value_with_embedded_quote_leaks_nothing(self):
        msg = "Truncated value: 'Maria ''Mary'' Silva'."
        out = sanitize_error(msg)
        assert "Maria" not in out
        assert "Mary" not in out
        assert "Silva" not in out


    def test_duplicate_key_with_both_quotes(self):
        """Test that duplicate key redaction works when value has both ' and \" (escape as \')."""
        # Simulate pyodbc.Error repr: single quotes are escaped as \'
        msg = (
            "Violation of PRIMARY KEY constraint 'PK_Cliente'. Cannot insert duplicate key "
            'in object \'dbo.Cliente\'. The duplicate key value is (se"cret O\'Brien 123).'
        )
        out = sanitize_error(msg)
        assert "Brien" not in out
        assert "123" not in out
        assert "se" not in out or "se\"cret" not in out  # at least the mixed part is redacted

    def test_truncated_value_with_both_quotes(self):
        """Test truncated-value redaction when value has both ' and \"."""
        # Value: se"cret with an apostrophe inside
        msg = 'Truncated value: \'se"cret O\'Brien 123.456.789-00\'.'
        out = sanitize_error(msg)
        assert "Brien" not in out
        assert "123.456" not in out
        assert "Truncated value: '[REDACTED]'" in out

    def test_conversion_failed_with_both_quotes(self):
        """Test conversion (245) redaction when value has both ' and \"."""
        # This mimics what pyodbc.Error's repr does: escapes ' as \'
        msg = (
            "Conversion failed when converting the nvarchar value "
            '\'ab"c O\'Brien 123.456.789-00\' to data type int. (245)'
        )
        out = sanitize_error(msg)
        assert "Brien" not in out
        assert "123.456" not in out
        assert "ab" not in out or 'ab"c' not in out  # at least distinctive parts are gone
        assert "the nvarchar value '[REDACTED]' to data type int" in out

    def test_overflow_248_with_both_quotes(self):
        """Test overflow (248) redaction when value has both ' and \"."""
        msg = (
            "The conversion of the varchar value "
            '\'secret"password O\'Brien 123.456.789-00\' overflowed an int column.'
        )
        out = sanitize_error(msg)
        assert "Brien" not in out
        assert "123.456" not in out
        assert "secret" not in out or 'secret"' not in out  # at least distinctive parts
        assert "the varchar value '[REDACTED]' overflowed an int column" in out


@pytest.mark.parametrize(
    "name", ["MCPError", "ValidationError", "ConnectionError", "QueryError", "TimeoutError"]
)
def test_unused_exception_classes_removed(name):
    assert not hasattr(errors, name)
