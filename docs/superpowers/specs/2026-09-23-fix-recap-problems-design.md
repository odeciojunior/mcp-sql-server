# Fix Recap Problems — Design

**Date:** 2026-09-23
**Status:** Approved in conversation (sections 1–4); awaiting written-spec review
**Scope:** Resolve every open problem found in the 2026-09-23 codebase recap of `mcp-sql-server` (HEAD `5434c81`).

## Goal and success criteria

Fix the confirmed bugs, security gaps, dead/inconsistent code, and stale docs from the recap, without changing any MCP tool name, parameter, or response shape.

Done means:

- All tests pass (the existing suite plus the new tests below). `.venv/bin/pytest tests/ -q`
- mypy strict stays clean. `.venv/bin/python -m mypy src/mcp_sql_server/`
- Every fix has a test that fails before the change and passes after it (docs excepted).
- `test_server_registration.py` passes unchanged — tool/resource names and schemas are identical.

## Decisions taken

| Question | Decision |
|---|---|
| Row limiting for `execute_query` | Drop SQL rewriting; `fetchmany(limit + 1)`; guard stacking with a tokenizer |
| Dead observability code | Delete unused exception classes, `ALL_TOOLS`/`ALL_RESOURCES`, unused logger helpers; wire `request_id` per tool call |
| Audit SQL preview | Mask string and numeric literals as `?` |
| Startup behavior | Validate all configs in `lifespan` (fail fast); do not connect |

## Non-goals

- A full SQL parser. The tokenizer only classifies tokens; it does not build a syntax tree.
- `SQL_SERVER_*` fallback for named databases. Named aliases read only `DB_{ALIAS}_*`, as documented in `.claude/rules/sql-server-connection.md`.
- Changing `ConnectionPool.acquire()` polling or `DatabaseRegistry.close()` error handling beyond comments/docstrings (see §2.3, §2.4 for why they are not defects).

---

## 1. Query execution and SQL validation

### 1.1 New module: `src/mcp_sql_server/sql_lexer.py`

Dependency-free, single purpose.

```python
class TokenKind(Enum): WORD, STRING, QUOTED_IDENT, NUMBER, SEMICOLON, OTHER

@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str          # original text of the token

class LexError(ValueError): ...

def tokenize(sql: str) -> list[Token]: ...
```

Rules:

- `WORD` — `[A-Za-z_@#][A-Za-z0-9_@#$]*`. Keyword comparisons use `text.upper()`.
- `STRING` — `'...'` with `''` as an escaped quote; an `N` or `n` immediately before `'` is part of the string token (`N'abc'` is one `STRING`).
- `QUOTED_IDENT` — `[...]` (with `]]` escape) and `"..."` (with `""` escape).
- `NUMBER` — `\d+(\.\d+)?([eE][+-]?\d+)?` and `0x[0-9A-Fa-f]*`.
- `SEMICOLON` — `;`.
- `OTHER` — any other single non-whitespace character (operators, parentheses, commas, dots).
- Whitespace is skipped. `-- ...` to end of line and `/* ... */` comments are skipped and produce no token. Block comments nest, as in T-SQL (`/* a /* b */ c */` is one comment).
- An unterminated string, quoted identifier, or block comment raises `LexError` with a message naming which construct is unterminated.

### 1.2 `security.validate_query` rewrite

Signature and return contract unchanged: `validate_query(sql: str, allow_modifications: bool = False) -> tuple[bool, str]`.

Changes to constants:

- `BLOCKED_KEYWORDS` gains `EXEC`, `EXECUTE`.
- New `READ_ONLY_FORBIDDEN: set[str] = {"INSERT", "UPDATE", "DELETE", "MERGE", "INTO"}`.

Algorithm (checks run in this order; first failure wins):

1. Empty or whitespace-only input → `"Query cannot be empty"`.
2. `tokenize(sql)`; on `LexError` → `"Invalid SQL: <message>"`.
3. No tokens (e.g. comment-only) → `"Query cannot be empty"`.
4. Any `SEMICOLON` token that is not the last token → `"Multiple statements are not allowed"`. A single trailing `;` is accepted.
5. For each `WORD` token: upper-cased text in `BLOCKED_KEYWORDS` → `"Blocked keyword detected: <KW>"`.
6. For each `WORD` token: upper-cased text starts with `XP_` or `SP_` → `"System procedure calls not allowed: <prefix>*"` (same message format as today).
7. First token must be a `WORD` whose upper-cased text is in the allowed set (`ALLOWED_QUERY_KEYWORDS`, plus `ALLOWED_STATEMENT_KEYWORDS` when `allow_modifications`) → otherwise `"Statement type '<X>' not allowed"`.
8. When `allow_modifications` is false: any `WORD` token in `READ_ONLY_FORBIDDEN` → `"Data modification not allowed in read-only query: <KW>"`.

Consequences (each gets a test):

| Input | Before | After |
|---|---|---|
| `WITH c AS (SELECT 1 x) SELECT * FROM c` | fails at execution (wrapper) | accepted and runs |
| `SELECT * FROM t ORDER BY id` | fails at execution (wrapper) | accepted and runs |
| `-- note\nSELECT 1` | rejected (`--`) | accepted |
| `SELECT * FROM t WHERE x = 'DROP'` | rejected | accepted |
| `SELECT [Create] FROM t` | rejected | accepted |
| `SELECT 1 /* DROP */` | rejected | accepted |
| `SELECT 1;` | accepted | accepted |
| `SELECT 1; SELECT 2` | wrapper syntax error | rejected: multiple statements |
| `INSERT INTO t VALUES (1); EXEC('...')` | **accepted** | rejected |
| `WITH c AS (SELECT 1 x) DELETE FROM t` via `execute_query` | wrapper syntax error | rejected: read-only |
| `SELECT * INTO NewTable FROM t` via `execute_query` | wrapper syntax error | rejected: read-only |
| `SELECT 'unterminated` | wrapper syntax error | rejected: invalid SQL |

`validate_identifier`, `validate_procedure_name`, `sanitize_table_name` are unchanged. `execute_procedure` does not call `validate_query` and is unaffected by the `EXEC` block.

### 1.3 `tools/query_execution.py`

- Delete `_inject_top_clause`.
- `execute_query` calls `_get_db(database).execute_query(sql, params_tuple, max_rows=limit + 1)` with the original SQL. `truncated`/slicing logic stays as is.
- `execute_statement` derives `first_word` from the first token (`tokenize(sql)[0].text.upper()`) instead of `sql.split()[0]`. Validation has already guaranteed tokenization succeeds and the first token is a `WORD`.
- `execute_query_file` needs no change; it delegates to `execute_query`.

### 1.4 `database.py` — `DatabaseManager.execute_query`

New signature: `execute_query(sql, params=None, max_rows: int | None = None) -> list[dict[str, Any]]`.

- `max_rows is None` → `fetchall()` (existing behavior for any other callers).
- Otherwise → `fetchmany(max_rows)`.
- The cursor is closed by the existing `get_cursor()` context manager, which discards remaining rows; the pooled connection is released and rolled back as today.

### 1.5 `audit.py` — masked SQL preview

`_get_sql_preview(sql, max_length=100)`:

- Tokenize; rebuild the preview by joining token texts with single spaces, replacing every `STRING` and `NUMBER` token with `?`.
- Truncate to `max_length`, appending `...` when truncated (as today).
- On `LexError` → return `"<unparseable>"`.

Example: `SELECT * FROM Cliente WHERE cpf = '123.456.789-00' AND id = 42` → `SELECT * FROM Cliente WHERE cpf = ? AND id = ?`.

`_hash_sql` is unchanged (hashes the raw SQL).

### 1.6 Tests

- New `tests/test_sql_lexer.py`: each token kind; `''`, `]]`, `""` escapes; `N'...'`; line and nested block comments; unterminated string / identifier / comment raise `LexError`.
- `tests/test_security.py`: every row of the table in §1.2, for both `allow_modifications` values where relevant; `EXEC`/`EXECUTE` blocked; `xp_`/`sp_` inside a string not blocked.
- `tests/test_server.py`: the `execute_query` tool passes SQL unmodified and `max_rows=limit+1`; `fetchmany` used; `truncated` true when `limit+1` rows returned.
- `tests/test_database.py`: `execute_query` with and without `max_rows`.
- New `tests/test_audit.py`: literal masking, truncation, unparseable input.
- Delete `tests/test_inject_top_clause.py`.

---

## 2. Connections and pool

### 2.1 `DatabaseManager.connect()` with pooling → error

When `use_pool=True`, `connect()` raises `RuntimeError("connect() is not supported when pooling is enabled; use get_cursor()")`. Remove the pooled branch, the `DeprecationWarning`, and `_current_pooled_conn`. Update the docstring. Non-pooled behavior is unchanged (`get_cursor()` and `execute_statement()` still call it internally in non-pooled mode).

Update `tests/test_database.py` (the test asserting `DeprecationWarning`) and the comment in `tests/test_concurrency.py` that describes `connect()` as deprecated.

### 2.2 Non-pooled health check rollback

`DatabaseManager._is_connected()` calls `self._connection.rollback()` after `execute("SELECT 1")`, matching `ConnectionPool._is_connection_healthy`. Test: rollback is called.

### 2.3 `ConnectionPool.acquire()` — comment only

No behavior change. Add a comment at the `queue.get(timeout=min(remaining, 0.1))` call explaining that the short timeout is required: retiring a connection on release frees a creation slot without putting anything on the queue, so a waiter blocked for the full timeout would miss the slot.

### 2.4 `DatabaseRegistry.close()` — docstring only

No behavior change. Docstring states it is a shutdown path that closes every manager, logging per-manager failures rather than raising, so one failure cannot leave other pools open; `close_database()` raises.

### 2.5 Fail-fast config validation in `lifespan`

Before `yield`, `lifespan` calls `get_registry()` (which runs `DatabaseRegistry.from_env()` and loads every database and pool config; no connection is opened).

On `pydantic.ValidationError` or `ValueError`: log at ERROR a message naming the failing database alias(es)/field(s) — never values — then re-raise so the server exits. Shutdown cleanup after `yield` is unchanged.

Tests: `lifespan` with invalid env raises and logs without leaking the password; with valid env builds the registry and `pyodbc.connect` is not called.

---

## 3. Observability and cleanup

### 3.1 Logging setup

- Remove `logging.basicConfig(level=logging.INFO)` from `server.py` module import.
- `main()` calls `setup_logging()` before `mcp.run(...)`. Output stays on stderr (stdout is the MCP stdio channel).

### 3.2 Per-call `request_id`

In `logging_config.py`:

```python
def with_request_id(func: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        token = request_id_var.set(uuid.uuid4().hex[:12])
        try:
            return func(*args, **kwargs)
        finally:
            request_id_var.reset(token)
    return wrapper
```

- Applied beneath each `@mcp.tool()` in `server.py` (10 tools). Not applied to resources.
- `functools.wraps` keeps the signature visible to the SDK; `test_server_registration.py` guards schemas.
- SDK 2.x runs sync handlers via `anyio.to_thread.run_sync`, which runs each call in a copied context, so concurrent calls keep distinct IDs.
- `request_id` appears only in JSON log output (existing `StructuredFormatter` behavior).

### 3.3 Dead code removal

- `errors.py`: delete `MCPError`, `ValidationError`, `ConnectionError`, `QueryError`, `TimeoutError`. Keep `sanitize_error`, `simplify_error`, `create_error_response`.
- `tools/__init__.py`: delete `ALL_TOOLS` and its `__all__` entry. `resources/__init__.py`: delete `ALL_RESOURCES` and its `__all__` entry.
- `logging_config.py`: delete `get_logger`, `LoggerAdapter`, `get_logger_with_context`, `set_request_id`, `clear_request_id` (no callers in `src/` or `tests/`; `with_request_id` uses `request_id_var` directly).

### 3.4 Cache key construction

In `cache.cached`:

- Prefix: `key_prefix or f"{func.__module__}.{func.__qualname__}"`.
- Key: `f"{prefix}|{args!r}|{sorted(kwargs.items())!r}"`.

Key remains `str`; `TTLCache` is unchanged. Test: `f("a", "b")` and `f("a:b")` yield different keys; two same-named functions in different modules do not collide.

### 3.5 Kept as-is

The comment at `server.py` above `MCPServer(...)` (positional-arg trap) stays; it explains why only `name` is positional.

### 3.6 Tests

- New `tests/test_logging_config.py`: JSON and text formatters; `setup_logging` honors `LOG_LEVEL`/`LOG_FORMAT` and writes to stderr; `with_request_id` sets an ID during the call and restores it after (including on exception); wrapped function keeps its signature.
- `tests/test_concurrency.py`: concurrent wrapped calls observe distinct `request_id` values.
- `tests/test_cache.py`: key collision cases above.

---

## 4. Docs and hygiene

Each doc change lands in the same commit as the code change it describes.

- **Test counts:** replace hardcoded counts in `README.md` (lines ~17 and ~732) and `CLAUDE.md` with count-free wording; keep the "85%+ coverage" claim.
- **Test file lists:** `CLAUDE.md` — remove `test_errors.py` (does not exist); keep `test_audit.py` (created in §1.6); add `test_config_multi.py`, `test_query_dir.py`, `test_sql_lexer.py`, `test_logging_config.py`. `README.md` table and tree — remove `test_inject_top_clause.py`; add `test_server_registration.py`, `test_concurrency.py`, `test_sql_lexer.py`, `test_logging_config.py`, `test_audit.py`.
- **`SQL_SERVER_*`:** README gains a configuration subsection describing both sources and precedence (process `DB_*` > `SQL_SERVER_*` > `.env` `DB_*`; named aliases only `DB_{ALIAS}_*`). `.env.example` gains a commented `SQL_SERVER_*` block.
- **Changed behavior:** README "Security" and CLAUDE.md "Blocked SQL Keywords"/"Statement Type Enforcement" reflect §1.2; README error-hierarchy section replaced with the error-dict format; README logging section covers `LOG_FORMAT=json`, `request_id`, masked preview; `.claude/rules/mcp-tools.md` describes `fetchmany`-based limiting; README and CLAUDE.md architecture add `sql_lexer.py`.
- **`.claude/agents/README.md`:** the lessons knowledge base section points to the `sql-playground` repository (`docs/lessons/`, where `INDEX.md` lives) instead of a nonexistent local path.
- **Merged branch:** `git branch -d feat/mcp-2x-migration` as the final step, after asking the user.
- **Verification:** grep docs for every referenced module, test file, and keyword to confirm each exists.

Already resolved before this spec: `.coverage` untracked and ignored (`5434c81`).

## Suggested implementation order

1. `sql_lexer.py` + tests.
2. `validate_query` rewrite + security tests.
3. `max_rows` / `fetchmany`, remove TOP wrapper, `execute_statement` first-token (must land together with step 2 so stacking is never unguarded).
4. Audit preview masking.
5. Pool/connection fixes (§2.1–2.4).
6. Fail-fast `lifespan` (§2.5).
7. Logging setup + `with_request_id` (§3.1–3.2).
8. Dead code + cache keys (§3.3–3.4).
9. Docs sweep + verification grep; delete merged branch.
