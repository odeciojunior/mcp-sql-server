"""Tests for audit SQL masking and hashing."""

from mcp_sql_server.audit import _get_sql_preview, _hash_sql, _mask_sql


class TestMaskSql:
    def test_masks_strings_and_numbers(self):
        sql = "SELECT * FROM Cliente WHERE cpf = '123.456.789-00' AND id>=42"
        assert _mask_sql(sql) == "SELECT * FROM Cliente WHERE cpf = ? AND id>=?"

    def test_masks_unicode_string(self):
        assert _mask_sql("WHERE a = N'Não'") == "WHERE a = ?"

    def test_masks_double_quoted_token(self):
        assert _mask_sql('WHERE a = "secret"') == "WHERE a = ?"

    def test_keeps_bracket_identifiers(self):
        assert _mask_sql("SELECT [Order Details].x") == "SELECT [Order Details].x"

    def test_comments_become_single_space(self):
        assert _mask_sql("SELECT /* pwd=x */ 1\n\n  FROM t -- c") == "SELECT ? FROM t"

    def test_adjacent_tokens_stay_adjacent(self):
        assert _mask_sql("a.b>=c") == "a.b>=c"

    def test_unparseable_returns_none(self):
        assert _mask_sql("SELECT 'open") is None


class TestPreview:
    def test_truncates_masked(self):
        sql = "SELECT " + ", ".join(f"c{i}" for i in range(100))
        preview = _get_sql_preview(sql, max_length=20)
        assert preview.endswith("...")
        assert len(preview) == 23

    def test_unparseable(self):
        assert _get_sql_preview("SELECT 'open") == "<unparseable>"

    def test_no_literal_leaks(self):
        assert "123" not in _get_sql_preview("SELECT 1 WHERE cpf = '123'")


class TestHash:
    def test_same_shape_same_hash(self):
        assert _hash_sql("SELECT * FROM t WHERE cpf = '111'") == _hash_sql(
            "SELECT * FROM t WHERE cpf = '222'"
        )

    def test_different_shape_different_hash(self):
        assert _hash_sql("SELECT a FROM t") != _hash_sql("SELECT b FROM t")

    def test_length(self):
        assert len(_hash_sql("SELECT 1")) == 16

    def test_unparseable_falls_back_to_raw(self):
        assert _hash_sql("SELECT 'a") != _hash_sql("SELECT 'b")
