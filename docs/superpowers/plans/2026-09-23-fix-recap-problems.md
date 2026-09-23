# Fix Recap Problems Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every open problem from the 2026-09-23 codebase recap of `mcp-sql-server` — SQL validation gaps, broken CTE/ORDER BY queries, pool/session hygiene, logging wiring, dead code, and stale docs — without renaming or removing any MCP tool, parameter, or response key.

**Architecture:** A new dependency-free tokenizer (`sql_lexer.py`) replaces regex-over-uppercase validation; `validate_query` enforces a single-statement rule at parenthesis depth 0. Row limits move from SQL rewriting to `SET ROWCOUNT` + `fetchmany` inside `DatabaseManager`, with poisoned pooled connections retired. Startup validates each database config independently and keeps running; logging is configured in `main()` with a per-tool-call `request_id`.

**Tech Stack:** Python ≥3.10, MCP SDK 2.x (`mcp.server.mcpserver.MCPServer`), pyodbc, pydantic 2.12+, python-dotenv, pytest (+ pytest-cov), mypy strict.

**Spec:** `docs/superpowers/specs/2026-09-23-fix-recap-problems-design.md` (revision 4). Read it before starting; Appendix A there explains why each rule exists.

## Global Constraints

- Do not rename or remove any MCP tool, tool parameter, resource URI, or existing response key. Adding keys (`truncated` on `execute_procedure`, `status`/`error` on `list_databases` entries) is allowed.
- `tests/test_server_registration.py` must pass unchanged.
- mypy strict must stay clean: `.venv/bin/python -m mypy src/mcp_sql_server/` → `Success: no issues found`.
- Full suite must pass after every task: `.venv/bin/pytest tests/ -q`.
- Existing tests may only change where a task says so explicitly.
- Log output goes to stderr only (stdout is the MCP stdio channel).
- No error message, log line, or exception text may contain a password, host value, or any raw env value.
- Commit messages end with: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
- All commands run from the repo root `/home/odecio/projects/mcp-sql-server` using `.venv/bin/...`.

## Review Focus

These inputs are implied by the spec but easy to miss; each has a test added to the task named in brackets.

1. **Portuguese identifiers and accented `N'...'` literals** (`SELECT Situação FROM Req WHERE Descrição = N'Não'`) must validate and mask cleanly, not be split or rejected. [Task 2]
2. **`?` parameter placeholders** in queries and statements must validate (they are `OTHER` tokens). [Task 2]
3. **A trailing comment after the final semicolon** (`SELECT 1; -- done`) must be accepted: comments produce no token, so `;` is still last. [Task 2]
4. **Very long SQL** (hundreds of KB, e.g. generated `IN (...)` lists) must tokenize in linear time without recursion. [Task 1]
5. **Real SQL Server behavior of `SET ROWCOUNT` with parameterized queries and the follow-up query on the same pooled connection** cannot be proven with mocks; verify against a live database when one is configured. [Task 14 live smoke check]

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/mcp_sql_server/sql_lexer.py` (new) | Tokenize T-SQL into kinds with offsets and paren depth | 1 |
| `src/mcp_sql_server/security.py` | Query/statement validation rules; identifier checks (unchanged) | 2 |
| `src/mcp_sql_server/tools/query_execution.py` | Tool bodies for query/statement/file | 2, 4 |
| `src/mcp_sql_server/database.py` | `DatabaseManager`: cursors, row limiting, session reset, `SessionStateError` | 3, 7 |
| `src/mcp_sql_server/pool.py` | `PooledConnection.invalid`; release retires invalid connections; acquire comment | 3, 7 |
| `src/mcp_sql_server/tools/stored_procedures.py` | Procedure row cap + `truncated` | 4 |
| `src/mcp_sql_server/audit.py` | Masked preview and shape hash | 5 |
| `src/mcp_sql_server/errors.py` | Driver-value redaction; dead exception classes removed | 6 |
| `src/mcp_sql_server/config.py` | Safe int/float parsing, per-alias loaders, safe error description, `DEFAULT_ENV_PATH` | 8 |
| `src/mcp_sql_server/registry.py` | Per-alias config error tolerance | 7, 9 |
| `src/mcp_sql_server/resources/database_info.py` | `sqlserver://databases` shows status | 9 |
| `src/mcp_sql_server/logging_config.py` | `RequestIdFilter`, `with_request_id`, setup; dead helpers removed | 10 |
| `src/mcp_sql_server/server.py` | `main()` startup order, tool decorators, no import-time logging config | 11 |
| `src/mcp_sql_server/tools/__init__.py`, `resources/__init__.py` | Drop `ALL_TOOLS`/`ALL_RESOURCES` | 12 |
| `src/mcp_sql_server/cache.py` | Signature-bound cache keys | 12 |
| Docs: `README.md`, `CLAUDE.md`, `.env.example`, `.claude/rules/mcp-tools.md`, `.claude/agents/*.md` | Behavior docs | 2, 4, 5, 6, 9, 11, 12, 13 |

---

### Task 1: SQL tokenizer

**Files:**
- Create: `src/mcp_sql_server/sql_lexer.py`
- Test: `tests/test_sql_lexer.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class TokenKind(Enum)`: `WORD`, `STRING`, `QUOTED_IDENT`, `NUMBER`, `SEMICOLON`, `OTHER`
  - `@dataclass(frozen=True) class Token(kind: TokenKind, text: str, start: int, end: int, depth: int)` with property `upper -> str` (`text.upper()`)
  - `class LexError(ValueError)`
  - `def tokenize(sql: str) -> list[Token]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sql_lexer.py`:

```python
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
        assert kinds("1.5e") == [(N, "1.5"), (W, "e")]

    def test_non_ascii_digits_are_not_numbers(self):
        assert tokenize("١٢٣")[0].kind is not TokenKind.NUMBER


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sql_lexer.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'mcp_sql_server.sql_lexer'`

- [ ] **Step 3: Implement the tokenizer**

Create `src/mcp_sql_server/sql_lexer.py`:

```python
"""Minimal T-SQL tokenizer for statement validation and log masking.

This is not a parser. It classifies tokens and tracks parenthesis depth so
that validation never inspects string literals, quoted identifiers, or
comments for keywords. Any character it does not recognise becomes an OTHER
token, so a mismatch with SQL Server's own lexer can only split words (and
cause a rejection), never hide them.
"""

from dataclasses import dataclass
from enum import Enum

_WHITESPACE = frozenset(" \t\r\n\f\v")
_ASCII_DIGITS = frozenset("0123456789")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class TokenKind(Enum):
    WORD = "word"
    STRING = "string"
    QUOTED_IDENT = "quoted_ident"
    NUMBER = "number"
    SEMICOLON = "semicolon"
    OTHER = "other"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    start: int
    end: int
    depth: int

    @property
    def upper(self) -> str:
        return self.text.upper()


class LexError(ValueError):
    """Raised for an unterminated string, quoted identifier, or block comment."""


def _is_word_start(ch: str) -> bool:
    return ch in "_@#" or ch.isalpha()


def _is_word_char(ch: str) -> bool:
    return ch in "_@#$" or ch in _ASCII_DIGITS or ch.isalpha()


def _scan_delimited(sql: str, pos: int, close: str, what: str) -> int:
    """Return the index just past the closing delimiter.

    `pos` is the index just after the opening delimiter. A doubled closing
    delimiter is an escaped literal character.
    """
    n = len(sql)
    i = pos
    while i < n:
        if sql[i] == close:
            if i + 1 < n and sql[i + 1] == close:
                i += 2
                continue
            return i + 1
        i += 1
    raise LexError(f"unterminated {what}")


def _scan_block_comment(sql: str, pos: int) -> int:
    """Return the index just past a (possibly nested) block comment at `pos`."""
    n = len(sql)
    nesting = 0
    i = pos
    while i < n:
        if sql.startswith("/*", i):
            nesting += 1
            i += 2
        elif sql.startswith("*/", i):
            nesting -= 1
            i += 2
            if nesting == 0:
                return i
        else:
            i += 1
    raise LexError("unterminated block comment")


def _scan_number(sql: str, pos: int) -> int:
    n = len(sql)
    i = pos
    if sql[i] == "0" and i + 1 < n and sql[i + 1] in "xX":
        i += 2
        while i < n and sql[i] in _HEX_DIGITS:
            i += 1
        return i
    while i < n and sql[i] in _ASCII_DIGITS:
        i += 1
    if i < n and sql[i] == ".":
        i += 1
        while i < n and sql[i] in _ASCII_DIGITS:
            i += 1
    if i < n and sql[i] in "eE":
        j = i + 1
        if j < n and sql[j] in "+-":
            j += 1
        if j < n and sql[j] in _ASCII_DIGITS:
            i = j
            while i < n and sql[i] in _ASCII_DIGITS:
                i += 1
    return i


def tokenize(sql: str) -> list[Token]:
    """Split T-SQL into tokens; comments and whitespace produce none.

    Raises:
        LexError: for an unterminated string, quoted identifier, or block comment.
    """
    tokens: list[Token] = []
    n = len(sql)
    i = 0
    depth = 0
    while i < n:
        ch = sql[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if sql.startswith("--", i):
            while i < n and sql[i] not in "\r\n":
                i += 1
            continue
        if sql.startswith("/*", i):
            i = _scan_block_comment(sql, i)
            continue

        start = i
        token_depth = depth
        if ch in "Nn" and i + 1 < n and sql[i + 1] == "'":
            kind = TokenKind.STRING
            end = _scan_delimited(sql, i + 2, "'", "string")
        elif ch == "'":
            kind = TokenKind.STRING
            end = _scan_delimited(sql, i + 1, "'", "string")
        elif ch == "[":
            kind = TokenKind.QUOTED_IDENT
            end = _scan_delimited(sql, i + 1, "]", "quoted identifier")
        elif ch == '"':
            kind = TokenKind.QUOTED_IDENT
            end = _scan_delimited(sql, i + 1, '"', "quoted identifier")
        elif ch in _ASCII_DIGITS or (ch == "." and i + 1 < n and sql[i + 1] in _ASCII_DIGITS):
            kind = TokenKind.NUMBER
            end = _scan_number(sql, i)
        elif _is_word_start(ch):
            kind = TokenKind.WORD
            end = i + 1
            while end < n and _is_word_char(sql[end]):
                end += 1
        elif ch == ";":
            kind = TokenKind.SEMICOLON
            end = i + 1
        else:
            kind = TokenKind.OTHER
            end = i + 1
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
                token_depth = depth

        tokens.append(Token(kind, sql[start:end], start, end, token_depth))
        i = end
    return tokens
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sql_lexer.py -q`
Expected: all pass.

- [ ] **Step 5: Type check**

Run: `.venv/bin/python -m mypy src/mcp_sql_server/`
Expected: `Success: no issues found in 21 source files`

- [ ] **Step 6: Commit**

```bash
git add src/mcp_sql_server/sql_lexer.py tests/test_sql_lexer.py
git commit -m "feat(security): add minimal T-SQL tokenizer

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Token-based validation rules

**Files:**
- Modify: `src/mcp_sql_server/security.py:1-74` (constants and `validate_query`; leave `validate_identifier`, `validate_procedure_name`, `sanitize_table_name` untouched)
- Modify: `src/mcp_sql_server/tools/query_execution.py` (`execute_statement` body)
- Test: `tests/test_security.py` (append new classes; existing tests unchanged)
- Docs: `README.md` Security section and `execute_statement` blurb; `CLAUDE.md` Security section

**Interfaces:**
- Consumes: `tokenize`, `Token`, `TokenKind`, `LexError` from Task 1.
- Produces: `validate_query(sql: str, allow_modifications: bool = False) -> tuple[bool, str]` (same signature); new module constants `STATEMENT_WORDS`, `BLOCKED_PAIRS`, `BLOCKED_FUNCTIONS`, `READ_ONLY_FORBIDDEN`, `SET_OPERATORS`.

Note: the TOP wrapper in `execute_query` still exists after this task; Task 4 removes it. Validation only gets stricter here, so no commit is ever less safe than `main`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_security.py`:

```python
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
    ("SELECT 'unterminated", "Invalid SQL"),
    ("SELECT 1 --\rDELETE FROM t", "DELETE"),
    ("SELECT 1 DECLARE @p int", "DECLARE"),
    ("SELECT 1 BEGIN TRAN", "BEGIN"),
    ("SELECT 1 COMMIT", "COMMIT"),
    ("SELECT 1 DENY SELECT ON t TO u", "DENY"),
    ("SELECT 1 EXEC('x')", "EXEC"),
    ("SELECT 1 WRITETEXT t.c @p 'x'", "WRITETEXT"),
    ("SELECT 1 DISABLE TRIGGER trg ON t", "DISABLE TRIGGER"),
    ("SELECT 1 ADD SIGNATURE TO p BY CERTIFICATE c", "ADD"),
    ("-- only a comment", "empty"),
    ("(SELECT 1)", "Statement type"),
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


class TestIdentifierUnaffectedByStatementWords:
    def test_table_named_print_still_valid(self):
        from mcp_sql_server.security import validate_identifier

        assert validate_identifier("Print") == (True, "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_security.py -q`
Expected: many failures in the new classes (e.g. `SELECT * FROM t WHERE x = 'DROP'` rejected, `SELECT 1 WAITFOR ...` accepted).

- [ ] **Step 3: Rewrite the top of `security.py`**

Replace everything from the top of `src/mcp_sql_server/security.py` through the end of `validate_query` (currently lines 1–74) with:

```python
"""Security utilities for SQL validation and keyword blocking."""

import re

from .sql_lexer import LexError, Token, TokenKind, tokenize

# Blocked SQL keywords that could cause damage. Also used by validate_identifier.
BLOCKED_KEYWORDS: set[str] = {
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "GRANT",
    "REVOKE",
    "SHUTDOWN",
    "BACKUP",
    "RESTORE",
    "DBCC",
    "OPENROWSET",
    "OPENQUERY",
    "OPENDATASOURCE",
    "BULK",
    "KILL",
}

# Blocked prefixes for dangerous system procedures
BLOCKED_PREFIXES: set[str] = {"xp_", "sp_"}

# Words that begin a separate T-SQL statement. T-SQL does not require ';'
# between statements, so these are rejected anywhere in either mode.
# Kept apart from BLOCKED_KEYWORDS so identifiers such as a table named
# "Print" remain valid for describe_table.
# END (CASE ... END) and FETCH (OFFSET ... FETCH) are deliberately absent.
STATEMENT_WORDS: set[str] = {
    "EXEC", "EXECUTE", "DENY", "USE", "DECLARE", "WAITFOR", "BEGIN", "COMMIT",
    "ROLLBACK", "SAVE", "PRINT", "RAISERROR", "THROW", "IF", "WHILE", "GOTO",
    "RETURN", "OPEN", "CLOSE", "DEALLOCATE", "CHECKPOINT", "RECONFIGURE",
    "SETUSER", "REVERT", "RECEIVE", "SEND", "WRITETEXT", "UPDATETEXT",
    "READTEXT", "ADD",
}

# Consecutive word pairs that start a statement or cause side effects.
# Single words ENABLE/DISABLE/NEXT/GET/MOVE/END are common identifiers.
BLOCKED_PAIRS: set[tuple[str, str]] = {
    ("ENABLE", "TRIGGER"),
    ("DISABLE", "TRIGGER"),
    ("NEXT", "VALUE"),
    ("GET", "CONVERSATION"),
    ("MOVE", "CONVERSATION"),
    ("END", "CONVERSATION"),
}

# System functions that read files, including over SMB (credential leak).
BLOCKED_FUNCTIONS: set[str] = {
    "FN_XE_FILE_TARGET_READ_FILE",
    "FN_TRACE_GETTABLE",
    "FN_GET_AUDIT_FILE",
}

# Allowed statement types for execute_query (read-only)
ALLOWED_QUERY_KEYWORDS: set[str] = {"SELECT", "WITH"}

# Allowed statement types for execute_statement (modifications)
ALLOWED_STATEMENT_KEYWORDS: set[str] = {"INSERT", "UPDATE", "DELETE"}

# Words that may not appear anywhere in a read-only query
READ_ONLY_FORBIDDEN: set[str] = {"INSERT", "UPDATE", "DELETE", "MERGE", "INTO", "SET"}

# Words that may precede a second top-level SELECT
SET_OPERATORS: set[str] = {"UNION", "EXCEPT", "INTERSECT"}

_DML_WORDS: set[str] = {"INSERT", "UPDATE", "DELETE", "MERGE"}
_MULTIPLE = "Multiple statements are not allowed"


def _follows_set_operator(tokens: list[Token], idx: int) -> bool:
    """True if tokens[idx] is preceded by UNION/EXCEPT/INTERSECT (optionally + ALL)."""
    j = idx - 1
    if j >= 0 and tokens[j].kind is TokenKind.WORD and tokens[j].upper == "ALL":
        j -= 1
    return j >= 0 and tokens[j].kind is TokenKind.WORD and tokens[j].upper in SET_OPERATORS


def _check_read_query(tokens: list[Token]) -> tuple[bool, str]:
    select_seen = False
    for idx, tok in enumerate(tokens):
        if tok.kind is not TokenKind.WORD:
            continue
        word = tok.upper
        if word in READ_ONLY_FORBIDDEN:
            return False, f"Data modification not allowed in read-only query: {word}"
        if word == "SELECT" and tok.depth == 0:
            if select_seen and not _follows_set_operator(tokens, idx):
                return False, _MULTIPLE
            select_seen = True
    return True, ""


def _check_modify_statement(tokens: list[Token]) -> tuple[bool, str]:
    first = tokens[0].upper
    set_seen = False
    select_seen = False
    values_seen = False
    output_seen = False
    output_into_used = False
    for idx, tok in enumerate(tokens):
        if idx == 0 or tok.kind is not TokenKind.WORD:
            continue
        word = tok.upper
        if word in _DML_WORDS and tok.depth == 0:
            return False, _MULTIPLE
        if word == "SET":
            if first != "UPDATE" or set_seen or tok.depth != 0:
                return False, _MULTIPLE
            set_seen = True
        elif word in ("VALUES", "DEFAULT") and tok.depth == 0:
            values_seen = True
        elif word == "SELECT" and tok.depth == 0:
            if first != "INSERT" or values_seen:
                return False, _MULTIPLE
            if select_seen and not _follows_set_operator(tokens, idx):
                return False, _MULTIPLE
            select_seen = True
        elif word == "OUTPUT" and tok.depth == 0:
            output_seen = True
        elif word == "INTO":
            if idx == 1 and first == "INSERT":
                continue
            if output_seen and not output_into_used and tok.depth == 0:
                output_into_used = True
                continue
            return False, "INTO not allowed here"
    return True, ""


def validate_query(sql: str, allow_modifications: bool = False) -> tuple[bool, str]:
    """
    Validate SQL for security issues.

    Tokenizes the SQL so string literals, quoted identifiers, and comments
    are never inspected for keywords, then enforces a single statement at
    parenthesis depth 0 (T-SQL does not need ';' between statements).

    Args:
        sql: The SQL to validate
        allow_modifications: True for execute_statement (INSERT/UPDATE/DELETE
            only); False for execute_query (SELECT/WITH only)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sql or not sql.strip():
        return False, "Query cannot be empty"

    try:
        tokens = tokenize(sql)
    except LexError as e:
        return False, f"Invalid SQL: {e}"

    if not tokens:
        return False, "Query cannot be empty"

    last = len(tokens) - 1
    for idx, tok in enumerate(tokens):
        if tok.kind is TokenKind.SEMICOLON and idx != last:
            return False, _MULTIPLE

    words = [t for t in tokens if t.kind is TokenKind.WORD]

    for tok in words:
        for prefix in sorted(BLOCKED_PREFIXES):
            if tok.upper.startswith(prefix.upper()):
                return False, f"System procedure calls not allowed: {prefix}*"

    for tok in words:
        if tok.upper in BLOCKED_KEYWORDS:
            return False, f"Blocked keyword detected: {tok.upper}"

    for tok in words:
        if tok.upper in STATEMENT_WORDS:
            return False, f"Statement not allowed: {tok.upper}"

    for a, b in zip(tokens, tokens[1:]):
        if (
            a.kind is TokenKind.WORD
            and b.kind is TokenKind.WORD
            and (a.upper, b.upper) in BLOCKED_PAIRS
        ):
            return False, f"Statement not allowed: {a.upper} {b.upper}"

    for tok in words:
        if tok.upper in BLOCKED_FUNCTIONS:
            return False, f"Function not allowed: {tok.upper}"

    first = tokens[0]
    allowed = ALLOWED_STATEMENT_KEYWORDS if allow_modifications else ALLOWED_QUERY_KEYWORDS
    first_text = first.upper if first.kind is TokenKind.WORD else first.text
    if first.kind is not TokenKind.WORD or first_text not in allowed:
        return False, f"Statement type '{first_text}' not allowed"

    if allow_modifications:
        return _check_modify_statement(tokens)
    return _check_read_query(tokens)
```

`re` is still used by `validate_identifier` below; keep the import.

- [ ] **Step 4: Simplify `execute_statement`**

In `src/mcp_sql_server/tools/query_execution.py`, add to the imports:

```python
from ..sql_lexer import tokenize
```

and replace:

```python
    # Additional check: must be a modification statement
    first_word = sql.strip().upper().split()[0]
    if first_word not in {"INSERT", "UPDATE", "DELETE"}:
        return {"error": "Use execute_query for SELECT statements", "success": False}
```

with:

```python
    # Validation guarantees the first token is INSERT, UPDATE, or DELETE.
    first_word = tokenize(sql)[0].upper
```

- [ ] **Step 5: Run the security and server tests**

Run: `.venv/bin/pytest tests/test_security.py tests/test_server.py -q`
Expected: all pass (existing tests `test_blocked_xp_cmdshell`/`test_blocked_sp_procedure` still see `xp_`/`sp_` because the prefix check runs first).

- [ ] **Step 6: Update docs**

In `README.md`, replace the whole `### SQL Validation` subsection (from `### SQL Validation` up to, not including, `### Identifier Validation`) with:

```markdown
### SQL Validation

All queries and statements are tokenized before execution. Keywords inside string literals, quoted identifiers (`[...]`, `"..."`), and comments are ignored, so `WHERE note = 'DROP'` and leading `-- comments` are fine.

**Blocked Keywords (DDL/DCL/Admin):**

| Category | Keywords |
|----------|----------|
| DDL | `DROP`, `TRUNCATE`, `ALTER`, `CREATE` |
| DCL | `GRANT`, `REVOKE`, `DENY` |
| Admin | `SHUTDOWN`, `BACKUP`, `RESTORE`, `DBCC`, `KILL`, `RECONFIGURE`, `CHECKPOINT` |
| External Access | `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `BULK` |
| Dynamic SQL / control flow | `EXEC`, `EXECUTE`, `DECLARE`, `USE`, `WAITFOR`, `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVE`, `IF`, `WHILE`, `GOTO`, `RETURN`, `PRINT`, `RAISERROR`, `THROW`, `OPEN`, `CLOSE`, `DEALLOCATE`, `SETUSER`, `REVERT`, `RECEIVE`, `SEND`, `ADD` |
| Legacy text/image | `WRITETEXT`, `UPDATETEXT`, `READTEXT` |
| Side effects | `NEXT VALUE FOR`, `ENABLE/DISABLE TRIGGER`, `GET/MOVE/END CONVERSATION` |
| File readers | `fn_xe_file_target_read_file`, `fn_trace_gettable`, `fn_get_audit_file` |

**Blocked Prefixes:** `xp_*`, `sp_*` (system stored procedures)

**One statement per call.** T-SQL does not need `;` between statements, so the validator allows only one statement at the top level (outside parentheses). A single trailing `;` is fine.

- `execute_query` accepts only `SELECT` or `WITH` first, rejects `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `INTO`, and `SET` anywhere, and allows a second top-level `SELECT` only after `UNION`, `EXCEPT`, or `INTERSECT`.
- `execute_statement` accepts only `INSERT`, `UPDATE`, or `DELETE` first. `SET` is allowed once in an `UPDATE`; a top-level `SELECT` only in `INSERT ... SELECT`; `INTO` only in `INSERT INTO` or `OUTPUT ... INTO`.
- CTE-prefixed DML (`WITH c AS (...) DELETE ...`) is not supported by either tool.

Unbracketed column names that match a blocked word (for example `Send`, `Receive`, `Open`) must be written in brackets: `[Send]`.

**Use a read-only login.** The validator is defense in depth. For read-only use, connect with a login that only has `db_datareader`.
```

In `README.md` under `### \`execute_statement\``, after the parameter table, add:

```markdown
Only one statement is allowed per call; see [SQL Validation](#sql-validation).
```

In `CLAUDE.md`, replace the `### Blocked SQL Keywords` and `### Statement Type Enforcement` subsections with:

```markdown
### Blocked SQL Keywords

| Category | Keywords |
|----------|----------|
| DDL | `DROP`, `TRUNCATE`, `ALTER`, `CREATE` |
| DCL | `GRANT`, `REVOKE`, `DENY` |
| Admin | `SHUTDOWN`, `BACKUP`, `RESTORE`, `DBCC`, `KILL`, `RECONFIGURE`, `CHECKPOINT` |
| External | `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `BULK` |
| Statement starters | `EXEC`, `EXECUTE`, `DECLARE`, `USE`, `WAITFOR`, `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVE`, `IF`, `WHILE`, `GOTO`, `RETURN`, `PRINT`, `RAISERROR`, `THROW`, `OPEN`, `CLOSE`, `DEALLOCATE`, `SETUSER`, `REVERT`, `RECEIVE`, `SEND`, `ADD`, `WRITETEXT`, `UPDATETEXT`, `READTEXT` |
| Pairs / functions | `NEXT VALUE`, `ENABLE/DISABLE TRIGGER`, `GET/MOVE/END CONVERSATION`, `fn_xe_file_target_read_file`, `fn_trace_gettable`, `fn_get_audit_file` |
| System Procs | `xp_*`, `sp_*` prefixes |

Validation tokenizes SQL (`sql_lexer.py`); strings, quoted identifiers, and comments are never checked for keywords.

### Statement Type Enforcement

- One statement per call at parenthesis depth 0 (no reliance on `;`).
- `execute_query`: first word `SELECT`/`WITH`; `INSERT`/`UPDATE`/`DELETE`/`MERGE`/`INTO`/`SET` rejected anywhere; extra top-level `SELECT` only after `UNION`/`EXCEPT`/`INTERSECT`.
- `execute_statement`: first word `INSERT`/`UPDATE`/`DELETE`; one `SET` in `UPDATE`; top-level `SELECT` only in `INSERT ... SELECT`; `INTO` only in `INSERT INTO` / `OUTPUT ... INTO`.
- CTE-prefixed DML is unsupported.
```

Also add `sql_lexer.py` to the architecture listings:
- `README.md` Module Responsibilities table, after the `security.py` row: `| \`sql_lexer.py\` | Dependency-free T-SQL tokenizer (words, strings, quoted identifiers, numbers, paren depth) used by validation and audit masking |`
- `README.md` Project Structure tree, after the `security.py` line: `|       +-- sql_lexer.py                   # T-SQL tokenizer for validation and masking`
- `CLAUDE.md` Module Responsibilities table, after the `security.py` row: `| \`sql_lexer.py\` | T-SQL tokenizer used by validation and audit masking |`
- `CLAUDE.md` ASCII diagram `Cross-Cutting:` line: append `, sql_lexer.py`

- [ ] **Step 7: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; `Success: no issues found`.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_sql_server/security.py src/mcp_sql_server/tools/query_execution.py tests/test_security.py README.md CLAUDE.md
git commit -m "feat(security): token-based validation with single-statement rule

Blocks statement stacking without semicolons, side-effect functions, and
DML inside read-only queries; stops rejecting keywords in strings/comments.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Row limiting and session reset in DatabaseManager

**Files:**
- Modify: `src/mcp_sql_server/pool.py` (`PooledConnection`, `release`)
- Modify: `src/mcp_sql_server/database.py` (`SessionStateError`, `get_cursor`, `execute_query`)
- Modify: `tests/conftest.py` (`mock_cursor` fixture)
- Test: `tests/test_database.py`, `tests/test_pool.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `class SessionStateError(RuntimeError)` in `mcp_sql_server.database`
  - `PooledConnection.invalid: bool = False`
  - `DatabaseManager.execute_query(sql: str, params: tuple[Any, ...] | None = None, max_rows: int | None = None, server_limit: bool = False) -> list[dict[str, Any]]`
    - `max_rows` → `fetchmany(max_rows)` instead of `fetchall()`
    - `server_limit=True` (requires `max_rows`) → `SET ROWCOUNT max_rows` before, and reset + `DB_NAME()` check after

- [ ] **Step 1: Extend the cursor fixture**

In `tests/conftest.py`, replace the body of `mock_cursor` with:

```python
    cursor = MagicMock()
    cursor.description = [
        ("id", int, None, None, None, None, None),
        ("name", str, None, None, None, None, None),
        ("value", float, None, None, None, None, None),
    ]
    rows = [
        (1, "test1", 10.5),
        (2, "test2", 20.5),
        (3, "test3", 30.5),
    ]
    cursor.fetchall.return_value = rows
    cursor.fetchmany.side_effect = lambda n: rows[:n]
    # Matches sample_config.database for the post-query DB_NAME() check.
    cursor.fetchone.return_value = ("test-db",)
    cursor.rowcount = 3
    cursor.execute = MagicMock()
    cursor.close = MagicMock()
    return cursor
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_database.py`:

```python
from mcp_sql_server.database import SessionStateError  # noqa: E402


class TestExecuteQueryRowLimit:
    def test_fetchall_without_max_rows(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        rows = db.execute_query("SELECT * FROM t")
        assert len(rows) == 3
        mock_cursor.fetchmany.assert_not_called()
        executed = [c.args for c in mock_cursor.execute.call_args_list]
        assert executed == [("SELECT * FROM t",)]

    def test_max_rows_uses_fetchmany_only(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        rows = db.execute_query("EXEC [dbo].[p]", max_rows=2)
        assert len(rows) == 2
        mock_cursor.fetchmany.assert_called_once_with(2)
        executed = [c.args for c in mock_cursor.execute.call_args_list]
        assert executed == [("EXEC [dbo].[p]",)]

    def test_server_limit_sequence(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        rows = db.execute_query("SELECT * FROM t WHERE a = ?", ("x",), max_rows=2, server_limit=True)
        assert len(rows) == 2
        executed = [c.args for c in mock_cursor.execute.call_args_list]
        assert executed == [
            ("SET ROWCOUNT 2",),
            ("SELECT * FROM t WHERE a = ?", ("x",)),
            ("SET ROWCOUNT 0",),
            ("SELECT DB_NAME()",),
        ]
        mock_cursor.cancel.assert_called_once()

    def test_server_limit_requires_max_rows(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(ValueError, match="max_rows"):
            db.execute_query("SELECT 1", server_limit=True)

    def test_reset_runs_even_when_query_fails(self, mock_pyodbc, mock_cursor, sample_config):
        def execute(sql, *args):
            if sql == "SELECT bad":
                raise pyodbc.Error("boom")
        mock_cursor.execute.side_effect = execute
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(pyodbc.Error):
            db.execute_query("SELECT bad", max_rows=5, server_limit=True)
        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert executed[-2:] == ["SET ROWCOUNT 0", "SELECT DB_NAME()"]

    def test_failed_reset_raises_session_state_error(self, mock_pyodbc, mock_cursor, sample_config):
        def execute(sql, *args):
            if sql == "SET ROWCOUNT 0":
                raise pyodbc.Error("busy")
        mock_cursor.execute.side_effect = execute
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(SessionStateError):
            db.execute_query("SELECT 1", max_rows=5, server_limit=True)

    def test_changed_database_raises_session_state_error(self, mock_pyodbc, mock_cursor, sample_config):
        mock_cursor.fetchone.return_value = ("master",)
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(SessionStateError, match="database changed"):
            db.execute_query("SELECT 1", max_rows=5, server_limit=True)

    def test_non_pooled_session_error_drops_connection(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        mock_cursor.fetchone.return_value = ("master",)
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(SessionStateError):
            db.execute_query("SELECT 1", max_rows=5, server_limit=True)
        assert db._connection is None
        mock_connection.close.assert_called()

    def test_pooled_session_error_retires_connection(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        mock_cursor.fetchone.return_value = ("master",)
        db = DatabaseManager(sample_config)
        with pytest.raises(SessionStateError):
            db.execute_query("SELECT 1", max_rows=5, server_limit=True)
        assert db._get_pool().available == 0
        mock_connection.close.assert_called()
        db.close()


class TestGetCursorErrorOrdering:
    def test_cursor_closed_before_rollback(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        order = MagicMock()
        order.attach_mock(mock_cursor.close, "close")
        order.attach_mock(mock_connection.rollback, "rollback")
        db = DatabaseManager(sample_config)
        with pytest.raises(RuntimeError):
            with db.get_cursor():
                order.reset_mock()  # ignore any calls made while acquiring
                raise RuntimeError("fail")
        names = [c[0] for c in order.mock_calls]
        assert names.index("close") < names.index("rollback")
        assert names.count("close") == 1
        db.close()
```

Append to `tests/test_pool.py`:

```python
class TestInvalidConnections:
    def test_invalid_connection_is_closed_on_release(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            pooled_conn = pool.acquire()
            pooled_conn.invalid = True
            pool.release(pooled_conn)

            assert pool.available == 0
            mock_conn.close.assert_called()
            pool.close()

    def test_new_connection_is_valid(self):
        assert PooledConnection(connection=MagicMock()).invalid is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_database.py tests/test_pool.py -q`
Expected: `ImportError: cannot import name 'SessionStateError'` (collection error).

- [ ] **Step 4: Add `invalid` to `PooledConnection` and honor it in `release`**

In `src/mcp_sql_server/pool.py`, add a field to `PooledConnection` after `use_count: int = 0`:

```python
    # Set when session state could not be restored; release() closes it.
    invalid: bool = False
```

In `ConnectionPool.release`, directly after the `if self._closed:` block, insert:

```python
        if pooled_conn.invalid:
            self._close_connection(pooled_conn)
            return
```

- [ ] **Step 5: Add `SessionStateError`, reorder `get_cursor`, extend `execute_query`**

In `src/mcp_sql_server/database.py`, after `logger = logging.getLogger(__name__)` add:

```python
class SessionStateError(RuntimeError):
    """A connection's session state could not be restored after a query.

    The connection must not be reused: pooled connections are retired and a
    non-pooled connection is closed.
    """
```

Replace the whole `get_cursor` method with:

```python
    @contextmanager
    def get_cursor(self) -> Generator[pyodbc.Cursor, None, None]:
        """Context manager for cursor with automatic cleanup.

        When pooling is enabled, acquires a connection from the pool and
        releases it after the cursor is closed. On error the cursor is closed
        before rolling back: a pending result set would make rollback fail
        with "Connection is busy with results for another command".
        """
        if self._use_pool:
            pool = self._get_pool()
            with pool.connection() as pooled_conn:
                cursor = pooled_conn.connection.cursor()
                closed = False
                try:
                    yield cursor
                except Exception as e:
                    if isinstance(e, SessionStateError):
                        pooled_conn.invalid = True
                    self._close_cursor(cursor)
                    closed = True
                    try:
                        pooled_conn.connection.rollback()
                    except Exception:
                        logger.debug("Rollback failed during error handling")
                    if isinstance(e, pyodbc.Error):
                        logger.error(f"Database error: {e}")
                    raise
                finally:
                    if not closed:
                        cursor.close()
        else:
            conn = self.connect()
            cursor = conn.cursor()
            closed = False
            try:
                yield cursor
            except Exception as e:
                self._close_cursor(cursor)
                closed = True
                if isinstance(e, SessionStateError):
                    self._drop_connection()
                else:
                    try:
                        conn.rollback()
                    except Exception:
                        logger.debug("Rollback failed during error handling")
                if isinstance(e, pyodbc.Error):
                    logger.error(f"Database error: {e}")
                raise
            finally:
                if not closed:
                    cursor.close()

    @staticmethod
    def _close_cursor(cursor: pyodbc.Cursor) -> None:
        try:
            cursor.close()
        except Exception:
            logger.debug("Cursor close failed during error handling")

    def _drop_connection(self) -> None:
        """Close and forget the non-pooled connection."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                logger.debug("Connection close failed")
            self._connection = None
```

Replace the whole `execute_query` method with:

```python
    def execute_query(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        max_rows: int | None = None,
        server_limit: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute a query and return results as list of dicts.

        Args:
            sql: SQL to execute, unmodified.
            params: Positional parameters for ``?`` placeholders.
            max_rows: Read at most this many rows (``fetchmany``).
            server_limit: Also cap rows on the server with ``SET ROWCOUNT``.
                Requires ``max_rows``. Do not use for stored procedures:
                ROWCOUNT would also limit DML inside them.

        Raises:
            SessionStateError: the session could not be reset afterwards; the
                connection is retired.
        """
        if server_limit and max_rows is None:
            raise ValueError("server_limit requires max_rows")

        with self.get_cursor() as cursor:
            if server_limit:
                # Literal int, not a parameter: a parameterized SET runs inside
                # sp_executesql and reverts when that call returns.
                cursor.execute(f"SET ROWCOUNT {int(max_rows or 0)}")
            try:
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)

                if cursor.description is None:
                    return []

                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchmany(max_rows) if max_rows is not None else cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
            finally:
                if server_limit:
                    self._reset_session(cursor)

    def _reset_session(self, cursor: pyodbc.Cursor) -> None:
        """Undo SET ROWCOUNT and confirm the session is still on our database."""
        try:
            cursor.cancel()
            cursor.execute("SET ROWCOUNT 0")
            cursor.execute("SELECT DB_NAME()")
            row = cursor.fetchone()
        except pyodbc.Error as e:
            raise SessionStateError("could not reset session state") from e
        if row is None or row[0] != self.config.database:
            raise SessionStateError("session database changed")
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/test_database.py tests/test_pool.py tests/test_concurrency.py -q`
Expected: all pass.

- [ ] **Step 7: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_sql_server/pool.py src/mcp_sql_server/database.py tests/conftest.py tests/test_database.py tests/test_pool.py
git commit -m "feat(database): SET ROWCOUNT row limiting with session reset

Adds max_rows/server_limit to execute_query, retires connections whose
session cannot be reset or whose database changed, and closes the cursor
before rollback on errors.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire row limiting into tools; remove the TOP wrapper; cap procedures

**Files:**
- Modify: `src/mcp_sql_server/tools/query_execution.py` (delete `_inject_top_clause`; `execute_query` call)
- Modify: `src/mcp_sql_server/tools/stored_procedures.py` (`execute_procedure`)
- Delete: `tests/test_inject_top_clause.py`
- Test: `tests/test_server.py`
- Docs: `README.md` (`execute_query`, `execute_procedure`), `.claude/rules/mcp-tools.md` (Resource Limits, API Response Format)

**Interfaces:**
- Consumes: `DatabaseManager.execute_query(sql, params, max_rows=..., server_limit=...)` from Task 3.
- Produces: `execute_procedure` success response gains `"truncated": bool`; constant `MAX_PROCEDURE_ROWS = 10_000` in `stored_procedures.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
class TestRowLimitWiring:
    def test_execute_query_passes_sql_unmodified(self, mock_query_results):
        with patch.object(query_execution, "_get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_query_results
            mock_get_db.return_value = mock_db

            sql = "WITH c AS (SELECT 1 x) SELECT * FROM c ORDER BY x"
            result = execute_query(sql, limit=50)

            assert result["success"] is True
            args, kwargs = mock_db.execute_query.call_args
            assert args[0] == sql
            assert kwargs == {"max_rows": 51, "server_limit": True}

    def test_execute_query_reports_truncation(self):
        rows = [{"id": i} for i in range(51)]
        with patch.object(query_execution, "_get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = rows
            mock_get_db.return_value = mock_db

            result = execute_query("SELECT id FROM t", limit=50)

            assert result["truncated"] is True
            assert result["row_count"] == 50

    def test_execute_procedure_caps_rows(self):
        from mcp_sql_server.tools import stored_procedures

        rows = [{"id": i} for i in range(stored_procedures.MAX_PROCEDURE_ROWS + 1)]
        with patch.object(stored_procedures, "_get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = rows
            mock_get_db.return_value = mock_db

            result = execute_procedure("GetAll")

            assert result["success"] is True
            assert result["truncated"] is True
            assert result["row_count"] == stored_procedures.MAX_PROCEDURE_ROWS
            _, kwargs = mock_db.execute_query.call_args
            assert kwargs == {"max_rows": stored_procedures.MAX_PROCEDURE_ROWS + 1}

    def test_execute_procedure_not_truncated(self, mock_procedure_results):
        from mcp_sql_server.tools import stored_procedures

        with patch.object(stored_procedures, "_get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = mock_procedure_results
            mock_get_db.return_value = mock_db

            result = execute_procedure("GetUserById", params={"UserId": 1})

            assert result["truncated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -q -k TestRowLimitWiring`
Expected: FAIL — SQL is wrapped in `SELECT TOP`, kwargs missing, no `MAX_PROCEDURE_ROWS`.

- [ ] **Step 3: Remove the wrapper and pass limits**

In `src/mcp_sql_server/tools/query_execution.py`:
- Delete the whole `_inject_top_clause` function.
- In `execute_query`, delete these two lines:

```python
    # Inject TOP clause to limit results at database level
    limited_sql = _inject_top_clause(sql, limit)
```

- Replace:

```python
            results = _get_db(database).execute_query(limited_sql, params_tuple)
```

with:

```python
            # Fetch limit + 1 so truncation can be detected. The SQL runs
            # unmodified; the manager caps rows with SET ROWCOUNT.
            results = _get_db(database).execute_query(
                sql, params_tuple, max_rows=limit + 1, server_limit=True
            )
```

Delete `tests/test_inject_top_clause.py`:

```bash
git rm tests/test_inject_top_clause.py
```

- [ ] **Step 4: Cap procedure results**

In `src/mcp_sql_server/tools/stored_procedures.py`, after `METADATA_CACHE_TTL = 60` add:

```python
# Maximum rows returned by execute_procedure. SET ROWCOUNT is not used here:
# it would also limit INSERT/UPDATE/DELETE inside the procedure.
MAX_PROCEDURE_ROWS = 10_000
```

In `execute_procedure`, replace the two `execute_query` calls:

```python
                results = _get_db(database).execute_query(sql, param_values)
```

→

```python
                results = _get_db(database).execute_query(
                    sql, param_values, max_rows=MAX_PROCEDURE_ROWS + 1
                )
```

and

```python
                results = _get_db(database).execute_query(sql)
```

→

```python
                results = _get_db(database).execute_query(
                    sql, max_rows=MAX_PROCEDURE_ROWS + 1
                )
```

Then, before `audit_logger.log_procedure(` in the success path, insert:

```python
            truncated = len(results) > MAX_PROCEDURE_ROWS
            if truncated:
                results = results[:MAX_PROCEDURE_ROWS]
```

and change the success return to:

```python
            return {
                "success": True,
                "results": results,
                "row_count": len(results),
                "truncated": truncated,
            }
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_server.py -q`
Expected: all pass. If an existing test asserted the executed SQL contains `SELECT TOP`, it would fail here; none exists at time of writing (`grep -n "TOP" tests/test_server.py` only shows SQL literals in test inputs).

- [ ] **Step 6: Update docs**

`README.md`, under `### \`execute_query\``, replace the paragraph starting `The query is automatically wrapped with` with:

```markdown
The SQL runs unmodified (CTEs, `ORDER BY`, and `UNION` all work). Rows are capped on the server with `SET ROWCOUNT (limit+1)` and read with `fetchmany`; the session is reset afterwards and any connection whose state cannot be restored is retired. If more than `limit` rows exist, `truncated` is set to `True`.
```

`README.md`, under `### \`execute_procedure\``, after `System procedures (\`xp_*\`, \`sp_*\`) are blocked. Parameter names are validated as safe identifiers.` add:

```markdown
At most 10,000 rows are returned; the response includes `"truncated": true` when more rows existed. Only the first result set is read.
```

`.claude/rules/mcp-tools.md`, in `## Resource Limits`, add a row after the `Query result rows` row:

```markdown
| Procedure result rows | 10,000 | 10,000 | `execute_procedure` (reports `truncated`) |
```

and under the table add:

```markdown
`execute_query` runs SQL unmodified and caps rows server-side with `SET ROWCOUNT (limit+1)`, then `fetchmany`. CTEs and `ORDER BY` work.
```

- [ ] **Step 7: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 8: Commit**

```bash
git add -A src/mcp_sql_server/tools tests/test_server.py README.md .claude/rules/mcp-tools.md
git commit -m "fix(query): run SQL unmodified; cap rows via SET ROWCOUNT

Removes the SELECT TOP wrapper that broke CTE and ORDER BY queries.
execute_procedure now returns at most 10,000 rows and reports truncated.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Masked audit preview and shape hash

**Files:**
- Modify: `src/mcp_sql_server/audit.py:12-37`
- Test: `tests/test_audit.py` (new)
- Docs: `README.md` Audit Logging subsection

**Interfaces:**
- Consumes: `tokenize`, `TokenKind`, `LexError` from Task 1.
- Produces: `_mask_sql(sql: str) -> str | None`; `_get_sql_preview(sql: str, max_length: int = 100) -> str`; `_hash_sql(sql: str) -> str` (16 hex chars, of masked SQL).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_audit.py -q`
Expected: `ImportError: cannot import name '_mask_sql'`.

- [ ] **Step 3: Implement**

In `src/mcp_sql_server/audit.py`, add after the existing imports:

```python
from .sql_lexer import LexError, TokenKind, tokenize

_MASKED_KINDS = (TokenKind.STRING, TokenKind.NUMBER)
```

Replace `_hash_sql` and `_get_sql_preview` (lines 12–37) with:

```python
def _mask_sql(sql: str) -> str | None:
    """Rebuild SQL with literals replaced by '?' and comments removed.

    String and numeric literals, and "double-quoted" tokens (strings under
    QUOTED_IDENTIFIER OFF), become '?'. Any gap between tokens (whitespace or
    comments) becomes one space; adjacent tokens stay adjacent.

    Returns:
        The masked SQL, or None if the SQL cannot be tokenized.
    """
    try:
        tokens = tokenize(sql)
    except LexError:
        return None
    parts: list[str] = []
    prev_end: int | None = None
    for tok in tokens:
        if prev_end is not None and tok.start > prev_end:
            parts.append(" ")
        masked = tok.kind in _MASKED_KINDS or (
            tok.kind is TokenKind.QUOTED_IDENT and tok.text.startswith('"')
        )
        parts.append("?" if masked else tok.text)
        prev_end = tok.end
    return "".join(parts)


def _hash_sql(sql: str) -> str:
    """Fingerprint the query shape for audit logs.

    Hashes the masked SQL, so queries differing only in literals share a
    hash and literal values cannot be brute-forced from it.

    Returns:
        First 16 characters of the SHA-256 hex digest
    """
    masked = _mask_sql(sql)
    source = masked if masked is not None else sql
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def _get_sql_preview(sql: str, max_length: int = 100) -> str:
    """Get a masked, truncated preview of SQL for logging."""
    masked = _mask_sql(sql)
    if masked is None:
        return "<unparseable>"
    if len(masked) > max_length:
        return masked[:max_length] + "..."
    return masked
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_audit.py tests/test_server.py -q`
Expected: all pass.

- [ ] **Step 5: Update docs**

In `README.md` `### Audit Logging`, replace the bullet list and the sentence after it with:

```markdown
- **Queries:** SQL hash, masked preview (first 100 chars), duration, row count, truncation status, target database
- **Statements:** SQL hash, masked preview, statement type (INSERT/UPDATE/DELETE), duration, affected rows, target database
- **Procedures:** Procedure name, schema, duration, row count, target database
- **Validation Failures:** SQL hash, short masked preview, error, target database

Previews replace every string and numeric literal with `?` and drop comments (`WHERE cpf = '123'` → `WHERE cpf = ?`). `sql_hash` is the first 16 hex chars of the SHA-256 of the masked SQL: a query-shape fingerprint, so queries that differ only in literal values share a hash and the values cannot be recovered from it.
```

- [ ] **Step 6: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 7: Commit**

```bash
git add src/mcp_sql_server/audit.py tests/test_audit.py README.md
git commit -m "fix(audit): mask literals in SQL preview and hash query shape

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Driver-value redaction; remove unused exception classes

**Files:**
- Modify: `src/mcp_sql_server/errors.py` (`SENSITIVE_PATTERNS`; delete lines 106–end)
- Test: `tests/test_errors.py` (new)
- Docs: `README.md` Module Responsibilities `errors.py` row, `### Error Sanitization`, `### Error Handling`

**Interfaces:**
- Consumes: nothing.
- Produces: unchanged `sanitize_error`, `simplify_error`, `create_error_response`; classes `MCPError`, `ValidationError`, `ConnectionError`, `QueryError`, `TimeoutError` no longer exist.

- [ ] **Step 1: Confirm the classes are unused**

Run: `grep -rn "MCPError\|errors import.*Error\b\|QueryError" src tests .claude`
Expected: matches only inside `src/mcp_sql_server/errors.py`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_errors.py`:

```python
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

    def test_object_names_kept(self):
        assert "dbo.Req" in sanitize_error("Invalid object name 'dbo.Req'.")

    def test_credentials_still_redacted(self):
        out = sanitize_error("Login failed for user 'admin'. SERVER=10.0.0.5;PWD=x;")
        assert "admin" not in out
        assert "10.0.0.5" not in out

    def test_response_uses_redaction(self):
        resp = create_error_response("Truncated value: 'secret'.")
        assert "secret" not in resp["error"]


@pytest.mark.parametrize(
    "name", ["MCPError", "ValidationError", "ConnectionError", "QueryError", "TimeoutError"]
)
def test_unused_exception_classes_removed(name):
    assert not hasattr(errors, name)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_errors.py -q`
Expected: redaction tests FAIL (values present); removal tests FAIL (classes exist).

- [ ] **Step 4: Implement**

In `src/mcp_sql_server/errors.py`, add these entries at the end of `SENSITIVE_PATTERNS` (before the commented-out database-name pattern):

```python
    # Data values echoed by SQL Server in constraint/conversion errors
    (r"The duplicate key value is \([^\r\n]*\)", "The duplicate key value is ([REDACTED])"),
    (r"Truncated value: '(?:[^']|'')*'", "Truncated value: '[REDACTED]'"),
    (
        r"(Conversion failed when converting the [\w ]+? value )'(?:[^']|'')*'",
        r"\1'[REDACTED]'",
    ),
```

Delete everything from `class MCPError(Exception):` to the end of the file.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_errors.py tests/test_server.py -q`
Expected: all pass.

- [ ] **Step 6: Update docs**

`README.md` Module Responsibilities: replace the `errors.py` row with:

```markdown
| `errors.py` | Error sanitization (credentials, IPs, echoed data values), simplification of common SQL Server errors, error-response builder |
```

`README.md` Project Structure tree: change the `errors.py` comment to `# Error sanitization and error responses`.

`README.md` `### Error Sanitization`: replace the bullet list with:

```markdown
- IP addresses
- Usernames and passwords
- Connection string details (SERVER, UID, PWD)
- Data values SQL Server echoes in errors: duplicate key values (2627/2601), truncated values (2628), and values in conversion failures (245)

Object names (e.g. `Invalid object name 'dbo.Req'`) are kept for debugging.
```

`README.md` `### Error Handling`: replace from `Custom exception hierarchy with consistent response format:` through the closing ``` of the hierarchy diagram with:

```markdown
Tools never raise to the client. Every failure is returned as a sanitized error dictionary:
```

(keep the JSON example and the "Common SQL Server errors are automatically simplified" sentence that follow).

- [ ] **Step 7: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_sql_server/errors.py tests/test_errors.py README.md
git commit -m "fix(errors): redact data values echoed by SQL Server; drop unused exceptions

Removes MCPError hierarchy (never raised; ConnectionError/TimeoutError
shadowed builtins).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Connection hygiene and clarifying comments

**Files:**
- Modify: `src/mcp_sql_server/database.py` (`connect`, `_is_connected`, remove `import warnings`)
- Modify: `src/mcp_sql_server/pool.py` (comment in `acquire`)
- Modify: `src/mcp_sql_server/registry.py` (`close` docstring)
- Modify: `tests/test_database.py` (three existing tests, listed below), `tests/test_concurrency.py` (module docstring)

**Interfaces:**
- Consumes: `DatabaseManager._drop_connection()` from Task 3.
- Produces: `DatabaseManager.connect()` raises `RuntimeError` when `use_pool=True`.

- [ ] **Step 1: Update the three existing tests and add new ones**

In `tests/test_database.py`:

Replace `test_connect_emits_deprecation_warning_when_pooled` with:

```python
    def test_connect_raises_when_pooled(self, mock_pyodbc, sample_config):
        """connect() is not supported with pooling; get_cursor() is the API."""
        db = DatabaseManager(sample_config, use_pool=True)
        with pytest.raises(RuntimeError, match="get_cursor"):
            db.connect()
        db.close()
```

In `TestDatabaseManagerIsConnected`, change `test_is_connected_true_when_valid` and `test_is_connected_false_on_pyodbc_error` so each constructs the manager with `DatabaseManager(sample_config, use_pool=False)` (they call `connect()`).

Append to `TestDatabaseManagerIsConnected`:

```python
    def test_is_connected_rolls_back(self, mock_pyodbc, mock_connection, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.connect()
        mock_connection.rollback.reset_mock()
        assert db._is_connected() is True
        mock_connection.rollback.assert_called_once()

    def test_reconnect_closes_dead_connection(self, mock_pyodbc, sample_config):
        dead = MagicMock()
        dead.execute.side_effect = pyodbc.Error("gone")
        db = DatabaseManager(sample_config, use_pool=False)
        db._connection = dead
        db.connect()
        dead.close.assert_called_once()
        assert db._connection is not dead
```

If the module no longer uses `warnings` after this edit, remove `import warnings` from the top of `tests/test_database.py` only if nothing else in the file uses it (`grep -n "warnings" tests/test_database.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_database.py -q`
Expected: `test_connect_raises_when_pooled`, `test_is_connected_rolls_back`, `test_reconnect_closes_dead_connection` FAIL.

- [ ] **Step 3: Implement `connect` and `_is_connected`**

In `src/mcp_sql_server/database.py`, remove `import warnings`. Replace the whole `connect` method with:

```python
    def connect(self) -> pyodbc.Connection:
        """Return the non-pooled connection, opening or reopening it as needed.

        Only valid when pooling is disabled. With pooling, use get_cursor(),
        which acquires and releases pool connections safely.

        Not thread-safe: it mutates `_connection` without a lock. Non-pooled
        mode is not used on any tool path.

        Raises:
            RuntimeError: if pooling is enabled.
        """
        if self._use_pool:
            raise RuntimeError(
                "connect() is not supported when pooling is enabled; use get_cursor()"
            )

        if self._connection is None or not self._is_connected():
            self._drop_connection()
            self._connection = pyodbc.connect(
                self.config.get_connection_string(),
                timeout=self.config.connection_timeout,
            )
            self._connection.timeout = self.config.query_timeout
        return self._connection
```

Replace `_is_connected` with:

```python
    def _is_connected(self) -> bool:
        """Check if the connection is still valid (rolls back the probe)."""
        if self._connection is None:
            return False
        try:
            self._connection.execute("SELECT 1")
            self._connection.rollback()
            return True
        except (pyodbc.Error, AttributeError):
            return False
```

- [ ] **Step 4: Comments and docstrings**

In `src/mcp_sql_server/pool.py` `acquire`, directly above `pooled_conn = self._pool.get(timeout=min(remaining, 0.1))` add:

```python
                # Short timeout on purpose: release() can retire a connection,
                # freeing a creation slot without putting anything on the
                # queue. Waking every 100ms lets a waiter notice that slot
                # instead of blocking until acquire_timeout.
```

In `src/mcp_sql_server/registry.py`, replace the `close` docstring with:

```python
        """Close all DatabaseManager instances and their pools.

        Shutdown path: every manager is closed even if one fails. Failures are
        logged, not raised, so one bad pool cannot leave the others open.
        Use close_database() to close one database and see its error.
        """
```

In `tests/test_concurrency.py`, replace the module docstring's last paragraph (`The tool path is ... see its docstring.`) with:

```
The tool path is DatabaseManager.get_cursor() -> ConnectionPool.connection(),
which is lock-protected. DatabaseManager.connect() raises when pooling is
enabled, so it is not reachable from tools.
```

- [ ] **Step 5: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 6: Commit**

```bash
git add src/mcp_sql_server/database.py src/mcp_sql_server/pool.py src/mcp_sql_server/registry.py tests/test_database.py tests/test_concurrency.py
git commit -m "fix(database): forbid pooled connect(), roll back health probe, close dead conn

connect() with pooling leaked a pool slot via the never-read
_current_pooled_conn; it now raises. Documents why acquire() polls and why
registry close() logs instead of raising.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Safe config parsing and per-alias loaders

**Files:**
- Modify: `src/mcp_sql_server/config.py`
- Test: `tests/test_config.py`, `tests/test_config_multi.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all in `mcp_sql_server.config`):
  - `DEFAULT_ENV_PATH: Path` (repo-root `.env`)
  - `load_database_config(name: str, env_path: Path | None = None) -> DatabaseConfig`
  - `load_pool_config(name: str, env_path: Path | None = None) -> PoolConfig`
  - `describe_config_error(exc: ValueError) -> str` — never contains input values
  - Integer/float env vars raise `ValueError("<VAR> must be an integer")` / `"... must be a number"` with no value in the message.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from unittest.mock import patch as _patch  # noqa: E402

from mcp_sql_server.config import (  # noqa: E402
    DEFAULT_ENV_PATH,
    DatabaseConfig as _DatabaseConfig,
    PoolConfig as _PoolConfig,
    describe_config_error,
    load_database_config,
    load_pool_config,
)


class TestSafeConfigErrors:
    def _env(self, **extra: str) -> dict[str, str]:
        base = {"DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "pw-secret", "DB_NAME": "d"}
        base.update(extra)
        return base

    def test_bad_int_names_var_without_value(self, tmp_path):
        with _patch.dict("os.environ", self._env(DB_TIMEOUT="s3cr3t!"), clear=True):
            with pytest.raises(ValueError) as exc_info:
                _DatabaseConfig.from_env(tmp_path / "none.env")
        assert "DB_TIMEOUT" in str(exc_info.value)
        assert "s3cr3t!" not in str(exc_info.value)
        assert exc_info.value.__cause__ is None

    def test_bad_port_names_var_without_value(self, tmp_path):
        with _patch.dict("os.environ", self._env(DB_PORT="s3cr3t!"), clear=True):
            with pytest.raises(ValueError) as exc_info:
                _DatabaseConfig.from_env(tmp_path / "none.env")
        assert "PORT" in str(exc_info.value)
        assert "s3cr3t!" not in str(exc_info.value)

    def test_bad_pool_float(self, tmp_path):
        with _patch.dict("os.environ", {"DB_POOL_ACQUIRE_TIMEOUT": "s3cr3t!"}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                _PoolConfig.from_env(tmp_path / "none.env")
        assert "DB_POOL_ACQUIRE_TIMEOUT" in str(exc_info.value)
        assert "s3cr3t!" not in str(exc_info.value)

    def test_describe_validation_error_has_no_values(self, tmp_path):
        env = self._env(DB_PASSWORD="")
        with _patch.dict("os.environ", env, clear=True):
            try:
                _DatabaseConfig.from_env(tmp_path / "none.env")
            except ValueError as e:
                message = describe_config_error(e)
        assert message == "password: string_too_short"

    def test_describe_pool_min_max(self, tmp_path):
        with _patch.dict("os.environ", {"DB_POOL_MIN_SIZE": "9", "DB_POOL_MAX_SIZE": "1"}, clear=True):
            try:
                _PoolConfig.from_env(tmp_path / "none.env")
            except ValueError as e:
                message = describe_config_error(e)
        assert message == "config: value_error"

    def test_describe_plain_value_error(self):
        assert describe_config_error(ValueError("DB_PORT must be an integer")) == "DB_PORT must be an integer"


class TestPerAliasLoaders:
    def test_default_alias_uses_from_env(self, tmp_path):
        env = {"DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p", "DB_NAME": "d"}
        with _patch.dict("os.environ", env, clear=True):
            cfg = load_database_config("default", tmp_path / "none.env")
        assert cfg.host == "h"

    def test_named_alias_uses_prefix(self, tmp_path):
        env = {"DB_X_HOST": "hx", "DB_X_USER": "u", "DB_X_PASSWORD": "p", "DB_X_NAME": "d"}
        with _patch.dict("os.environ", env, clear=True):
            cfg = load_database_config("x", tmp_path / "none.env")
        assert cfg.host == "hx"

    def test_pool_loader(self, tmp_path):
        with _patch.dict("os.environ", {"DB_X_POOL_MAX_SIZE": "7"}, clear=True):
            assert load_pool_config("x", tmp_path / "none.env").max_size == 7

    def test_default_env_path_is_repo_root(self):
        assert DEFAULT_ENV_PATH.name == ".env"
        assert (DEFAULT_ENV_PATH.parent / "pyproject.toml").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: `ImportError: cannot import name 'DEFAULT_ENV_PATH'`.

- [ ] **Step 3: Implement**

In `src/mcp_sql_server/config.py`:

After `_ALIAS_PATTERN = ...` add:

```python
# Repository-root .env (works for editable installs)
DEFAULT_ENV_PATH = Path(__file__).parent.parent.parent / ".env"


def _parse_int(raw: str, name: str) -> int:
    """Parse an integer env value; the error names the variable, never the value."""
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _parse_float(raw: str, name: str) -> float:
    """Parse a numeric env value; the error names the variable, never the value."""
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
```

Replace every `Path(__file__).parent.parent.parent / ".env"` in the file with `DEFAULT_ENV_PATH`.

In `PoolConfig.from_env`, replace the `return cls(...)` with:

```python
        return cls(
            min_size=_parse_int(os.getenv("DB_POOL_MIN_SIZE", "1"), "DB_POOL_MIN_SIZE"),
            max_size=_parse_int(os.getenv("DB_POOL_MAX_SIZE", "5"), "DB_POOL_MAX_SIZE"),
            idle_timeout=_parse_int(os.getenv("DB_POOL_IDLE_TIMEOUT", "300"), "DB_POOL_IDLE_TIMEOUT"),
            health_check_interval=_parse_int(
                os.getenv("DB_POOL_HEALTH_CHECK_INTERVAL", "30"), "DB_POOL_HEALTH_CHECK_INTERVAL"
            ),
            acquire_timeout=_parse_float(
                os.getenv("DB_POOL_ACQUIRE_TIMEOUT", "10.0"), "DB_POOL_ACQUIRE_TIMEOUT"
            ),
            max_lifetime=_parse_int(os.getenv("DB_POOL_MAX_LIFETIME", "3600"), "DB_POOL_MAX_LIFETIME"),
        )
```

In `PoolConfig.from_env_prefixed`, replace the `return cls(...)` with:

```python
        def var(suffix: str) -> str:
            return f"DB_{p}_POOL_{suffix}"

        return cls(
            min_size=_parse_int(os.getenv(var("MIN_SIZE"), "1"), var("MIN_SIZE")),
            max_size=_parse_int(os.getenv(var("MAX_SIZE"), "5"), var("MAX_SIZE")),
            idle_timeout=_parse_int(os.getenv(var("IDLE_TIMEOUT"), "300"), var("IDLE_TIMEOUT")),
            health_check_interval=_parse_int(
                os.getenv(var("HEALTH_CHECK_INTERVAL"), "30"), var("HEALTH_CHECK_INTERVAL")
            ),
            acquire_timeout=_parse_float(
                os.getenv(var("ACQUIRE_TIMEOUT"), "10.0"), var("ACQUIRE_TIMEOUT")
            ),
            max_lifetime=_parse_int(os.getenv(var("MAX_LIFETIME"), "3600"), var("MAX_LIFETIME")),
        )
```

In `DatabaseConfig.from_env`, replace the three `int(...)` calls:

```python
            port=_parse_int(_get("SQL_SERVER_PORT", "DB_PORT", "1433"), "SQL_SERVER_PORT/DB_PORT"),
            ...
            connection_timeout=_parse_int(os.getenv("DB_TIMEOUT", "30"), "DB_TIMEOUT"),
            query_timeout=_parse_int(os.getenv("DB_QUERY_TIMEOUT", "120"), "DB_QUERY_TIMEOUT"),
```

In `DatabaseConfig.from_env_prefixed`, replace the three `int(...)` calls:

```python
            port=_parse_int(os.getenv(f"DB_{p}_PORT", "1433"), f"DB_{p}_PORT"),
            ...
            connection_timeout=_parse_int(os.getenv(f"DB_{p}_TIMEOUT", "30"), f"DB_{p}_TIMEOUT"),
            query_timeout=_parse_int(os.getenv(f"DB_{p}_QUERY_TIMEOUT", "120"), f"DB_{p}_QUERY_TIMEOUT"),
```

Replace `load_all_database_configs` and `load_all_pool_configs` with:

```python
def load_database_config(name: str, env_path: Path | None = None) -> DatabaseConfig:
    """Load the DatabaseConfig for one alias ("default" uses DB_*/SQL_SERVER_*)."""
    if name == "default":
        return DatabaseConfig.from_env(env_path)
    return DatabaseConfig.from_env_prefixed(name, env_path)


def load_pool_config(name: str, env_path: Path | None = None) -> PoolConfig:
    """Load the PoolConfig for one alias."""
    if name == "default":
        return PoolConfig.from_env(env_path)
    return PoolConfig.from_env_prefixed(name, env_path)


def load_all_database_configs(
    env_path: Path | None = None,
) -> dict[str, "DatabaseConfig"]:
    """Load DatabaseConfig for all configured databases.

    Returns:
        Mapping of alias -> DatabaseConfig. Always includes "default".
    """
    return {name: load_database_config(name, env_path) for name in get_database_names(env_path)}


def load_all_pool_configs(
    env_path: Path | None = None,
) -> dict[str, PoolConfig]:
    """Load PoolConfig for all configured databases.

    Returns:
        Mapping of alias -> PoolConfig. Always includes "default".
    """
    return {name: load_pool_config(name, env_path) for name in get_database_names(env_path)}


def describe_config_error(exc: ValueError) -> str:
    """Describe a config error without any input values.

    pydantic errors become "field: error_type" pairs (e.g.
    "password: string_too_short"). Other ValueErrors raised here name only
    variables or aliases, so their text is used as-is.
    """
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors(include_input=False, include_context=False, include_url=False):
            loc = ".".join(str(p) for p in err["loc"]) or "config"
            parts.append(f"{loc}: {err['type']}")
        return "; ".join(parts)
    return str(exc)
```

Change the pydantic import at the top to:

```python
from pydantic import BaseModel, Field, ValidationError
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_config.py tests/test_config_multi.py -q`
Expected: all pass. (The `min_size > max_size` check raises inside `model_post_init`; pydantic wraps it as a `ValidationError` with an empty `loc` and type `value_error`, hence `config: value_error`.)

- [ ] **Step 5: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 6: Commit**

```bash
git add src/mcp_sql_server/config.py tests/test_config.py
git commit -m "fix(config): value-free parse errors and per-alias config loaders

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Registry tolerates misconfigured aliases

**Files:**
- Modify: `src/mcp_sql_server/registry.py`
- Modify: `src/mcp_sql_server/resources/database_info.py` (`resource_databases`)
- Modify: `tests/test_registry.py` (`test_from_env` rewritten), add tests
- Docs: `README.md` `### \`list_databases\`` response

**Interfaces:**
- Consumes: `get_database_names`, `load_database_config`, `load_pool_config`, `describe_config_error` from Task 8.
- Produces:
  - `DatabaseRegistry(configs, pool_configs=None, config_errors: dict[str, str] | None = None)`
  - `DatabaseRegistry.config_errors -> dict[str, str]` (property, copy)
  - `get(name)` for a misconfigured alias raises `ValueError(f"Database '{name}' is misconfigured: {message}")`
  - `list_databases()` includes misconfigured aliases
  - `get_database_info()` entries gain `"status": "ok"`; misconfigured entries are `{"name", "status": "misconfigured", "error"}`

- [ ] **Step 1: Write the failing tests**

In `tests/test_registry.py`, replace the existing `test_from_env` (the one decorated with two `@patch(...)` lines) with:

```python
    @patch("mcp_sql_server.registry.load_pool_config")
    @patch("mcp_sql_server.registry.load_database_config")
    @patch("mcp_sql_server.registry.get_database_names")
    def test_from_env(self, mock_names, mock_db_config, mock_pool_config, default_config):
        mock_names.return_value = ["default"]
        mock_db_config.return_value = default_config
        mock_pool_config.return_value = PoolConfig()
        registry = DatabaseRegistry.from_env()
        assert "default" in registry.list_databases()
        assert registry.config_errors == {}
```

Append:

```python
class TestMisconfiguredAliases:
    def _from_env_with_bad_alias(self, default_config):
        def db_config(name, env_path=None):
            if name == "broken":
                DatabaseConfig(host="", user="u", password="p-secret", database="d")
            return default_config

        with patch("mcp_sql_server.registry.get_database_names", return_value=["default", "broken"]), \
             patch("mcp_sql_server.registry.load_database_config", side_effect=db_config), \
             patch("mcp_sql_server.registry.load_pool_config", return_value=PoolConfig()):
            return DatabaseRegistry.from_env()

    def test_bad_alias_recorded_not_raised(self, default_config):
        registry = self._from_env_with_bad_alias(default_config)
        assert registry.config_errors == {"broken": "host: string_too_short"}
        assert registry.list_databases() == ["default", "broken"]

    def test_get_bad_alias_raises_safe_message(self, default_config):
        registry = self._from_env_with_bad_alias(default_config)
        with pytest.raises(ValueError, match="Database 'broken' is misconfigured: host: string_too_short") as exc_info:
            registry.get("broken")
        assert "p-secret" not in str(exc_info.value)

    def test_default_still_works(self, default_config, mock_pyodbc):
        registry = self._from_env_with_bad_alias(default_config)
        assert registry.get("default") is registry.get("default")
        registry.close()

    def test_database_info_reports_status(self, default_config):
        info = self._from_env_with_bad_alias(default_config).get_database_info()
        by_name = {d["name"]: d for d in info}
        assert by_name["default"]["status"] == "ok"
        assert by_name["broken"] == {
            "name": "broken",
            "status": "misconfigured",
            "error": "host: string_too_short",
        }

    def test_misconfigured_default_allowed(self, second_config):
        registry = DatabaseRegistry(
            configs={"analytics": second_config},
            config_errors={"default": "host: string_too_short"},
        )
        with pytest.raises(ValueError, match="misconfigured"):
            registry.get("default")

    def test_invalid_db_databases_still_raises(self):
        with patch("mcp_sql_server.registry.get_database_names", side_effect=ValueError("Invalid database alias '1x'")):
            with pytest.raises(ValueError, match="Invalid database alias"):
                DatabaseRegistry.from_env()


def test_resource_databases_shows_status(default_config):
    from mcp_sql_server.resources import database_info

    registry = DatabaseRegistry(
        configs={"default": default_config},
        config_errors={"broken": "host: string_too_short"},
    )
    with patch.object(database_info, "_get_registry", return_value=registry):
        text = database_info.resource_databases()
    assert "| broken | - | - | - | misconfigured: host: string_too_short |" in text
    assert "| default | host1 | 1433 | db1 | ok |" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_registry.py -q`
Expected: failures (`config_errors` unknown, `from_env` raises on bad alias).

- [ ] **Step 3: Implement the registry changes**

In `src/mcp_sql_server/registry.py`, change the config import to:

```python
from .config import (
    DatabaseConfig,
    PoolConfig,
    describe_config_error,
    get_database_names,
    load_database_config,
    load_pool_config,
)
```

Replace `__init__` with:

```python
    def __init__(
        self,
        configs: dict[str, DatabaseConfig],
        pool_configs: dict[str, PoolConfig] | None = None,
        config_errors: dict[str, str] | None = None,
    ) -> None:
        """Initialize the registry.

        Args:
            configs: Mapping of alias -> DatabaseConfig for valid databases.
            pool_configs: Optional mapping of alias -> PoolConfig.
                          Missing aliases use PoolConfig defaults.
            config_errors: Mapping of alias -> safe error message for
                           databases whose configuration is invalid.

        Raises:
            ValueError: If "default" is in neither configs nor config_errors.
        """
        self._config_errors = dict(config_errors or {})
        if "default" not in configs and "default" not in self._config_errors:
            raise ValueError("configs must include a 'default' database entry")
        self._configs = configs
        self._pool_configs = pool_configs or {}
        self._managers: dict[str, DatabaseManager] = {}
        self._lock = threading.Lock()

    @property
    def config_errors(self) -> dict[str, str]:
        """Aliases whose configuration failed validation, with safe messages."""
        return dict(self._config_errors)
```

In `get`, directly before `# Validate name exists in config`, insert:

```python
        if name in self._config_errors:
            raise ValueError(
                f"Database '{name}' is misconfigured: {self._config_errors[name]}"
            )
```

Replace `list_databases` and `get_database_info` with:

```python
    def list_databases(self) -> list[str]:
        """Return all configured alias names, including misconfigured ones."""
        return [*self._configs, *(n for n in self._config_errors if n not in self._configs)]

    def get_database_info(self) -> list[dict[str, Any]]:
        """Return connection info for all databases (no passwords).

        Returns:
            One dict per alias. Valid: name, host, port, database, status "ok".
            Misconfigured: name, status "misconfigured", error.
        """
        databases: list[dict[str, Any]] = []
        for name in self.list_databases():
            if name in self._config_errors:
                databases.append({
                    "name": name,
                    "status": "misconfigured",
                    "error": self._config_errors[name],
                })
                continue
            config = self._configs[name]
            databases.append({
                "name": name,
                "host": config.host,
                "port": config.port,
                "database": config.database,
                "status": "ok",
            })
        return databases
```

Replace `from_env` with:

```python
    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "DatabaseRegistry":
        """Create a registry from environment configuration.

        Each alias is loaded independently: an invalid alias is recorded in
        config_errors (with a value-free message) instead of failing the
        whole registry. An invalid DB_DATABASES list still raises.
        """
        configs: dict[str, DatabaseConfig] = {}
        pool_configs: dict[str, PoolConfig] = {}
        errors: dict[str, str] = {}
        for name in get_database_names(env_path):
            try:
                config = load_database_config(name, env_path)
                pool_config = load_pool_config(name, env_path)
            except ValueError as e:
                errors[name] = describe_config_error(e)
                continue
            configs[name] = config
            pool_configs[name] = pool_config
        return cls(configs=configs, pool_configs=pool_configs, config_errors=errors)
```

- [ ] **Step 4: Update the `sqlserver://databases` resource**

In `src/mcp_sql_server/resources/database_info.py` `resource_databases`, replace the table header lines and loop with:

```python
        lines = [
            "# Configured Databases\n",
            "| Name | Host | Port | Database | Status |",
            "|------|------|------|----------|--------|",
        ]
        for db in databases:
            if db.get("status") == "misconfigured":
                lines.append(f"| {db['name']} | - | - | - | misconfigured: {db['error']} |")
            else:
                lines.append(
                    f"| {db['name']} | {db['host']} | {db['port']} | {db['database']} | ok |"
                )
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_registry.py tests/test_server.py -q`
Expected: all pass. If an existing `test_server.py` test asserts the old 4-column databases table, update that assertion to the 5-column format and note it in the commit message.

- [ ] **Step 6: Update docs**

In `README.md` `### \`list_databases\``, replace the JSON example with:

```json
{
  "success": true,
  "databases": [
    {"name": "default", "host": "server1", "port": 1433, "database": "MyDB", "status": "ok"},
    {"name": "archive", "status": "misconfigured", "error": "password: string_too_short"}
  ],
  "count": 2
}
```

and add below it:

```markdown
A database whose configuration is invalid is listed with `"status": "misconfigured"` and a value-free error. Calls that target it return that error; other databases keep working.
```

- [ ] **Step 7: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_sql_server/registry.py src/mcp_sql_server/resources/database_info.py tests/test_registry.py README.md
git commit -m "feat(registry): isolate misconfigured databases instead of failing all

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Request IDs and logging helpers

**Files:**
- Modify: `src/mcp_sql_server/logging_config.py`
- Test: `tests/test_logging_config.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces (in `mcp_sql_server.logging_config`):
  - `request_id_var: ContextVar[str | None]` (unchanged)
  - `class RequestIdFilter(logging.Filter)` — sets `record.request_id` to the current ID or `"-"`
  - `def with_request_id(func: Callable[P, R]) -> Callable[P, R]`
  - `setup_logging(level: str | None = None, log_format: str | None = None) -> None` (text format now includes `[%(request_id)s]`)
  - Removed: `get_logger`, `LoggerAdapter`, `get_logger_with_context`, `set_request_id`, `clear_request_id`

- [ ] **Step 1: Confirm helpers are unused**

Run: `grep -rn "get_logger\b\|get_logger_with_context\|LoggerAdapter\|set_request_id\|clear_request_id" src tests .claude README.md CLAUDE.md`
Expected: matches only in `src/mcp_sql_server/logging_config.py`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_logging_config.py`:

```python
"""Tests for logging configuration and request IDs."""

import inspect
import json
import logging

import pytest

from mcp_sql_server import logging_config
from mcp_sql_server.logging_config import (
    RequestIdFilter,
    request_id_var,
    setup_logging,
    with_request_id,
)


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


class TestSetupLogging:
    def test_text_format_to_stderr(self, capsys):
        setup_logging(level="INFO", log_format="text")
        logging.getLogger("t").info("hello")
        err = capsys.readouterr().err
        assert "hello" in err
        assert "[-]" in err
        assert capsys.readouterr().out == ""

    def test_json_format(self, capsys):
        setup_logging(level="INFO", log_format="json")
        logging.getLogger("t").info("hello")
        record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert record["message"] == "hello"
        assert "request_id" not in record

    def test_env_vars(self, capsys, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        monkeypatch.setenv("LOG_FORMAT", "json")
        setup_logging()
        logging.getLogger("t").info("quiet")
        logging.getLogger("t").warning("loud")
        err = capsys.readouterr().err
        assert "quiet" not in err
        assert json.loads(err.strip().splitlines()[-1])["message"] == "loud"

    def test_replaces_existing_handlers(self):
        root = logging.getLogger()
        root.addHandler(logging.NullHandler())
        setup_logging(level="INFO", log_format="text")
        assert len(root.handlers) == 1


class TestRequestId:
    def test_id_set_during_call_and_cleared_after(self):
        seen = []

        @with_request_id
        def tool() -> str:
            seen.append(request_id_var.get())
            return "ok"

        assert tool() == "ok"
        assert seen[0] is not None and len(seen[0]) == 12
        assert request_id_var.get() is None

    def test_cleared_after_exception(self):
        @with_request_id
        def tool() -> None:
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            tool()
        assert request_id_var.get() is None

    def test_each_call_gets_new_id(self):
        @with_request_id
        def tool() -> str | None:
            return request_id_var.get()

        assert tool() != tool()

    def test_signature_preserved(self):
        def original(sql: str, limit: int = 10, database: str = "default") -> dict[str, int]:
            """Doc."""
            return {}

        wrapped = with_request_id(original)
        assert inspect.signature(wrapped) == inspect.signature(original)
        assert wrapped.__doc__ == "Doc."
        assert wrapped.__name__ == "original"

    def test_text_log_includes_id(self, capsys):
        setup_logging(level="INFO", log_format="text")

        @with_request_id
        def tool() -> str | None:
            logging.getLogger("t").info("inside")
            return request_id_var.get()

        rid = tool()
        assert f"[{rid}] inside" in capsys.readouterr().err

    def test_json_log_includes_id(self, capsys):
        setup_logging(level="INFO", log_format="json")

        @with_request_id
        def tool() -> str | None:
            logging.getLogger("t").info("inside")
            return request_id_var.get()

        rid = tool()
        record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert record["request_id"] == rid

    def test_filter_default(self):
        record = logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None)
        RequestIdFilter().filter(record)
        assert record.request_id == "-"


@pytest.mark.parametrize(
    "name",
    ["get_logger", "LoggerAdapter", "get_logger_with_context", "set_request_id", "clear_request_id"],
)
def test_unused_helpers_removed(name):
    assert not hasattr(logging_config, name)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_logging_config.py -q`
Expected: `ImportError: cannot import name 'RequestIdFilter'`.

- [ ] **Step 4: Implement**

In `src/mcp_sql_server/logging_config.py`:

Replace the imports with:

```python
import functools
import json
import logging
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")
```

Replace `StandardFormatter.__init__`'s format string with:

```python
            fmt="%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s",
```

Add after `StandardFormatter`:

```python
class RequestIdFilter(logging.Filter):
    """Attach the current request ID (or "-") to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def with_request_id(func: Callable[P, R]) -> Callable[P, R]:
    """Run func with a fresh request ID so its log lines can be correlated.

    Apply directly beneath @mcp.tool(). functools.wraps keeps the signature
    the MCP SDK reads to build the tool schema.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        token = request_id_var.set(uuid.uuid4().hex[:12])
        try:
            return func(*args, **kwargs)
        finally:
            request_id_var.reset(token)

    return wrapper
```

In `setup_logging`, after `console_handler.setLevel(numeric_level)` add:

```python
    console_handler.addFilter(RequestIdFilter())
```

Delete `get_logger`, `LoggerAdapter`, `get_logger_with_context`, `set_request_id`, and `clear_request_id` (everything after `setup_logging` in the file). If `MutableMapping` is no longer used, it is already gone with the new import line.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_logging_config.py -q`
Expected: all pass.

- [ ] **Step 6: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 7: Commit**

```bash
git add src/mcp_sql_server/logging_config.py tests/test_logging_config.py
git commit -m "feat(logging): per-call request IDs in text and JSON logs

Adds RequestIdFilter and with_request_id; removes unused logger helpers.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Server startup order and tool decoration

**Files:**
- Modify: `src/mcp_sql_server/server.py`
- Test: `tests/test_server.py` (new class), `tests/test_concurrency.py` (new class)
- Docs: `README.md` Logging and Structured Logging subsections; `CLAUDE.md` Configuration section

**Interfaces:**
- Consumes: `setup_logging`, `with_request_id` (Task 10); `DEFAULT_ENV_PATH`, `describe_config_error` (Task 8); `DatabaseRegistry.config_errors` (Task 9).
- Produces: `main()` order — read `LOG_*` from process env or `.env` (without touching `os.environ`), `setup_logging`, report config errors, `mcp.run(transport="stdio")`; helper `_report_config_errors() -> None`.

Note on `.env`: `main()` must **not** call `load_dotenv`. `DatabaseConfig.from_env` snapshots process `DB_*` before loading `.env` to rank explicit settings above `SQL_SERVER_*`; loading `.env` earlier would make file values look explicit. Use `dotenv_values` (read-only) for the two logging variables.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
class TestMainStartup:
    @pytest.fixture(autouse=True)
    def _reset_registry(self):
        server._registry = None
        yield
        if server._registry is not None:
            server._registry.close()
        server._registry = None

    def _run_main(self, registry=None, get_registry_error=None, dotenv=None):
        calls = []
        get_registry = MagicMock(return_value=registry, side_effect=get_registry_error)
        with patch.object(server, "dotenv_values", return_value=dotenv or {}), \
             patch.object(server, "setup_logging", side_effect=lambda *a: calls.append(("setup_logging", a))), \
             patch.object(server, "get_registry", get_registry), \
             patch.object(server.mcp, "run", side_effect=lambda **kw: calls.append(("run", kw))):
            server.main()
        return calls

    def test_order_and_transport(self, default_registry):
        calls = self._run_main(registry=default_registry)
        assert [c[0] for c in calls] == ["setup_logging", "run"]
        assert calls[1][1] == {"transport": "stdio"}

    def test_logging_vars_from_dotenv_when_not_in_env(self, default_registry, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        calls = self._run_main(registry=default_registry, dotenv={"LOG_LEVEL": "DEBUG", "LOG_FORMAT": "json"})
        assert calls[0][1] == ("DEBUG", "json")

    def test_process_env_beats_dotenv(self, default_registry, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        calls = self._run_main(registry=default_registry, dotenv={"LOG_LEVEL": "DEBUG"})
        assert calls[0][1] == ("ERROR", None)

    def test_main_does_not_mutate_environ(self, default_registry, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        self._run_main(registry=default_registry, dotenv={"LOG_LEVEL": "DEBUG"})
        assert "LOG_LEVEL" not in os.environ

    def test_config_errors_logged_without_values_and_server_starts(self, caplog):
        from mcp_sql_server.config import DatabaseConfig
        from mcp_sql_server.registry import DatabaseRegistry

        registry = DatabaseRegistry(
            configs={"default": DatabaseConfig(host="h", user="u", password="p", database="d")},
            config_errors={"archive": "password: string_too_short"},
        )
        with caplog.at_level(logging.ERROR):
            calls = self._run_main(registry=registry)
        assert "Database 'archive' configuration invalid: password: string_too_short" in caplog.text
        assert calls[-1][0] == "run"

    def test_invalid_db_databases_logged_and_server_starts(self, caplog):
        with caplog.at_level(logging.ERROR):
            calls = self._run_main(get_registry_error=ValueError("Invalid database alias '1x'"))
        assert "Invalid database alias '1x'" in caplog.text
        assert calls[-1][0] == "run"

    def test_no_import_time_logging_config(self):
        import inspect as _inspect

        assert "basicConfig" not in _inspect.getsource(server)

    def test_every_tool_is_wrapped(self):
        import inspect as _inspect

        source = _inspect.getsource(server)
        assert source.count("@mcp.tool()\n@with_request_id") == 10
```

Add this fixture near the top of `tests/test_server.py` (after the imports), and add `import logging` and `import os` to the imports if they are not already there:

```python
@pytest.fixture
def default_registry():
    from mcp_sql_server.config import DatabaseConfig
    from mcp_sql_server.registry import DatabaseRegistry

    return DatabaseRegistry(
        configs={"default": DatabaseConfig(host="h", user="u", password="p", database="d")}
    )
```

Append to `tests/test_concurrency.py`:

```python
class TestRequestIdIsolation:
    def test_concurrent_calls_get_distinct_ids(self):
        from mcp_sql_server.logging_config import request_id_var, with_request_id

        seen: list[str | None] = []
        seen_lock = threading.Lock()
        barrier = threading.Barrier(WORKERS)

        @with_request_id
        def tool() -> None:
            barrier.wait(timeout=10)  # all calls are in flight at once
            with seen_lock:
                seen.append(request_id_var.get())

        errors = _run_concurrently(tool)
        assert errors == []
        assert len(seen) == WORKERS
        assert None not in seen
        assert len(set(seen)) == WORKERS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -q -k TestMainStartup`
Expected: FAIL — `server` has no attribute `dotenv_values`; source contains `basicConfig`.

- [ ] **Step 3: Implement**

In `src/mcp_sql_server/server.py`:

Add imports:

```python
import os

from dotenv import dotenv_values

from .config import DEFAULT_ENV_PATH, describe_config_error
from .logging_config import setup_logging, with_request_id
```

Replace:

```python
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
```

with:

```python
# Logging is configured in main(). MCPServer's constructor installs the
# SDK's default stderr handler at import; setup_logging() replaces it.
logger = logging.getLogger(__name__)
```

Add `@with_request_id` on its own line directly beneath each of the 10 `@mcp.tool()` decorators, for example:

```python
@mcp.tool()
@with_request_id
def _execute_query(
```

Do not decorate the `@mcp.resource(...)` functions.

Replace `main` with:

```python
def _logging_settings() -> tuple[str | None, str | None]:
    """LOG_LEVEL/LOG_FORMAT from the process env, else from .env.

    Reads .env without modifying os.environ: DatabaseConfig.from_env relies
    on os.environ holding only explicitly set DB_* values until it loads
    .env itself.
    """
    file_values = dotenv_values(DEFAULT_ENV_PATH)  # {} when the file is missing
    level = os.environ.get("LOG_LEVEL") or file_values.get("LOG_LEVEL")
    log_format = os.environ.get("LOG_FORMAT") or file_values.get("LOG_FORMAT")
    return level, log_format


def _report_config_errors() -> None:
    """Log invalid database configs at startup without stopping the server."""
    try:
        registry = get_registry()
    except ValueError as e:
        logger.error("Database configuration invalid: %s", describe_config_error(e))
        return
    for name, message in registry.config_errors.items():
        logger.error("Database '%s' configuration invalid: %s", name, message)


def main() -> None:
    """Entry point for the MCP server."""
    setup_logging(*_logging_settings())
    _report_config_errors()
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_server.py tests/test_server_registration.py tests/test_concurrency.py -q`
Expected: all pass; `test_server_registration.py` unchanged and green.

- [ ] **Step 5: Update docs**

`README.md` `### Logging` (Configuration section): add below the table:

```markdown
`LOG_LEVEL` and `LOG_FORMAT` are read from the process environment first, then from `.env`.
```

`README.md` `### Structured Logging`: replace the two format bullets and the sentence after them with:

```markdown
- **`text`** (default): `2024-01-15 10:30:00 - module.name - INFO - [3f2a9c1b7d4e] message`
- **`json`**: `{"timestamp": "...", "level": "INFO", "logger": "...", "message": "...", "request_id": "3f2a9c1b7d4e"}`

Every tool call gets a 12-character `request_id`, so all log lines from one call (including audit events) share it. Lines outside a tool call show `-` (text) or omit the field (JSON).
```

`README.md`: add a new subsection at the end of `## Configuration` (before `## Multi-Database Support`):

```markdown
### Startup Validation

On startup the server checks every database's configuration and logs problems at `ERROR` (alias, field, and error type only; never values). The server still starts: a misconfigured database returns its configuration error when a tool targets it, and the other databases keep working.
```

`CLAUDE.md` `## Configuration`: add after the `**Warning:**` line:

```markdown
At startup each database config is validated and errors are logged (no values); the server still starts, and only calls to a misconfigured database fail.
```

- [ ] **Step 6: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 7: Stdio smoke check**

Run:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  | timeout 10 .venv/bin/python -m mcp_sql_server.server 2>/tmp/claude-1000/-home-odecio-projects-mcp-sql-server/1f522304-17f9-42cb-b4a6-049de0ee73dc/scratchpad/stderr.log | head -c 400; echo
```

Expected: stdout begins with `{"jsonrpc":"2.0","id":1,"result":` and contains `"MCP SQL Server"`; nothing but JSON-RPC on stdout.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_sql_server/server.py tests/test_server.py tests/test_concurrency.py README.md CLAUDE.md
git commit -m "feat(server): configure logging in main, report config errors, tag tool calls

LOG_* from .env now take effect (read without touching os.environ, which
keeps DB_* precedence intact). Each tool call gets a request_id.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Remove unused exports; signature-bound cache keys; utils tests

**Files:**
- Modify: `src/mcp_sql_server/tools/__init__.py`, `src/mcp_sql_server/resources/__init__.py`
- Modify: `src/mcp_sql_server/cache.py` (`cached`)
- Test: `tests/test_cache.py`, `tests/test_utils.py` (new)
- Docs: `README.md` Project Structure tree comments, Caching features bullet

**Interfaces:**
- Consumes: nothing.
- Produces: `cached(...)` keys = `f"{key_prefix or func.__name__}|{sorted(bound.arguments.items())!r}"`; `ALL_TOOLS`/`ALL_RESOURCES` removed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cache.py`:

```python
class TestCacheKeys:
    def test_colon_in_args_does_not_collide(self):
        invalidate_metadata_cache()
        calls = []

        @cached(ttl=60, key_prefix="collide")
        def f(*args):
            calls.append(args)
            return args

        assert f("a", "b") == ("a", "b")
        assert f("a:b") == ("a:b",)
        assert len(calls) == 2

    def test_positional_and_keyword_share_entry(self):
        invalidate_metadata_cache()
        calls = []

        @cached(ttl=60, key_prefix="bind")
        def f(table, schema="dbo"):
            calls.append((table, schema))
            return table

        f("x", "dbo")
        f("x", schema="dbo")
        f("x")
        assert len(calls) == 1


def test_package_exports_removed():
    import mcp_sql_server.resources as resources
    import mcp_sql_server.tools as tools

    assert not hasattr(tools, "ALL_TOOLS")
    assert not hasattr(resources, "ALL_RESOURCES")
```

Create `tests/test_utils.py`:

```python
"""Tests for lazy server accessors in utils."""

from unittest.mock import MagicMock, patch

import pytest

from mcp_sql_server import utils


@pytest.fixture(autouse=True)
def reset_getters():
    utils._db_getter = None
    utils._registry_getter = None
    yield
    utils._db_getter = None
    utils._registry_getter = None


def test_get_db_delegates_and_caches():
    fake = MagicMock(return_value="manager")
    with patch("mcp_sql_server.server.get_db", fake):
        assert utils.get_db("analytics") == "manager"
        assert utils.get_db() == "manager"
    fake.assert_any_call("analytics")
    fake.assert_any_call("default")
    assert utils._db_getter is fake


def test_get_registry_delegates_and_caches():
    fake = MagicMock(return_value="registry")
    with patch("mcp_sql_server.server.get_registry", fake):
        assert utils.get_registry() == "registry"
        assert utils.get_registry() == "registry"
    assert fake.call_count == 2
    assert utils._registry_getter is fake
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cache.py tests/test_utils.py -q`
Expected: `test_colon_in_args_does_not_collide`, `test_positional_and_keyword_share_entry`, `test_package_exports_removed` FAIL; utils tests pass (they cover existing behavior and raise coverage).

- [ ] **Step 3: Implement cache keys**

In `src/mcp_sql_server/cache.py`, add `import inspect` to the imports. In `cached`, replace the `decorator` body so it reads:

```python
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        signature = inspect.signature(func)
        prefix = key_prefix or func.__name__

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            cache = get_metadata_cache()

            # Bind to the signature so f("x", "dbo") and f("x", schema="dbo")
            # share a key; repr() keeps argument boundaries unambiguous.
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            cache_key = f"{prefix}|{sorted(bound.arguments.items())!r}"

            # Try to get from cache
            value, found = cache.get(cache_key)
            if found:
                return value  # type: ignore[no-any-return]

            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            return result

        return wrapper
```

- [ ] **Step 4: Remove unused exports**

In `src/mcp_sql_server/tools/__init__.py`, delete the `ALL_TOOLS = [...]` block and the `"ALL_TOOLS",` entry in `__all__`.
In `src/mcp_sql_server/resources/__init__.py`, delete the `ALL_RESOURCES = [...]` block and the `"ALL_RESOURCES",` entry in `__all__`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_cache.py tests/test_utils.py tests/test_server.py -q`
Expected: all pass.

- [ ] **Step 6: Update docs**

`README.md` Project Structure tree: change `# Tool exports (ALL_TOOLS)` to `# Tool exports` and `# Resource exports (ALL_RESOURCES)` to `# Resource exports`.

`README.md` `### Features` under Caching: replace the `@cached` bullet with:

```markdown
- `@cached` decorator for transparent function-level caching; keys bind arguments to the function signature, so positional and keyword calls share an entry
```

- [ ] **Step 7: Full suite and types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all pass; no mypy issues.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_sql_server/cache.py src/mcp_sql_server/tools/__init__.py src/mcp_sql_server/resources/__init__.py tests/test_cache.py tests/test_utils.py README.md
git commit -m "refactor: signature-bound cache keys; drop unused ALL_TOOLS/ALL_RESOURCES

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Remaining documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `.env.example`, `.claude/agents/README.md`, `.claude/agents/lesson-retriever.md`, `.claude/agents/session-lessons-documenter.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Test counts**

`README.md` Features list: replace `- **337 Tests** at 85%+ code coverage` with `- **Comprehensive test suite** at 85%+ code coverage (no live database required)`.
`README.md` `## Testing`: replace `The test suite contains 369 tests covering all modules.` with `The test suite covers all modules with mocked database connections; run it to see the current count.`
`CLAUDE.md` `## Testing`: replace `369 tests with 85%+ coverage.` with `Comprehensive suite with 85%+ coverage.`

- [ ] **Step 2: Test file lists**

`CLAUDE.md` "Key test files": keep `test_audit.py` and `test_errors.py` (they now exist) and add:

```markdown
- `test_config_multi.py` - Multi-database config and alias parsing
- `test_query_dir.py` - Query directory resolution
- `test_sql_lexer.py` - T-SQL tokenizer
- `test_logging_config.py` - Log formats and request IDs
- `test_utils.py` - Lazy server accessors
```

`README.md` `### Test Files` table: delete the `test_inject_top_clause.py` row and add, before `conftest.py`:

```markdown
| `test_server_registration.py` | MCP SDK registration surface (tool/resource names, schemas) |
| `test_concurrency.py` | Parallel pool/registry access and per-call request IDs |
| `test_sql_lexer.py` | T-SQL tokenizer: strings, identifiers, comments, depth |
| `test_audit.py` | Masked SQL previews and query-shape hashes |
| `test_errors.py` | Error sanitization and value redaction |
| `test_logging_config.py` | Text/JSON formats, request ID propagation |
| `test_utils.py` | Lazy server accessors |
```

`README.md` Project Structure tree: delete the `test_inject_top_clause.py` line and add before `test_query_dir.py`:

```
    +-- test_server_registration.py        # MCP registration surface tests
    +-- test_concurrency.py                # Thread-safety tests
    +-- test_sql_lexer.py                  # Tokenizer tests
    +-- test_audit.py                      # Audit masking tests
    +-- test_errors.py                     # Error sanitization tests
    +-- test_logging_config.py             # Logging tests
    +-- test_utils.py                      # Lazy accessor tests
```

- [ ] **Step 3: Document `SQL_SERVER_*`**

In `README.md`, add a subsection at the start of `## Configuration`, right after the `Copy \`.env.example\`` sentence:

```markdown
### Configuration Sources and Precedence

The default database can be configured two ways:

- `DB_*` variables, from the MCP client's `env` block or the `.env` file
- `SQL_SERVER_*` variables (`SQL_SERVER_HOST`, `SQL_SERVER_PORT`, `SQL_SERVER_USER`, `SQL_SERVER_PASSWORD`, `SQL_SERVER_DATABASE`, `SQL_SERVER_DRIVER`, `SQL_SERVER_ENCRYPT`, `SQL_SERVER_TRUST_CERT`), typically set in `.claude/settings.local.json` for Claude Code

Precedence, highest first:

1. `DB_*` set in the process environment (e.g. by the MCP client)
2. `SQL_SERVER_*`
3. `DB_*` from `.env`

Named databases (`DB_{ALIAS}_*`) do not read `SQL_SERVER_*`.
```

In `.env.example`, after the `# DB_QUERY_TIMEOUT=120` line add:

```env

# Alternative for Claude Code: set these in .claude/settings.local.json "env"
# instead of DB_* (a DB_* set by the MCP client still wins; see README).
# SQL_SERVER_HOST=localhost
# SQL_SERVER_PORT=1433
# SQL_SERVER_USER=sa
# SQL_SERVER_PASSWORD=YourPassword123
# SQL_SERVER_DATABASE=YourDatabase
# SQL_SERVER_DRIVER=ODBC Driver 18 for SQL Server
# SQL_SERVER_ENCRYPT=false
# SQL_SERVER_TRUST_CERT=true
```

- [ ] **Step 4: Lessons knowledge base path**

The lessons live in the sibling `sql-playground` repository. Replace in:

- `.claude/agents/README.md`: `**Location:** \`docs/lessons/\`` → `**Location:** \`/home/odecio/projects/sql-playground/docs/lessons/\` (in the sql-playground repository)`; `**Index:** \`docs/lessons/INDEX.md\`` → `**Index:** \`/home/odecio/projects/sql-playground/docs/lessons/INDEX.md\``
- `.claude/agents/lesson-retriever.md`: every `/home/odecio/projects/mcp-sql-server/docs/lessons/` → `/home/odecio/projects/sql-playground/docs/lessons/`
- `.claude/agents/session-lessons-documenter.md`: `docs/lessons/INDEX.md` → `/home/odecio/projects/sql-playground/docs/lessons/INDEX.md`; `**Base Path:** \`docs/lessons/\` (relative to project root)` → `**Base Path:** \`/home/odecio/projects/sql-playground/docs/lessons/\` (sql-playground repository)`; `/home/odecio/projects/mcp-sql-server/docs/lessons/` → `/home/odecio/projects/sql-playground/docs/lessons/`

Verify: `grep -rn "docs/lessons" .claude/agents/` shows only `sql-playground` paths, and `ls /home/odecio/projects/sql-playground/docs/lessons/INDEX.md` succeeds.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md .env.example .claude/agents
git commit -m "docs: document SQL_SERVER_* precedence, test files, lessons location

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Final verification

**Files:** none modified unless a check fails.

- [ ] **Step 1: Full suite with coverage**

Run: `.venv/bin/pytest tests/ -q --cov=mcp_sql_server --cov-report=term 2>&1 | tail -30`
Expected: all pass; total ≥ 85%; `logging_config.py` and `utils.py` well above their previous 0% / 38%.

- [ ] **Step 2: Types**

Run: `.venv/bin/python -m mypy src/mcp_sql_server/`
Expected: `Success: no issues found in 21 source files`

- [ ] **Step 3: Doc reference check**

Run:

```bash
for f in $(grep -ohE "test_[a-z_]+\.py" README.md CLAUDE.md | sort -u); do [ -f "tests/$f" ] || echo "MISSING test file: $f"; done
for m in $(grep -ohE "\`[a-z_]+\.py\`" README.md CLAUDE.md | tr -d '`' | grep -v '^test_' | sort -u); do [ -n "$(find src -name "$m")" ] || echo "MISSING module: $m"; done
grep -nE "_limited_query|SELECT TOP \(limit|MCPError|ALL_TOOLS|ALL_RESOURCES|337|369 tests|DeprecationWarning" README.md CLAUDE.md .claude/rules/*.md || echo "no stale references"
```

Expected: no `MISSING` lines; final line `no stale references`. Fix any hit in the doc it names and commit with `docs: fix stale references`.

- [ ] **Step 4: Live smoke check (only if a database is configured)**

Skip this step if neither `SQL_SERVER_HOST` nor `DB_HOST` is set in the environment or `.claude/settings.local.json`. Otherwise run (it reads the same config as the server):

```bash
.venv/bin/python - <<'EOF'
from mcp_sql_server.tools.query_execution import execute_query
from mcp_sql_server import server

r = execute_query("WITH c AS (SELECT TOP 5 name FROM sys.objects ORDER BY name) SELECT * FROM c ORDER BY name", limit=3)
print("cte+order", r["success"], r.get("row_count"), r.get("truncated"), r.get("error"))
r = execute_query("SELECT name FROM sys.objects WHERE name LIKE ?", params=["sys%"], limit=2)
print("param+limit", r["success"], r.get("row_count"), r.get("truncated"), r.get("error"))
r = execute_query("SELECT @@ROWCOUNT AS rc, DB_NAME() AS db", limit=10)
print("follow-up", r["success"], r.get("rows"), r.get("error"))
server.get_registry().close()
EOF
```

Expected: `cte+order True 3 True None`; `param+limit True 2 True None`; `follow-up True [...] None` with `db` equal to the configured database. If `follow-up` fails with "Connection is busy" or shows a different database, stop and report — the session reset is not working against a real server.

- [ ] **Step 5: Ask before deleting the merged branch**

Ask the user: "All tasks are done and verified. May I delete the merged local branch `feat/mcp-2x-migration` (`git branch -d`, safe because it is fully merged into `main`)?" Only on a yes, run:

```bash
git branch -d feat/mcp-2x-migration
```

---

## Self-Review Notes

- **Spec coverage:** §1.1 → Task 1; §1.2 → Task 2; §1.3 → Tasks 2, 4; §1.4 → Task 3; §1.5 → Task 4; §1.6 → Task 5; §1.6a → Task 6; §1.7 → Tasks 1–6; §2.1–2.4 → Task 7; §2.5 → Tasks 8, 9, 11; §2.6 → Tasks 7–9, 11; §3.1–3.2 → Tasks 10, 11; §3.3 → Tasks 6, 10, 12; §3.4 → Task 12; §3.5 → no change; §3.6 → Tasks 10–12; §4 → Tasks 2, 4, 5, 6, 9, 11, 12, 13; §5 → Tasks 4, 7, 9; §6 order preserved.
- **Plan-time corrections recorded in the spec (revision 4):** `main()` reads `LOG_*` with `dotenv_values` instead of `load_dotenv` (preserves `DB_*` precedence); `execute_query` gains `server_limit` so procedures use `fetchmany` without `SET ROWCOUNT`; the reset uses two statements (`SET ROWCOUNT 0`, then `SELECT DB_NAME()`) to avoid depending on multi-statement result positioning; `ADD` and the `CONVERSATION` pairs added to the blocklists; `SELECT` after `VALUES` rejected in `execute_statement`; `list_databases` includes misconfigured aliases; `test_registry.py::test_from_env` rewritten.
