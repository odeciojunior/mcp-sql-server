"""Tests for the T-SQL tokenizer."""

import time

import pytest

from mcp_sql_server.sql_lexer import LexError, Token, TokenKind, tokenize


def kinds(sql: str) -> list[tuple[TokenKind, str]]:
    return [(t.kind, t.text) for t in tokenize(sql)]


W, S, Q, N, SEMI, O = (
    TokenKind.WORD,
    TokenKind.STRING,
    TokenKind.QUOTED_IDENT,
    TokenKind.NUMBER,
    TokenKind.SEMICOLON,
    TokenKind.OTHER,
)


class TestBasicTokens:
    def test_words_and_operators(self):
        assert kinds("SELECT a.b FROM t") == [
            (W, "SELECT"), (W, "a"), (O, "."), (W, "b"), (W, "FROM"), (W, "t"),
        ]

    def test_semicolon(self):
        assert kinds("SELECT 1;") == [(W, "SELECT"), (N, "1"), (SEMI, ";")]

    def test_parameter_placeholder_is_other(self):
        assert kinds("a = ?") == [(W, "a"), (O, "="), (O, "?")]

    def test_upper_property(self):
        assert tokenize("select")[0].upper == "SELECT"

    def test_offsets(self):
        tok = tokenize("  SELECT")[0]
        assert (tok.start, tok.end) == (2, 8)


class TestStrings:
    def test_simple_string(self):
        assert kinds("'abc'") == [(S, "'abc'")]

    def test_escaped_quote(self):
        assert kinds("'it''s'") == [(S, "'it''s'")]

    def test_n_prefix_at_token_start(self):
        assert kinds("N'Não'") == [(S, "N'Não'")]
        assert kinds("n'x'") == [(S, "n'x'")]

    def test_n_inside_word_is_not_prefix(self):
        assert kinds("columnN'x'") == [(W, "columnN"), (S, "'x'")]

    def test_comment_markers_inside_string(self):
        assert kinds("'-- not /* a comment'") == [(S, "'-- not /* a comment'")]

    def test_unterminated_string(self):
        with pytest.raises(LexError, match="unterminated string"):
            tokenize("SELECT 'abc")


class TestQuotedIdentifiers:
    def test_bracket(self):
        assert kinds("[Order Details]") == [(Q, "[Order Details]")]

    def test_bracket_escape(self):
        assert kinds("[a]]b]") == [(Q, "[a]]b]")]

    def test_double_quote(self):
        assert kinds('"col"') == [(Q, '"col"')]

    def test_double_quote_escape(self):
        assert kinds('"a""b"') == [(Q, '"a""b"')]

    def test_quote_inside_bracket(self):
        assert kinds("[it's]") == [(Q, "[it's]")]

    def test_unterminated_bracket(self):
        with pytest.raises(LexError, match="unterminated quoted identifier"):
            tokenize("SELECT [abc")


class TestNumbers:
    def test_integer_and_decimal(self):
        assert kinds("1 2.5") == [(N, "1"), (N, "2.5")]

    def test_leading_dot(self):
        assert kinds(".5") == [(N, ".5")]

    def test_exponent(self):
        assert kinds("1e5 1.5E-3 1.e5") == [(N, "1e5"), (N, "1.5E-3"), (N, "1.e5")]

    def test_hex_before_decimal(self):
        assert kinds("0xDEAD") == [(N, "0xDEAD")]

    def test_trailing_e_without_digits(self):
        assert kinds("1.5e") == [(N, "1.5e")]

    def test_exponent_glued_keyword_splits_after_e(self):
        assert kinds("1eEXEC") == [(N, "1e"), (W, "EXEC")]

    def test_non_ascii_digits_are_not_numbers(self):
        assert tokenize("١٢٣")[0].kind is not TokenKind.NUMBER

    def test_malformed_exponent_sign_with_no_digit_raises(self):
        with pytest.raises(LexError, match="malformed number exponent"):
            tokenize("SELECT 1e--x")

    def test_malformed_exponent_sign_at_end_raises(self):
        with pytest.raises(LexError, match="malformed number exponent"):
            tokenize("1e+")

    def test_exponent_with_digits_after_sign_still_single_token(self):
        assert kinds("1.5E-3") == [(N, "1.5E-3")]

    def test_exponent_glued_keyword_still_splits(self):
        assert kinds("1eEXEC") == [(N, "1e"), (W, "EXEC")]


class TestWords:
    def test_portuguese_identifier_is_one_word(self):
        assert kinds("Situação Descrição") == [(W, "Situação"), (W, "Descrição")]

    def test_system_variable(self):
        assert kinds("@@VERSION") == [(W, "@@VERSION")]

    def test_temp_table_and_variable(self):
        assert kinds("#tmp @p1") == [(W, "#tmp"), (W, "@p1")]


class TestCommentsAndWhitespace:
    def test_line_comment_skipped(self):
        assert kinds("SELECT 1 -- DROP\nFROM t") == [
            (W, "SELECT"), (N, "1"), (W, "FROM"), (W, "t"),
        ]

    def test_line_comment_ends_at_carriage_return(self):
        assert kinds("SELECT 1 --x\rDELETE") == [(W, "SELECT"), (N, "1"), (W, "DELETE")]

    def test_block_comment_skipped(self):
        assert kinds("SELECT /* DROP */ 1") == [(W, "SELECT"), (N, "1")]

    def test_nested_block_comment(self):
        assert kinds("SELECT /* a /* b */ c */ 1") == [(W, "SELECT"), (N, "1")]

    def test_unterminated_block_comment(self):
        with pytest.raises(LexError, match="unterminated block comment"):
            tokenize("SELECT /* a /* b */ 1")

    def test_comment_only_input_has_no_tokens(self):
        assert tokenize("-- just a note\n/* and this */") == []

    def test_non_breaking_space_is_other(self):
        assert kinds("SELECT\xa0DELETE") == [(W, "SELECT"), (O, "\xa0"), (W, "DELETE")]

    def test_nul_is_other(self):
        assert kinds("a\x00b") == [(W, "a"), (O, "\x00"), (W, "b")]


class TestDepth:
    def test_parenthesis_depth(self):
        toks = tokenize("SELECT (SELECT 1) x")
        depths = [(t.text, t.depth) for t in toks]
        assert depths == [
            ("SELECT", 0), ("(", 0), ("SELECT", 1), ("1", 1), (")", 0), ("x", 0),
        ]

    def test_unbalanced_close_never_negative(self):
        assert all(t.depth >= 0 for t in tokenize(") ) SELECT"))


class TestPerformance:
    def test_long_input_is_linear(self):
        sql = "SELECT * FROM t WHERE id IN (" + ", ".join(str(i) for i in range(60000)) + ")"
        started = time.perf_counter()
        tokens = tokenize(sql)
        assert time.perf_counter() - started < 2.0
        assert len(tokens) > 120000


def test_token_is_frozen():
    tok = Token(TokenKind.WORD, "a", 0, 1, 0)
    with pytest.raises(Exception):
        tok.text = "b"  # type: ignore[misc]
