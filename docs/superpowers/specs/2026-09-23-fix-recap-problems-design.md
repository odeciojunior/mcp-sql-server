# Fix Recap Problems — Design

**Date:** 2026-09-23
**Status:** Revision 4 — revision 3 plus plan-time corrections (Appendix B). Implementation plan: `docs/superpowers/plans/2026-09-23-fix-recap-problems.md`.
**Scope:** Resolve every open problem found in the 2026-09-23 codebase recap of `mcp-sql-server` (HEAD `5434c81`).

## Goal and success criteria

Fix the confirmed bugs, security gaps, dead/inconsistent code, and stale docs from the recap, without renaming or removing any MCP tool, parameter, or response key.

Done means:

- All tests pass: `.venv/bin/pytest tests/ -q`. Existing tests are only changed where §5 lists them.
- mypy strict stays clean: `.venv/bin/python -m mypy src/mcp_sql_server/`.
- Every behavior change has a test that fails before the change and passes after it. Exempt: comment/docstring-only items (§2.3, §2.4, §3.5), pure deletions (§3.3), and docs (§4).
- `test_server_registration.py` passes unchanged. (Verified by review: a `functools.wraps`-wrapped tool yields an identical schema on mcp 2.2.0.)

## Decisions taken

| Question | Decision |
|---|---|
| SQL rewriting for row limits | None. The user's SQL runs unmodified |
| Row limiting | `SET ROWCOUNT {limit+1}` on the same cursor, then `fetchmany(limit+1)` as a second cap, then `SET ROWCOUNT 0` |
| Statement stacking | Tokenizer + single-statement rule at parenthesis depth 0 (no reliance on `;`) |
| Dead observability code | Delete unused exception classes, `ALL_TOOLS`/`ALL_RESOURCES`, unused logger helpers; wire `request_id` per tool call |
| Audit SQL preview | Mask literals; the logged hash is of the masked SQL; redact values echoed in driver errors |
| Startup behavior | Validate each database config in `main()`, log safe errors, keep running; broken aliases fail only their own tool calls |

## Non-goals

- A full SQL parser. The tokenizer classifies tokens and tracks parenthesis depth only.
- The validator as the only line of defense. It is defense in depth; the README will recommend a `db_datareader`-only login for read-only use.
- CTE-prefixed DML (`WITH c AS (...) DELETE ...`). Rejected by both tools, as today; documented.
- `SQL_SERVER_*` fallback for named databases (documented as intentional).
- Changing `ConnectionPool.acquire()` polling or `DatabaseRegistry.close()` error handling beyond comments/docstrings (§2.3, §2.4 explain why they are correct).

---

## 1. Query execution and SQL validation

### 1.1 New module: `src/mcp_sql_server/sql_lexer.py`

Dependency-free; implemented as a character loop (no Unicode-sensitive regex classes).

```python
class TokenKind(Enum):
    WORD, STRING, QUOTED_IDENT, NUMBER, SEMICOLON, OTHER

@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str      # original text
    start: int     # offset in the input
    end: int       # offset one past the last character
    depth: int     # parenthesis depth at this token; "(" has the outer depth, ")" too

class LexError(ValueError): ...

def tokenize(sql: str) -> list[Token]: ...
```

Rules, checked in this order at each position:

1. **Whitespace** — exactly space, `\t`, `\r`, `\n`, `\f`, `\v`; skipped. Any other character (including `\x00`, `\xa0`, `　`) is not whitespace and becomes an `OTHER` token. Mismatches with SQL Server must fail closed: an unrecognized character can only split words, never hide them.
2. **Line comment** — `--` up to (not including) the first `\r` or `\n`, or end of input; skipped.
3. **Block comment** — `/* ... */`, nesting (`/* a /* b */ c */` is one comment); skipped. Unterminated → `LexError("unterminated block comment")`.
4. **String** — `'...'` with `''` as an escaped quote. `N'...'`/`n'...'` is one `STRING` only when the `N` is at the start of a token (i.e. not inside a word: `columnN'x'` is `WORD columnN` + `STRING 'x'`). Unterminated → `LexError("unterminated string")`.
5. **Quoted identifier** — `[...]` with `]]` escape, and `"..."` with `""` escape. Unterminated → `LexError("unterminated quoted identifier")`.
6. **Number** — `0x`/`0X` followed by hex digits first; else ASCII digits with an optional `.digits` fraction and optional `e`/`E` exponent; also `.digits`. ASCII `0-9` only.
7. **Word** — first character is `_`, `@`, `#`, or `str.isalpha()`; following characters are `_`, `@`, `#`, `$`, ASCII digits, or `str.isalpha()`. So `Situação` and `@@VERSION` are single words.
8. **Semicolon** — `;`.
9. **Other** — any other single character. `(` increments depth for following tokens; `)` decrements (never below 0).

Keyword comparisons use `text.upper()`; comparison sets contain ASCII words only, so non-ASCII look-alikes never match an allowed keyword (they can only cause rejection).

### 1.2 `security.py` — validation rules

`validate_query(sql: str, allow_modifications: bool = False) -> tuple[bool, str]` keeps its signature and contract. `allow_modifications=True` is used only by `execute_statement`.

Constants:

- `BLOCKED_KEYWORDS` — unchanged (still used by `validate_identifier`).
- `BLOCKED_PREFIXES` — unchanged (`xp_`, `sp_`).
- New `STATEMENT_WORDS` — words that start a separate statement; rejected anywhere, both modes:
  `EXEC, EXECUTE, DENY, USE, DECLARE, WAITFOR, BEGIN, COMMIT, ROLLBACK, SAVE, PRINT, RAISERROR, THROW, IF, WHILE, GOTO, RETURN, OPEN, CLOSE, DEALLOCATE, CHECKPOINT, RECONFIGURE, SETUSER, REVERT, RECEIVE, SEND, WRITETEXT, UPDATETEXT, READTEXT`.
  Deliberately excluded: `END` (needed by `CASE`), `FETCH` (needed by `OFFSET ... FETCH`), `ENABLE`/`DISABLE` (common column names; handled as pairs below). Kept separate from `BLOCKED_KEYWORDS` so `describe_table` on a table named e.g. `Print` still works.
- New `BLOCKED_PAIRS` — consecutive `WORD` pairs rejected anywhere, both modes: `(ENABLE, TRIGGER)`, `(DISABLE, TRIGGER)`, `(NEXT, VALUE)`.
- New `BLOCKED_FUNCTIONS` — rejected anywhere, both modes (outbound-file/SMB readers): `FN_XE_FILE_TARGET_READ_FILE, FN_TRACE_GETTABLE, FN_GET_AUDIT_FILE`. Matched on the last dotted part, so `sys.fn_trace_gettable` is caught.
- `ALLOWED_QUERY_KEYWORDS = {SELECT, WITH}`; `ALLOWED_STATEMENT_KEYWORDS = {INSERT, UPDATE, DELETE}`. With `allow_modifications=True` the allowed first words are exactly `ALLOWED_STATEMENT_KEYWORDS` (SELECT/WITH no longer pass there; `execute_statement`'s separate first-word check is removed as redundant).
- New `READ_ONLY_FORBIDDEN = {INSERT, UPDATE, DELETE, MERGE, INTO, SET}` — rejected anywhere in read mode.
- New `SET_OPERATORS = {UNION, EXCEPT, INTERSECT}`.

Checks, in order; the first failure wins. "Word" means a `WORD` token; comparisons are upper-cased. `STRING`, `QUOTED_IDENT` and comments are never inspected for keywords.

1. Empty/whitespace-only input → `"Query cannot be empty"`.
2. `tokenize`; `LexError` → `"Invalid SQL: <message>"`.
3. No tokens → `"Query cannot be empty"`.
4. A `SEMICOLON` that is not the last token → `"Multiple statements are not allowed"`.
5. A word starting with `XP_` or `SP_` → `"System procedure calls not allowed: xp_*"` / `"...: sp_*"` (existing message; runs before step 6 so existing tests for `EXEC xp_cmdshell` still see `xp_`).
6. A word in `BLOCKED_KEYWORDS` → `"Blocked keyword detected: <KW>"`.
7. A word in `STATEMENT_WORDS` → `"Statement not allowed: <KW>"`.
8. A pair in `BLOCKED_PAIRS` → `"Statement not allowed: <W1> <W2>"`.
9. A word whose last dotted part is in `BLOCKED_FUNCTIONS` → `"Function not allowed: <NAME>"`.
10. First token is not an allowed first word → `"Statement type '<X>' not allowed"`.
11. Mode-specific single-statement rules (below).

**Read mode (`execute_query`):**

- Any word in `READ_ONLY_FORBIDDEN` → `"Data modification not allowed in read-only query: <KW>"`.
- Depth-0 `SELECT` words: the first is allowed; each later one must be immediately preceded by a set operator (`UNION`, `UNION ALL`, `EXCEPT`, `INTERSECT`), else `"Multiple statements are not allowed"`. CTE bodies are inside parentheses (depth ≥ 1) and unaffected.

**Modify mode (`execute_statement`):** let `first` be the first word.

- A later depth-0 `INSERT`/`UPDATE`/`DELETE`/`MERGE` → `"Multiple statements are not allowed"`.
- `SET` words: allowed only when `first` is `UPDATE`, and only one at depth 0 → else `"Multiple statements are not allowed"`.
- Depth-0 `SELECT` words: allowed only when `first` is `INSERT`; the first is free, later ones need a preceding set operator.
- `INTO` words: allowed only at token index 1 when `first` is `INSERT`, or as the first `INTO` after a depth-0 `OUTPUT` word → else `"INTO not allowed here"`.

Consequences (each row is a test):

| Input | Tool | Before | After |
|---|---|---|---|
| `WITH c AS (SELECT 1 x) SELECT * FROM c` | query | fails at execution (wrapper) | runs |
| `SELECT * FROM t ORDER BY id` | query | fails at execution | runs |
| `SELECT a FROM t UNION ALL SELECT b FROM u` | query | runs | runs |
| `SELECT * FROM t ORDER BY id OFFSET 0 ROWS FETCH NEXT 5 ROWS ONLY` | query | fails at execution | runs |
| `-- note\nSELECT 1` | query | rejected (`--`) | runs |
| `SELECT * FROM t WHERE x = 'DROP'` | query | rejected | runs |
| `SELECT [Create], Situação FROM t` | query | rejected | runs |
| `SELECT 1 /* DROP */` / `SELECT 1;` | query | rejected / runs | runs / runs |
| `SELECT 1; SELECT 2` | query | wrapper error | rejected: multiple statements |
| `SELECT 1 SELECT 2` | query | wrapper error | rejected: multiple statements |
| `SELECT 1 WAITFOR DELAY '00:01'` | query | wrapper error | rejected: WAITFOR |
| `SELECT 1 USE master` | query | wrapper error | rejected: USE |
| `SELECT 1 SET ROWCOUNT 0` | query | wrapper error | rejected: SET |
| `WITH c AS (SELECT 1 x) DELETE FROM t` | query | wrapper error | rejected: read-only |
| `SELECT * INTO NewTable FROM t` | query | wrapper error | rejected: read-only |
| `SELECT NEXT VALUE FOR dbo.Seq` | query | wrapper error | rejected: NEXT VALUE |
| `SELECT * FROM sys.fn_trace_gettable('x', 1)` | query | runs | rejected: function |
| `SELECT 'unterminated` | query | wrapper error | rejected: invalid SQL |
| `SELECT 1 --\rDELETE FROM t` | query | wrapper error | rejected: read-only |
| `INSERT INTO t VALUES (1); EXEC('...')` | statement | **runs** | rejected: EXEC |
| `INSERT INTO t VALUES (1) DELETE FROM u` | statement | **runs** | rejected: multiple statements |
| `UPDATE t SET a=1 SELECT * INTO x FROM y` | statement | **runs** | rejected |
| `UPDATE t SET a=1 DISABLE TRIGGER trg ON t` | statement | **runs** | rejected: DISABLE TRIGGER |
| `INSERT INTO t (a) SELECT a FROM u WHERE b = 1` | statement | runs | runs |
| `UPDATE t SET a = (SELECT MAX(b) FROM u) WHERE id = 1` | statement | runs | runs |
| `DELETE FROM t OUTPUT deleted.id INTO audit WHERE id = 1` | statement | runs | runs |
| `SELECT 1` | statement | rejected (second check) | rejected (first-word) |

`validate_identifier`, `validate_procedure_name`, `sanitize_table_name` are unchanged. `execute_procedure` does not call `validate_query`.

### 1.3 `tools/query_execution.py`

- Delete `_inject_top_clause`.
- `execute_query` calls `_get_db(database).execute_query(sql, params_tuple, max_rows=limit + 1)` with the original SQL; `truncated` logic unchanged.
- `execute_statement`: remove the redundant first-word check; derive `statement_type` for the audit log from `tokenize(sql)[0].text.upper()`.
- `execute_query_file` unchanged (delegates to `execute_query`).

### 1.4 `database.py` — row limiting and cursor cleanup

`DatabaseManager.execute_query(sql, params=None, max_rows: int | None = None)`:

```
with self.get_cursor() as cursor:
    if max_rows is not None:
        cursor.execute(f"SET ROWCOUNT {int(max_rows)}")   # literal int: a parameterized
                                                         # SET runs inside sp_executesql
                                                         # and reverts when it returns
    try:
        cursor.execute(sql[, params])
        rows = cursor.fetchmany(max_rows) if max_rows is not None else cursor.fetchall()
    finally:
        if max_rows is not None:
            try:
                cursor.cancel()                          # discard anything still pending
                cursor.execute("SET ROWCOUNT 0; SELECT DB_NAME()")
                current_db = cursor.fetchone()[0]
            except pyodbc.Error as e:
                raise SessionStateError("could not reset session state") from e
            if current_db != self.config.database:
                raise SessionStateError("session database changed")
```

- The reset batch also checks `DB_NAME()` in the same round trip. `USE` is already rejected by §1.2, so this is defense in depth: a connection whose database changed by any route is retired, never reused.
- If `cursor.description is None` after the user SQL (a statement that returns no result set), the method returns `[]` as today. The single-statement rule (§1.2) means there is never a later result set being silently skipped.
- `SessionStateError(RuntimeError)` is defined in `database.py` (internal; tools already turn exceptions into error dicts).
- `PooledConnection` gains `invalid: bool = False`. `ConnectionPool.release()` closes invalid connections instead of re-queuing them.
- `get_cursor()` (pooled): on `SessionStateError`, set `pooled_conn.invalid = True` before re-raising.
- `get_cursor()` (both modes), exception path: close the cursor **before** `rollback()` (a pending result set makes rollback fail with "Connection is busy"); the cursor is closed exactly once.
- `max_rows=None` keeps `fetchall()` for other callers.

### 1.5 `tools/stored_procedures.py` — cap procedure results

`execute_procedure` reads at most 10,000 rows with `fetchmany(10_001)` and adds `"truncated": bool` to its success response (additive key; no existing key changes). `SET ROWCOUNT` is **not** used here because it would also limit DML inside the procedure.

### 1.6 `audit.py` — masked preview and hash

- New helper `_mask_sql(sql) -> str | None`: rebuild the SQL from tokens, replacing `STRING`, `NUMBER`, and `"`-quoted `QUOTED_IDENT` tokens with `?`. Between two tokens, emit one space if the original had any gap (whitespace or comment) and nothing otherwise, so `a.b` and `>=` stay intact and comments disappear. Returns `None` on `LexError`.
- `_get_sql_preview(sql, max_length)`: `_mask_sql` truncated to `max_length` with `...`; `"<unparseable>"` when masking fails.
- `_hash_sql(sql)`: SHA-256 (first 16 hex chars) of `_mask_sql(sql)`, falling back to the raw SQL only when masking fails. The log field keeps the name `sql_hash`; it now fingerprints the query shape, so equal queries with different literals share a hash and literals cannot be brute-forced from it.

Example: `SELECT * FROM Cliente WHERE cpf = '123.456.789-00' AND id>=42` → `SELECT * FROM Cliente WHERE cpf = ? AND id>=?`.

### 1.6a `errors.py` — redact values echoed by the driver

SQL Server echoes data values in a few error messages, and the sanitized text reaches both tool responses and the audit `error` field. Add these patterns to `SENSITIVE_PATTERNS` (applied by `sanitize_error`, so both destinations are covered):

| SQL Server message fragment | Replacement |
|---|---|
| `The duplicate key value is (...)` (errors 2627, 2601) | `The duplicate key value is ([REDACTED])` |
| `Truncated value: '...'` (error 2628) | `Truncated value: '[REDACTED]'` |
| `Conversion failed when converting the <type> value '...' to data type` (error 245) | `... the <type> value '[REDACTED]' to data type` |

Object names in messages (`Invalid object name 'dbo.Req'`) are left intact; they are needed for debugging and are not data.

### 1.7 Tests for §1

- New `tests/test_sql_lexer.py`: every token kind; `''`, `]]`, `""` escapes; `N'..'` at token start vs `columnN'x'`; `0xFF` vs `0`; `.5`, `1e5`; `--` ended by `\r` and by `\n`; nested block comments; `Situação` and `@@VERSION` as single words; `\xa0` as `OTHER`; depth tracking; each unterminated construct raises `LexError`.
- `tests/test_security.py`: every row of the §1.2 table; `xp_`/`sp_` inside strings and brackets not blocked; `validate_identifier("Print")` still valid.
- `tests/conftest.py`: `mock_cursor.fetchmany.side_effect = lambda n: rows[:n]` using the same rows as `fetchall`.
- `tests/test_database.py`: `execute_query` with `max_rows` issues `SET ROWCOUNT n`, the SQL unchanged, `fetchmany(n)`, `cancel()`, `SET ROWCOUNT 0`, in that order; without `max_rows` none of those; a failing reset raises `SessionStateError` and the pooled connection is closed rather than re-queued; the exception path closes the cursor before rollback.
- `tests/test_server.py`: the `execute_query` tool passes SQL unmodified with `max_rows=limit+1` and reports `truncated`; `execute_procedure` caps at 10,000 and reports `truncated`.
- New `tests/test_audit.py`: masking, gap handling, truncation, `<unparseable>`, equal hashes for queries differing only in literals.
- `tests/test_database.py`: reset batch returning a different `DB_NAME()` raises `SessionStateError` and the connection is closed.
- New `tests/test_errors.py`: each §1.6a pattern redacts the value; `Invalid object name` keeps the object name; existing credential/IP redaction still applies.
- Delete `tests/test_inject_top_clause.py`.

---

## 2. Connections, pool, startup

### 2.1 `DatabaseManager.connect()` with pooling → error

With `use_pool=True`, `connect()` raises `RuntimeError("connect() is not supported when pooling is enabled; use get_cursor()")`. Remove the pooled branch, the `DeprecationWarning` (and the now-unused `import warnings`), and `_current_pooled_conn`. Non-pooled behavior unchanged.

### 2.2 Non-pooled health check rollback

`_is_connected()` calls `rollback()` after `SELECT 1`, matching `ConnectionPool._is_connection_healthy`. Low value (only reachable in non-pooled mode, which no production path uses) but keeps the two health checks consistent.

Also in non-pooled `connect()`: when `_is_connected()` is false and a new connection is opened, close the old `_connection` first (ignoring errors) instead of overwriting it, so a dead connection is not leaked.

### 2.3 `ConnectionPool.acquire()` — comment only

Comment at `queue.get(timeout=min(remaining, 0.1))`: the short timeout is required because retiring a connection on release frees a creation slot without putting anything on the queue; a waiter blocked for the full timeout would miss it. (Confirmed by review against `pool.py` release/`_close_connection`.)

### 2.4 `DatabaseRegistry.close()` — docstring only

States that it is a shutdown path that closes every manager, logging per-manager failures rather than raising, so one failure cannot leave other pools open; `close_database()` raises.

### 2.5 Startup validation (log and keep running)

**Safe config errors at the source (`config.py`):** integer env vars are parsed with a helper `_int_env(name, default)` that raises `ValueError(f"{name} must be an integer")`, so no error message ever contains an env value. Alias-pattern errors already contain only the alias name.

**Per-alias tolerance (`registry.py`):** `DatabaseRegistry.from_env()` loads each alias independently. An alias whose config fails is recorded in `_config_errors: dict[str, str]` with a safe message (see below) and left out of `_configs`. `get(name)` for such an alias raises `ValueError(f"Database '{name}' is misconfigured: {message}")`; tools already return that as an error dict. `list_databases`/`get_database_info` report it with `"status": "misconfigured"`. Other aliases work normally. If `DB_DATABASES` itself is invalid, `from_env()` still raises.

**Safe message format:** for a pydantic `ValidationError`, `"; ".join(f"{'.'.join(map(str, err['loc']))}: {err['type']}" for err in e.errors(include_input=False))` — e.g. `password: string_too_short`. For other `ValueError`s, `str(e)` (safe after `_int_env`).

**`main()` order:**

1. `load_dotenv(<repo>/.env)` — so `LOG_LEVEL`/`LOG_FORMAT` in `.env` work.
2. `setup_logging()`.
3. `get_registry()`; for each entry in `_config_errors`, log at ERROR: `Database '<alias>' configuration invalid: <safe message>`. If `DB_DATABASES` is invalid, log it and continue; tool calls re-raise the same error.
4. `mcp.run(transport="stdio")`.

`lifespan` stays shutdown-only. The server always starts, so the `.claude/rules/sql-server-connection.md` setup flow keeps working.

### 2.6 Tests for §2

- `tests/test_database.py`: `connect()` raises in pooled mode; `_is_connected` rolls back; reconnect closes the old connection.
- `tests/test_config.py`: `DB_TIMEOUT=s3cr3t!` raises `ValueError` whose message names `DB_TIMEOUT` and does not contain `s3cr3t!`.
- `tests/test_registry.py`: a misconfigured secondary alias is reported, `get()` on it raises the safe message, `default` still works; secrets absent from messages.
- New startup test (in `tests/test_server.py`): `main()` with `mcp.run` patched calls `load_dotenv`, `setup_logging`, logs config errors without values, and still calls `mcp.run`. Uses a fixture that patches `load_dotenv`/`env_path` (no real `.env`) and resets `server._registry` afterward.

---

## 3. Observability and cleanup

### 3.1 Logging setup

- Remove `logging.basicConfig(level=logging.INFO)` from `server.py`. Note: `MCPServer.__init__` calls the SDK's own `configure_logging` (→ `basicConfig`), which today is a no-op because our call runs first; after removal the SDK installs a stderr handler at import, and `setup_logging()` in `main()` replaces it. Output stays on stderr.
- `main()` runs `setup_logging()` per §2.5.

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

- Applied directly beneath `@mcp.tool()` on each of the 10 tools (`@mcp.tool()` outermost). Not applied to resources.
- Verified by review: schemas unchanged; mypy strict accepts it; SDK 2.x runs sync tools via `anyio.to_thread.run_sync` in a copied context, and 8 concurrent calls observed 8 distinct IDs.
- The ID appears in both formats. JSON: the existing `request_id` field. Text: `setup_logging` attaches a `logging.Filter` to its handler that sets `record.request_id` (or `-` outside a call), and the text format becomes `%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s`.
- Log lines emitted by the SDK outside a tool call (startup, protocol handling) have no ID (`-`); only work inside a tool call is correlated.

### 3.3 Dead code removal

- `errors.py`: delete `MCPError`, `ValidationError`, `ConnectionError`, `QueryError`, `TimeoutError`. Keep `sanitize_error`, `simplify_error`, `create_error_response`. (`pool.py` raises the builtin `TimeoutError`; unaffected.)
- `tools/__init__.py` / `resources/__init__.py`: delete `ALL_TOOLS` / `ALL_RESOURCES` and their `__all__` entries.
- `logging_config.py`: delete `get_logger`, `LoggerAdapter`, `get_logger_with_context`, `set_request_id`, `clear_request_id`.

Verified by review: no references in `src/`, `tests/`, or `.claude/`; README mentions are handled in §4.

### 3.4 Cache key construction

In `cache.cached`, bind arguments to the function signature before building the key, so positional and keyword calls share an entry:

```python
bound = inspect.signature(func).bind(*args, **kwargs)
bound.apply_defaults()
key = f"{key_prefix or func.__name__}|{sorted(bound.arguments.items())!r}"
```

The signature is computed once per decorated function, not per call. No default-prefix change: every caller passes `key_prefix`. Cached functions receive only `str`/`None`, so `repr` is deterministic. Tests: `("a", "b")` and `("a:b",)` produce different keys; `f("x", schema="dbo")` and `f("x", "dbo")` hit the same entry.

### 3.5 Kept as-is

The comment above `MCPServer(...)` about the positional-argument trap stays; it is accurate.

### 3.6 Tests for §3

- New `tests/test_logging_config.py`, with a fixture that saves and restores the root logger's handlers and level: JSON and text formatters; text lines include `[<id>]` inside a call and `[-]` outside; `setup_logging` honors `LOG_LEVEL`/`LOG_FORMAT` and writes to stderr; `with_request_id` sets an ID during the call, restores it afterward (also on exception), and preserves the signature.
- `tests/test_server.py`: importing `server` does not call `logging.basicConfig` from our code; `main()` calls `setup_logging` (covered by §2.6's startup test).
- `tests/test_concurrency.py`: concurrent wrapped calls see distinct `request_id` values; update the docstring that calls `connect()` deprecated.
- `tests/test_cache.py`: key collision case.
- New `tests/test_utils.py`: `get_db`/`get_registry` lazy import and caching (raises `utils.py` from 38%).

---

## 4. Docs and hygiene

Doc edits land with the code they describe; a final grep (last step) verifies every referenced module, test file, and keyword exists.

- **Test counts:** replace hardcoded counts (README ~17 and ~732; CLAUDE.md ~144) with count-free wording; keep "85%+ coverage".
- **Test file lists:** CLAUDE.md — keep `test_audit.py` and `test_errors.py` (both created in §1.7); add `test_config_multi.py`, `test_query_dir.py`, `test_sql_lexer.py`, `test_logging_config.py`, `test_utils.py`. README table and tree — remove `test_inject_top_clause.py`; add `test_server_registration.py`, `test_concurrency.py`, `test_sql_lexer.py`, `test_logging_config.py`, `test_audit.py`, `test_errors.py`, `test_utils.py`.
- **`SQL_SERVER_*`:** README configuration subsection on both sources and precedence (process `DB_*` > `SQL_SERVER_*` > `.env` `DB_*`; named aliases only `DB_{ALIAS}_*`). `.env.example` gains a commented `SQL_SERVER_*` block.
- **Changed behavior, all locations:**
  - README Security section and CLAUDE.md "Blocked SQL Keywords"/"Statement Type Enforcement": §1.2 rules, the recommendation of a read-only login, CTE-DML unsupported.
  - README ~440 (TOP wrapper) and `.claude/rules/mcp-tools.md`: `SET ROWCOUNT` + `fetchmany` limiting; `execute_procedure` 10,000-row cap and `truncated`.
  - README ~93 (errors.py row) and ~712–716 (exception hierarchy): replace with the error-dict format.
  - README ~691 (preview) and logging section: masked preview, `sql_hash` as query-shape fingerprint, `request_id` in JSON and text logs, `.env` logging vars honored, redaction of values echoed in driver errors (§1.6a).
  - README Security section: unbracketed column names matching §1.2 statement words (e.g. `Send`, `Receive`) must be bracketed.
  - README ~799, ~806 (tree comments `ALL_TOOLS`/`ALL_RESOURCES`): remove.
  - README and CLAUDE.md: startup validation behavior (server starts; misconfigured aliases reported).
  - README and CLAUDE.md architecture: add `sql_lexer.py`.
- **Lessons path:** `.claude/agents/README.md`, `.claude/agents/lesson-retriever.md`, `.claude/agents/session-lessons-documenter.md` point to the `sql-playground` repository's `docs/lessons/` (where `INDEX.md` exists) instead of a local path.
- **Merged branch:** `git branch -d feat/mcp-2x-migration` as the final step, after asking the user.

Already resolved before this spec: `.coverage` untracked and ignored (`5434c81`).

---

## 5. Existing tests that change

| Test | Change |
|---|---|
| `test_database.py::test_connect_emits_deprecation_warning_when_pooled` | Now asserts `RuntimeError` |
| `test_database.py::TestDatabaseManagerIsConnected::test_is_connected_true_when_valid` | Construct with `use_pool=False` |
| `test_database.py::TestDatabaseManagerIsConnected::test_is_connected_false_on_pyodbc_error` | Construct with `use_pool=False` |
| `test_security.py` cases asserting SELECT passes `allow_modifications=True` (if any) | Update: SELECT/WITH no longer allowed in modify mode |
| `test_inject_top_clause.py` | Deleted |
| `test_server.py` cases asserting `SELECT TOP` in executed SQL (if any) | Assert SQL unmodified and `max_rows` |

## 6. Implementation order

1. `sql_lexer.py` + tests.
2. `validate_query` rules + security tests.
3. Row limiting (`SET ROWCOUNT`, `fetchmany`, `SessionStateError`, cursor close order), remove the TOP wrapper, `execute_statement` cleanup. **Steps 2 and 3 land together** so stacking is never unguarded.
4. `execute_procedure` cap.
5. Audit masking and hash; driver-error redaction (§1.6a).
6. `connect()`, `_is_connected`, reconnect close, comments/docstrings (§2.1–2.4).
7. `_int_env`, per-alias registry tolerance, `main()` startup order (§2.5) + logging setup (§3.1).
8. `with_request_id` (§3.2).
9. Dead code, cache key, `test_utils.py` (§3.3–3.4, §3.6).
10. Final doc grep; delete merged branch (ask first).

---

## Appendix A — Adversarial review findings and resolutions

Three independent reviews of revision 1 (commit `a357023`). Every finding is listed; "Rejected" means the finding was checked and deliberately not acted on, with the reason.

### A.1 SQL security review

| # | Severity | Finding | Resolution |
|---|---|---|---|
| S1 | Critical | Removing the TOP wrapper allows stacking without `;` (`SELECT 1 WRITETEXT … COMMIT`, `DISABLE TRIGGER`, `DENY`, `RECEIVE`, `WAITFOR` with `TABLOCKX`) | §1.2 `STATEMENT_WORDS`, `BLOCKED_PAIRS`, single-statement rule at depth 0 |
| S2 | High | Pooled-connection poisoning via `USE`/`SET` (rollback does not reset session state) | §1.2 rejects `USE` and `SET` (read mode); §1.4 `DB_NAME()` check retires changed connections |
| S3 | High | `fetchmany` loses the server row goal; closing with pending rows; rollback before close fails with "Connection is busy" | §1.4 `SET ROWCOUNT`, `cancel()`, close before rollback, invalid-connection retirement |
| S4 | High | Unsalted hash of raw SQL lets masked literals be brute-forced | §1.6 hash of masked SQL |
| S5 | Medium | `--` comment may end at a lone `\r` in SQL Server | §1.1 rule 2; fail-closed principle stated |
| S6 | Medium | ASCII word regex splits Portuguese identifiers; Unicode `\s`/`\d`/case-folding look-alikes | §1.1 character loop, `str.isalpha()`, exact whitespace set, ASCII digits, ASCII-only keyword sets |
| S7 | Medium | Side effects inside SELECT: `NEXT VALUE FOR`; SMB/NTLM leak via `fn_xe_file_target_read_file`, `fn_trace_gettable`, `fn_get_audit_file` | §1.2 `BLOCKED_PAIRS`, `BLOCKED_FUNCTIONS` |
| S8 | Medium | `execute_statement` DDL via `UPDATE … SELECT INTO`; `DISABLE TRIGGER`/`DENY` commit | §1.2 modify-mode rules |
| S9 | Low | Later result sets silently ignored when the first has no columns | §1.4 note; impossible under the single-statement rule |
| S10 | Nit | `N'` rule depends on alternation order (`columnN'x'`) | §1.1 rule 4; test in §1.7 |
| S11 | Nit | `1.e5`, `.5` tokenize oddly | §1.1 rule 6; tests in §1.7 |
| S12 | Nit | Preview spaces around `.` and `>=` | §1.6 gap-preserving join |
| S13 | Nit | Comments dropped from preview | Intended: no secrets leak from comments |
| S14 | Nit | `"..."` is a string under `QUOTED_IDENTIFIER OFF` | §1.6 masks `"`-quoted tokens |
| S15 | Nit | `WITH cte … UPDATE` rejected by `execute_statement` | Non-goal, documented (§4) |
| S16 | Nit | `[xp_cmdshell]` / `"sp_executesql"` as quoted identifiers bypass the prefix check | Rejected: a procedure cannot be invoked without `EXEC` except as the first statement, and the first word must be SELECT/WITH or DML. Test in §1.7 pins that these stay harmless |
| S17 | Nit | Four-part linked-server names | Rejected: read-only access through an existing linked server; `OPENQUERY`/`OPENROWSET` remain blocked |
| S18 | Nit | `GO` | Rejected: a client-side batch separator, sent to the server as an identifier; harmless |
| S19 | Nit | Bare `Update`/`Into` column names | Rejected: reserved words must already be bracketed in T-SQL; non-reserved statement words documented (§4) |
| S20 | Advice | Prefer a statement allowlist; recommend a read-only login | §1.2 single-statement rule; non-goals and §4 recommend `db_datareader` |

### A.2 Runtime correctness review

| # | Severity | Finding | Resolution |
|---|---|---|---|
| R1 | High | `LOG_LEVEL`/`LOG_FORMAT` in `.env` ignored: `setup_logging()` runs before `load_dotenv` | §2.5 `main()` loads `.env` first |
| R2 | High | Fail-fast exits via an anyio ExceptionGroup traceback, echoes raw env values, names no alias, stops at first alias | §2.5 redesigned: `_int_env`, per-alias errors, safe message format, keep running |
| R3 | Medium | §2.1 breaks 3 tests, not 1 | §5 lists all three |
| R4 | Medium | Planned lifespan tests read the real `.env`; password-leak test is vacuous; global `_registry` leaks between tests | §2.6 fixtures; secret placed in `DB_TIMEOUT` |
| R5 | Medium | One bad secondary alias takes down `default`; unconfigured server can't start | §2.5 per-alias tolerance; server always starts |
| R6 | Medium | `setup_logging` removes pytest's handlers | §3.6 save/restore fixture |
| R7 | Medium | SDK's own `configure_logging` → `basicConfig` not accounted for | §3.1 note |
| R8 | Low | `import warnings` becomes unused | §2.1 |
| R9 | Low | §2.2 rollback is low value | §2.2 kept for consistency, value stated |
| R10 | Low | Non-pooled reconnect overwrites the old connection without closing it | §2.2 close before reconnect |
| R11 | Low | Cache: every caller passes `key_prefix`; positional vs keyword calls get different keys | §3.4 default-prefix change dropped; signature binding unifies keys |
| R12 | Info | SDK logs outside tool calls carry no `request_id` | §3.2 stated |
| V | Verified | `functools.wraps` schema identical; mypy accepts ParamSpec decorator; per-call contextvars isolation; §2.3 polling reasoning; §2.4 close(); §3.3 no references | Recorded in §3.2, §2.3, §3.3 |

### A.3 Completeness review

| # | Severity | Finding | Resolution |
|---|---|---|---|
| C1 | High | Stacking without `;` (same as S1); `DENY` not blocked; `execute_statement` stacking unspecified | §1.2 |
| C2 | High | Adding `EXEC` to `BLOCKED_KEYWORDS` breaks two `xp_`/`sp_` message tests and makes `validate_identifier` reject tables named `Exec` | §1.2 separate `STATEMENT_WORDS`; prefix check before keyword checks |
| C3 | Medium | "fetchmany used" untestable in `test_server.py`; conftest has no `fetchmany` | §1.7 conftest `fetchmany` and tests in `test_database.py` |
| C4 | Medium | fetchmany performance and cursor-close behavior unverified | Same as S3 |
| C5 | Medium | Fail-fast is an unacknowledged behavior change; alias not available in error | Same as R2/R5 |
| C6 | Medium | `execute_procedure` still unbounded `fetchall` | §1.5 cap and `truncated` |
| C7 | Medium | Audit `error` field can carry values from driver messages | §1.6a redaction patterns |
| C8 | Medium | `utils.py` 38% coverage not addressed | §3.6 `test_utils.py` |
| M1 | Medium | `docs/lessons` path also in `lesson-retriever.md`, `session-lessons-documenter.md` | §4 |
| M2 | Medium | Docs sweep misses README ~93, ~440, ~691, ~712, ~799, ~806 | §4 |
| M3 | Medium | No test fails before the `basicConfig` fix; comment/deletion items can't have failing tests | §3.6 startup/import tests; exemptions in success criteria |
| M4 | Medium | Modify mode lets SELECT/WITH through validation, then a misleading message | §1.2 modify mode allows only INSERT/UPDATE/DELETE; redundant check removed |
| M5 | Medium | `N'` and hex/decimal order ambiguous; ASCII words garble previews | §1.1 rules 4, 6, 7 |
| M6 | Low | "Docs with code" contradicts "docs sweep" step | §4 and §6: docs with code, final step is verification grep only |
| N1 | Nit | Prefix message casing unspecified | §1.2 step 5 |
| N2 | Nit | anyio context-copy claim irrelevant | §3.2 reworded to the verified behavior |
| N3 | Nit | `request_id` only in JSON logs | §3.2 filter adds it to text logs |
| N4 | Info | Implementation order sound | §6 unchanged in structure |
| G1 | Scope | Module-qualified default cache prefix fixes nothing | Dropped (§3.4) |
| G2 | Scope | `request_id` wiring is new feature work | Kept: user decision |

### A.4 Items from the original recap intentionally not changed

| Recap item | Reason |
|---|---|
| `ConnectionPool.acquire()` "busy-poll" | Polling is required for correctness (§2.3); verified by review |
| `DatabaseRegistry.close()` swallows errors | Correct for a shutdown path (§2.4); verified by review |
| "Stale" comment at `server.py:56-58` | Accurate; documents the positional-argument trap (§3.5) |
| Named aliases lack `SQL_SERVER_*` fallback | Documented as intentional in `.claude/rules/sql-server-connection.md` |

---

## Appendix B — Plan-time corrections (revision 4)

Found while writing the implementation plan against the actual code; the plan implements these, and they supersede the sections named.

| Section | Correction | Reason |
|---|---|---|
| §2.5 `main()` step 1 | Read `LOG_LEVEL`/`LOG_FORMAT` with `dotenv_values` (process env first); do **not** call `load_dotenv` in `main()` | `DatabaseConfig.from_env` snapshots process `DB_*` before loading `.env` to rank explicit config above `SQL_SERVER_*`; loading `.env` earlier would make file values look explicit and break that precedence |
| §1.3, §1.4 | `DatabaseManager.execute_query(sql, params, max_rows=None, server_limit=False)`; `execute_query` tool passes `max_rows=limit+1, server_limit=True`; `execute_procedure` passes only `max_rows` | §1.5 requires procedures to use `fetchmany` without `SET ROWCOUNT`; one flag keeps both on one method |
| §1.4 | Reset runs `SET ROWCOUNT 0` and `SELECT DB_NAME()` as two statements | Avoids depending on result-set positioning of a multi-statement batch in pyodbc |
| §1.4 | Non-pooled `SessionStateError` closes and drops `_connection` | Non-pooled mode has no pool to retire the connection |
| §1.2 | `STATEMENT_WORDS` gains `ADD`; `BLOCKED_PAIRS` gains `(GET, CONVERSATION)`, `(MOVE, CONVERSATION)`, `(END, CONVERSATION)`; `BLOCKED_FUNCTIONS` matched as plain words (the tokenizer already splits `sys.` off) | `ADD SIGNATURE` / `ADD SENSITIVITY CLASSIFICATION` and Service Broker statements are further statement starters |
| §1.2 modify mode | A top-level `SELECT` is rejected after `VALUES`/`DEFAULT` | `INSERT ... VALUES (1) SELECT ...` would otherwise stack a second statement |
| §2.5 registry | `list_databases()` includes misconfigured aliases; valid entries in `get_database_info()` gain `"status": "ok"`; `DatabaseRegistry(..., config_errors=...)` accepts a misconfigured `default` | Lets `list_databases` and the `sqlserver://databases` resource show broken aliases |
| §5 | `test_registry.py::test_from_env` is rewritten to patch the per-alias loaders | `from_env` no longer calls `load_all_*` |

---

## Appendix C — Found during the live check (revision 5)

| Finding | Resolution |
|---|---|
| `get_database_names()` called `load_dotenv()` before `DatabaseConfig.from_env()` snapshotted explicit `DB_*`, so `.env` `DB_*` outranked `SQL_SERVER_*` on every real startup (pre-existing on `main`). The server targeted the `.env` database instead of the configured one. | Plan Task 15: `.env` is read with `dotenv_values` and never merged into `os.environ`; every loader resolves process env → `.env` explicitly. Precedence: process `DB_*` > `SQL_SERVER_*` (process, then `.env`) > `.env` `DB_*`. |
| `ConnectionPool.acquire()` retried a failing login every 100 ms until `acquire_timeout` (~10 logins per query). | Plan Task 16: re-raise the creation error immediately when the pool holds no connections; keep waiting only when others are checked out. |
