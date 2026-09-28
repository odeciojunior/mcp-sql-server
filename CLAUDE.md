# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Reference

```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Test
.venv/bin/pytest tests/ -v

# Run single test file
.venv/bin/pytest tests/test_server.py -v

# Run tests matching pattern
.venv/bin/pytest tests/ -v -k "test_execute_query"

# Coverage
.venv/bin/pytest tests/ -q --cov=mcp_sql_server --cov-report=term

# Type check
.venv/bin/python -m mypy src/mcp_sql_server/

# Run server (the `mcp-sql-server` console script is equivalent)
.venv/bin/python -m mcp_sql_server.server
```

## Architecture

```
                    +------------------+
                    |   MCP Client     |
                    |  (Claude, etc.)  |
                    +--------+---------+
                             | stdio
                    +--------v---------+
                    |    server.py     |
                    |   MCP Server     |
                    +--------+---------+
                             |
              +--------------+--------------+
              |                             |
    +---------v----------+       +----------v---------+
    |   tools/           |       |   resources/       |
    | - query_execution  |       | - database_info    |
    | - schema_discovery |       +--------------------+
    | - object_defs      |
    | - stored_procs     |
    | - registry_tools   |
    +--------+-----------+
             |
             +----------- utils.py (lazy imports)
                             |
                    +--------v---------+
                    |   registry.py    |
                    | DatabaseRegistry |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   database.py    |
                    | DatabaseManager  |
                    +--------+---------+
                             |
                    +--------v---------+
                    |     pool.py      |
                    |  ConnectionPool  |
                    +------------------+

Cross-Cutting: config.py, security.py, cache.py, audit.py, errors.py, logging_config.py, sql_lexer.py
```

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `server.py` | MCPServer initialization, tool/resource registration, lifecycle |
| `registry.py` | Named multi-database management with lazy initialization |
| `database.py` | Connection management, cursor context managers, query execution |
| `pool.py` | Thread-safe connection pooling with health checks and retirement |
| `config.py` | Pydantic config from `.env`, multi-DB env parsing |
| `security.py` | SQL validation, blocked keyword detection, identifier sanitization |
| `sql_lexer.py` | T-SQL tokenizer used by validation and audit masking |
| `cache.py` | TTL cache with `@cached` decorator for metadata |
| `audit.py` | Query hashing, execution timing, audit events |
| `errors.py` | Error sanitization (credentials, IPs, echoed data values) and error-response builder |
| `logging_config.py` | Text/JSON formatters, `RequestIdFilter`, `with_request_id` decorator, `setup_logging()` |
| `utils.py` | Lazy `get_db`/`get_registry` accessors; importing `server` inside the function avoids a server ↔ tools cycle |

## MCP Tools and Resources

**Tools:** `execute_query`, `execute_statement`, `execute_query_file`, `list_tables`, `describe_table`, `get_view_definition`, `get_function_definition`, `list_procedures`, `execute_procedure`, `list_databases`

**Resources:** `sqlserver://tables`, `sqlserver://database/info`, `sqlserver://functions`, `sqlserver://pool/stats`, `sqlserver://databases`

All tools accept optional `database` parameter (default: `"default"`) for multi-database support.

`list_databases` takes `probe` instead (default `true`): it opens a real connection per database. `status` is `ok`/`unreachable`/`misconfigured`/`unknown` and never claims reachability it did not measure.

## Configuration

Copy `.env.example` to `.env` and fill in your values. Required: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` (or `SQL_SERVER_*` via `.claude/settings.local.json`).

- Default-DB precedence: process `DB_*` > `SQL_SERVER_*` > `.env` `DB_*`; named aliases read only `DB_{ALIAS}_*`.
- `.env` location: explicit arg > `$MCP_SQL_SERVER_ENV_FILE` > `./.env` (cwd only, never parents) > package-relative, which exists only for an editable install.
- `.env` is read with `dotenv_values` and must never be merged into `os.environ` (`load_dotenv`) — that silently breaks precedence.
- The local `.env` may define aliases on production hosts: before any live check, print the resolved host (`load_database_config("default").host`, no connection) and only query the `default` alias.

**Warning:** `.env` contains credentials and is gitignored. Use `.env.example` as a template.

At startup each database config is validated and errors are logged (no values); the server still starts, and only calls to a misconfigured database fail.

### Multi-Database Support

```env
DB_DATABASES=analytics,archive
DB_ANALYTICS_HOST=...
DB_ANALYTICS_USER=...
```

Each alias reads prefixed env vars (`DB_{ALIAS}_*`) and gets independent pool configuration via `DB_{ALIAS}_POOL_*` (e.g. `DB_ANALYTICS_POOL_MAX_SIZE`).

`SQL_SERVER_HOST/PORT/USER/PASSWORD/DATABASE/DRIVER/ENCRYPT/TRUST_CERT` map to `DB_HOST/PORT/USER/PASSWORD/NAME/DRIVER/ENCRYPT/TRUST_CERT` for the default database only (see `.claude/rules/sql-server-connection.md`).

### Additional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PORT` | `1433` | SQL Server port |
| `DB_DRIVER` | `ODBC Driver 17 for SQL Server` | ODBC driver name |
| `DB_ENCRYPT` | `false` | Enable encryption |
| `DB_TRUST_CERT` | `false` | Trust server certificate |
| `DB_TIMEOUT` | `30` | Connection timeout (seconds) |
| `DB_QUERY_TIMEOUT` | `120` | Query timeout (seconds) |
| `DB_POOL_MIN_SIZE` | `1` | Minimum pool connections |
| `DB_POOL_MAX_SIZE` | `5` | Maximum pool connections |
| `DB_POOL_IDLE_TIMEOUT` | `300` | Idle connection retirement (seconds) |
| `DB_POOL_HEALTH_CHECK_INTERVAL` | `30` | Health check interval (seconds) |
| `DB_POOL_ACQUIRE_TIMEOUT` | `10.0` | Wait for a pooled connection (seconds) |
| `DB_POOL_MAX_LIFETIME` | `3600` | Maximum connection age (seconds) |
| `QUERY_DIR` | `query/` | Directory for `execute_query_file` SQL files |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FORMAT` | `text` | Log format (`text` or `json`) |

## Security

### Blocked SQL Keywords

| Category | Keywords |
|----------|----------|
| DDL | `DROP`, `TRUNCATE`, `ALTER`, `CREATE` |
| DCL | `GRANT`, `REVOKE`, `DENY` |
| Admin | `SHUTDOWN`, `BACKUP`, `RESTORE`, `DBCC`, `KILL`, `RECONFIGURE`, `CHECKPOINT` |
| External | `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `BULK` |
| Statement starters | `EXEC`, `EXECUTE`, `DECLARE`, `USE`, `WAITFOR`, `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVE`, `IF`, `WHILE`, `GOTO`, `RETURN`, `PRINT`, `RAISERROR`, `THROW`, `OPEN`, `CLOSE`, `DEALLOCATE`, `SETUSER`, `REVERT`, `RECEIVE`, `SEND`, `ADD`, `WRITETEXT`, `UPDATETEXT`, `READTEXT` |
| Pairs / functions | `NEXT VALUE`, `ENABLE/DISABLE TRIGGER`, `GET/MOVE/END CONVERSATION`, `fn_xe_file_target_read_file`, `fn_trace_gettable`, `fn_get_audit_file`, `fn_get_audit_file_v2`, `fn_dump_dblog`, `fn_dump_dblog_xtp`, `fn_xe_telemetry_blob_target_read_file`, `dm_os_file_exists`, `dm_os_enumerate_filesystem` |
| System Procs | `xp_*`, `sp_*` prefixes |

Validation tokenizes SQL (`sql_lexer.py`); strings, quoted identifiers, and comments are never checked for keywords.

### Statement Type Enforcement

- One statement per call at parenthesis depth 0 (no reliance on `;`).
- `execute_query`: first word `SELECT`/`WITH`; `INSERT`/`UPDATE`/`DELETE`/`MERGE`/`INTO`/`SET` rejected anywhere; extra top-level `SELECT` only after `UNION`/`EXCEPT`/`INTERSECT`.
- `execute_statement`: first word `INSERT`/`UPDATE`/`DELETE`; one `SET` in `UPDATE`; top-level `SELECT` only in `INSERT ... SELECT`; `INTO` only in `INSERT INTO` / `OUTPUT ... INTO`.
- `execute_statement` also rejects `UPDATE STATISTICS`, a `SET` after `FROM`/`WHERE`/`OUTPUT`/`OPTION`, and any DML word other than the first token (nested/composable DML).
- Unbalanced parentheses are rejected; a single trailing `;` is allowed.
- CTE-prefixed DML is unsupported.
- Query hints are allowed: join/union hints such as `INNER MERGE JOIN` and `OPTION (MERGE JOIN)`, and `OPTION (USE HINT(...))` / `OPTION (USE PLAN ...)`.

### Gotchas

- Ambient `SQL_SERVER_*`/`DB_*` from the shell outrank `.env` file values — run config checks under `env -i` or they verify the process env while appearing to test the file.
- `tests/conftest.py` neutralises every `.env` resolution branch (points `MCP_SQL_SERVER_ENV_FILE` at a nonexistent file); a new branch must be neutralised there too or the suite reads the real `.env`.
- `.venv` may be the runtime for a registered MCP server; a bare `pip install --force-reinstall` can break a running setup — force only the package, with `--no-deps`.

- Validator changes must fail closed: SQL Server ends `--` comments at a bare `\r`, reads `1e` as a float, and lexes `1e--x` as `1e-` then `-x` (verified live).
- `execute_query` limits rows with session `SET ROWCOUNT` + `fetchmany`, then resets and checks `DB_NAME()`; never parameterize the `SET` (it reverts inside `sp_executesql`).
- `execute_procedure` runs on the read path and never commits — procedure writes are rolled back (documented, pinned by a test).
- `execute_query` clamps `limit` to 1–10000; `execute_procedure` caps at `MAX_PROCEDURE_ROWS = 10_000` via `fetchmany` only (no `SET ROWCOUNT`, which would cap the procedure's own DML) and reports `truncated`.
- MCP tool names are un-prefixed (`execute_query`), set via `@mcp.tool(name=...)`; wrapper functions keep `_` names to avoid clashing with imports.

## Testing

Comprehensive suite with 95%+ coverage (see the coverage command above). Tests use mocked database connections (no live DB required).

`drift.yml` installs non-editable (`pip install .`): assertions that assume a repo checkout beside the package fail there, correctly.

Key test files:
- `test_server.py` - Tool and resource integration tests
- `test_pool.py` - Connection pool lifecycle, health checks
- `test_security.py` - SQL validation, blocked keywords
- `test_database.py` - DatabaseManager operations
- `test_config.py` - Configuration parsing, validation, and `.env` path resolution
- `test_registry.py` - Multi-database registry
- `test_cache.py` - TTL cache behavior
- `test_audit.py` - Audit logging and query hashing
- `test_errors.py` - Error sanitization and value redaction
- `test_server_registration.py` - MCP SDK registration surface (tool/resource names, schemas)
- `test_concurrency.py` - Parallel pool/registry access (mcp 2.x runs sync handlers in threads)
- `test_config_multi.py` - Multi-database config and alias parsing
- `test_query_dir.py` - Query directory resolution
- `test_sql_lexer.py` - T-SQL tokenizer
- `test_logging_config.py` - Log formats and request IDs
- `test_utils.py` - Lazy server accessors
- `test_entrypoint.py` - `python -m mcp_sql_server.server` over stdio (port 1, `list_databases` only; regression for the double-import bug)

Conventions:
- Never let a test open a real DB connection — the local `.env` can target production; fixtures mock `pyodbc.connect` (`mock_pyodbc`, `mock_connection`, `mock_cursor`, `sample_config`).
- `tests/conftest.py` autouse fixture points `$MCP_SQL_SERVER_ENV_FILE` at a missing file, repoints `_PACKAGE_ENV_PATH`, and strips `DB_*`/`SQL_SERVER_*`; tests must never read the real `.env`.
- Tool-layer tests patch the tool module's alias (`patch.object(query_execution, "_get_db")`), not `utils.get_db`.
- `test_concurrency.py` uses `_run_concurrently(target, workers=...)` for real-thread tests (mcp 2.x runs sync handlers in threads).
- `test_logging_config.py` restores the root logger via an autouse fixture; logging tests must not leak handlers.

## Repo Layout

- `docs/superpowers/{specs,plans}/` - design specs and implementation plans for past changes
- `query/` - SQL files for `execute_query_file` (not checked in; create as needed)
- `.claude/rules/*.md` - auto-loaded rules (SQL conventions, connection setup, tool reference); `.claude/agents/` - SQL Server specialist subagents
- `README.md` - full user-facing documentation; keep it in sync with behavior changes

## CI

- `ci.yml` — push to `main`, PRs, manual; matrix Python 3.10 and 3.14; installs `unixodbc`, runs the suite.
- `drift.yml` — Mondays 06:23 UTC + manual; resolves dependencies fresh and probes above the `<3` ceilings, the only check that can meet a new upstream major.
- Dependabot raises floors weekly (pip + github-actions); it never widens the ceilings.

## Git Workflow

Conventional commits with scopes (`fix(server): ...`, `feat(security): ...`, `docs: ...`); one branch per change, merged to `main` with `--no-ff`.

## Type Safety

Full mypy strict mode compliance. Run `mypy src/mcp_sql_server/` to verify.
