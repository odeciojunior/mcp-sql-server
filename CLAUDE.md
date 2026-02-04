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

# Type check
.venv/bin/python -m mypy src/mcp_sql_server/

# Run server
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
                    |  FastMCP Server  |
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

Cross-Cutting: config.py, security.py, cache.py, audit.py, errors.py, logging_config.py
```

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `server.py` | FastMCP initialization, tool/resource registration, lifecycle |
| `registry.py` | Named multi-database management with lazy initialization |
| `database.py` | Connection management, cursor context managers, query execution |
| `pool.py` | Thread-safe connection pooling with health checks and retirement |
| `config.py` | Pydantic config from `.env`, multi-DB env parsing |
| `security.py` | SQL validation, blocked keyword detection, identifier sanitization |
| `cache.py` | TTL cache with `@cached` decorator for metadata |
| `audit.py` | Query hashing, execution timing, audit events |
| `errors.py` | Exception hierarchy, error sanitization |

## MCP Tools and Resources

**Tools:** `execute_query`, `execute_statement`, `execute_query_file`, `list_tables`, `describe_table`, `get_view_definition`, `get_function_definition`, `list_procedures`, `execute_procedure`, `list_databases`

**Resources:** `sqlserver://tables`, `sqlserver://database/info`, `sqlserver://functions`, `sqlserver://pool/stats`, `sqlserver://databases`

All tools accept optional `database` parameter (default: `"default"`) for multi-database support.

## Configuration

Copy `.env.example` to `.env` and fill in your values. Required: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.

### Multi-Database Support

```env
DB_DATABASES=analytics,archive
DB_ANALYTICS_HOST=...
DB_ANALYTICS_USER=...
```

Each alias reads prefixed env vars (`DB_{ALIAS}_*`) and gets independent pool configuration.

### Additional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PORT` | `1433` | SQL Server port |
| `DB_DRIVER` | Auto-detected | ODBC driver name |
| `DB_ENCRYPT` | - | Enable encryption |
| `DB_TRUST_CERT` | - | Trust server certificate |
| `DB_TIMEOUT` | `30` | Connection timeout (seconds) |
| `DB_QUERY_TIMEOUT` | `120` | Query timeout (seconds) |
| `DB_POOL_MIN_SIZE` | `2` | Minimum pool connections |
| `DB_POOL_MAX_SIZE` | `10` | Maximum pool connections |
| `QUERY_DIR` | `query/` | Directory for `execute_query_file` SQL files |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FORMAT` | `text` | Log format (`text` or `json`) |

## Security

### Blocked SQL Keywords

| Category | Keywords |
|----------|----------|
| DDL | `DROP`, `TRUNCATE`, `ALTER`, `CREATE` |
| DCL | `GRANT`, `REVOKE` |
| Admin | `SHUTDOWN`, `BACKUP`, `RESTORE`, `DBCC`, `KILL` |
| External | `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `BULK` |
| System Procs | `xp_*`, `sp_*` prefixes |

### Statement Type Enforcement

- `execute_query`: only `SELECT` and `WITH`
- `execute_statement`: only `INSERT`, `UPDATE`, `DELETE`

## Testing

337 tests with 85%+ coverage. Tests use mocked database connections (no live DB required).

Key test files:
- `test_server.py` - Tool and resource integration tests
- `test_pool.py` - Connection pool lifecycle, health checks
- `test_security.py` - SQL validation, blocked keywords
- `test_database.py` - DatabaseManager operations
- `test_config.py` - Configuration parsing and validation
- `test_registry.py` - Multi-database registry
- `test_cache.py` - TTL cache behavior
- `test_audit.py` - Audit logging and query hashing
- `test_errors.py` - Error hierarchy and sanitization

## Type Safety

Full mypy strict mode compliance. Run `mypy src/mcp_sql_server/` to verify.
