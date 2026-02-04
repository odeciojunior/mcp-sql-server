# SQL Playground MCP Server

A Python [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that enables AI assistants to interact with Microsoft SQL Server databases. Built with [FastMCP](https://github.com/jlowin/fastmcp), it provides tools for executing queries, exploring schemas, retrieving object definitions, and managing stored procedures -- with multi-database support, connection pooling, caching, audit logging, and security validation.

## Features

- **10 MCP Tools** for query execution, schema discovery, object inspection, and stored procedure management
- **5 MCP Resources** for browsing database metadata, functions, pool statistics, and configured connections
- **Multi-Database Support** via a `DatabaseRegistry` pattern -- connect to multiple SQL Server instances from a single server
- **Thread-Safe Connection Pooling** with health checks, idle/stale retirement, and configurable pool sizing
- **TTL-Based Metadata Caching** (60-second default) for schema discovery queries
- **SQL Security Validation** blocking DDL, DCL, admin commands, and system procedures
- **Audit Logging** with SQL hashing, execution timing, and sensitive data masking
- **Structured Logging** in JSON or text format with request correlation IDs
- **Error Sanitization** that redacts IPs, credentials, and connection details from error messages
- **Strict Type Safety** with full mypy strict mode compliance
- **262 Tests** at 85%+ code coverage

## Architecture Overview

```
                    +------------------+
                    |   MCP Client     |
                    |  (Claude, etc.)  |
                    +--------+---------+
                             | stdio
                    +--------v---------+
                    |    server.py     |
                    |  FastMCP Server  |
                    |  (tool & resource|
                    |   registration)  |
                    +--------+---------+
                             |
              +--------------+--------------+
              |                             |
    +---------v----------+       +----------v---------+
    |   tools/           |       |   resources/       |
    | - query_execution  |       | - database_info    |
    | - schema_discovery |       |   (tables, funcs,  |
    | - object_defs      |       |    pool stats,     |
    | - stored_procs     |       |    db info,        |
    | - registry_tools   |       |    databases)      |
    +--------+-----------+       +----------+----------+
             |                              |
             +----------- utils.py ---------+
                             |
                    +--------v---------+
                    |   registry.py    |
                    | DatabaseRegistry |
                    | (named DB mgmt)  |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   database.py    |
                    | DatabaseManager  |
                    | (cursor mgmt,   |
                    |  query/stmt exec)|
                    +--------+---------+
                             |
                    +--------v---------+
                    |     pool.py      |
                    |  ConnectionPool  |
                    | (thread-safe,    |
                    |  health checks)  |
                    +--------+---------+
                             |
                    +--------v---------+
                    |    pyodbc        |
                    |  (ODBC Driver)   |
                    +------------------+

Cross-Cutting Concerns:
  config.py ........... Pydantic models, .env loading, multi-DB config
  security.py ......... SQL validation, keyword blocking, identifier checks
  cache.py ............ TTL cache with @cached decorator
  audit.py ............ Query hashing, execution timing, audit events
  errors.py ........... Exception hierarchy, error sanitization
  logging_config.py ... Structured JSON/text logging, request correlation
```

### Module Responsibilities

| Module | Responsibility |
|--------|----------------|
| `server.py` | FastMCP server initialization, tool/resource registration, lifecycle management |
| `registry.py` | Named database management with lazy initialization and thread-safe access |
| `database.py` | Connection management, cursor context managers, query/statement execution |
| `pool.py` | Thread-safe connection pooling with `Queue`, health checks, stale/idle retirement |
| `config.py` | Pydantic `DatabaseConfig` and `PoolConfig` models, `.env` loading, multi-DB env parsing |
| `security.py` | SQL validation, blocked keyword detection, identifier sanitization, bracket quoting |
| `cache.py` | `TTLCache` class with thread-safe get/set, `@cached` decorator, global metadata cache |
| `audit.py` | `AuditLogger` for queries/statements/procedures, SQL hashing, `timed_operation()` context manager |
| `errors.py` | `MCPError` hierarchy (`ValidationError`, `ConnectionError`, `QueryError`, `TimeoutError`), error sanitization |
| `logging_config.py` | `StructuredFormatter` (JSON), `StandardFormatter` (text), request ID correlation via `ContextVar` |
| `utils.py` | Lazy import helpers to break circular dependencies between tools/resources and server |
| `tools/` | Tool implementations organized by domain (query, schema, objects, procedures, registry) |
| `resources/` | MCP resource handlers returning formatted markdown strings |

## Installation

### Prerequisites

- Python 3.10+
- An ODBC driver for SQL Server (e.g., [Microsoft ODBC Driver 17 or 18](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server))
- Access to a SQL Server instance

### Setup

```bash
cd mcp-server

# Create virtual environment
python3 -m venv .venv

# Install package with dev dependencies
.venv/bin/pip install -e ".[dev]"
```

### Claude Code CLI Integration

Register the MCP server with Claude Code:

```bash
claude mcp add --transport stdio mcp-sql-server -- \
  /path/to/mcp-sql-server/.venv/bin/python -m mcp_sql_server.server
```

## Configuration

Copy `.env.example` to `.env` at the repository root and configure your database connection.

### Database Connection

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `DB_HOST` | - | Yes | SQL Server hostname or IP |
| `DB_PORT` | `1433` | No | SQL Server port |
| `DB_USER` | - | Yes | Database username |
| `DB_PASSWORD` | - | Yes | Database password |
| `DB_NAME` | - | Yes | Database name |
| `DB_DRIVER` | `ODBC Driver 17 for SQL Server` | No | ODBC driver name |
| `DB_TIMEOUT` | `30` | No | Connection timeout (seconds) |
| `DB_QUERY_TIMEOUT` | `120` | No | Query timeout (seconds) |
| `DB_ENCRYPT` | `false` | No | Enable encrypted connection |
| `DB_TRUST_CERT` | `false` | No | Trust server certificate |

### Connection Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_POOL_MIN_SIZE` | `1` | Minimum connections pre-created |
| `DB_POOL_MAX_SIZE` | `5` | Maximum connections allowed |
| `DB_POOL_IDLE_TIMEOUT` | `300` | Idle connection retirement (seconds) |
| `DB_POOL_HEALTH_CHECK_INTERVAL` | `30` | Health check frequency (seconds) |
| `DB_POOL_ACQUIRE_TIMEOUT` | `10.0` | Timeout waiting for a connection (seconds) |
| `DB_POOL_MAX_LIFETIME` | `3600` | Maximum connection age before retirement (seconds) |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FORMAT` | `text` | Output format (`text` or `json`) |

### Query Files

| Variable | Default | Description |
|----------|---------|-------------|
| `QUERY_DIR` | `<repo-root>/query/` | Directory containing `.sql` files for `execute_query_file` |

### Example `.env`

```env
DB_HOST=sqlserver.example.com
DB_PORT=1433
DB_USER=myuser
DB_PASSWORD=mypassword
DB_NAME=MyDatabase
DB_DRIVER=ODBC Driver 18 for SQL Server
DB_ENCRYPT=true
DB_TRUST_CERT=true
DB_POOL_MIN_SIZE=2
DB_POOL_MAX_SIZE=10
LOG_LEVEL=INFO
LOG_FORMAT=text
```

## Multi-Database Support

The server supports connecting to multiple SQL Server databases simultaneously via the `DatabaseRegistry` pattern. Every tool accepts an optional `database` parameter (defaults to `"default"`).

### How It Works

1. The **default** database reads from standard `DB_*` environment variables.
2. Additional databases are declared in `DB_DATABASES` as a comma-separated list of aliases.
3. Each alias reads from **prefixed** environment variables: `DB_{ALIAS}_HOST`, `DB_{ALIAS}_USER`, etc.
4. Each database gets its own `DatabaseManager` and `ConnectionPool`, lazily initialized on first use.

### Configuration Pattern

```env
# Default database (always required)
DB_HOST=primary.example.com
DB_USER=user1
DB_PASSWORD=pass1
DB_NAME=PrimaryDB

# Declare additional databases
DB_DATABASES=analytics,archive

# Analytics database
DB_ANALYTICS_HOST=analytics.example.com
DB_ANALYTICS_USER=analyst
DB_ANALYTICS_PASSWORD=pass2
DB_ANALYTICS_NAME=AnalyticsDB
DB_ANALYTICS_POOL_MAX_SIZE=3

# Archive database
DB_ARCHIVE_HOST=archive.example.com
DB_ARCHIVE_USER=reader
DB_ARCHIVE_PASSWORD=pass3
DB_ARCHIVE_NAME=ArchiveDB
DB_ARCHIVE_TIMEOUT=60
```

### Usage in Tools

```python
# Query the default database
execute_query(sql="SELECT TOP 10 * FROM Users")

# Query a named database
execute_query(sql="SELECT TOP 10 * FROM Events", database="analytics")

# List all configured databases
list_databases()
# Returns: {"success": True, "databases": [{"name": "default", ...}, {"name": "analytics", ...}]}
```

### Alias Rules

- Must match `^[a-zA-Z][a-zA-Z0-9_]{0,63}$` (start with letter, max 64 chars)
- `"default"` is reserved and always present
- Each alias has independent pool configuration via `DB_{ALIAS}_POOL_*` variables

## Available Tools

### `execute_query`

Execute a read-only SELECT query against the database.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sql` | `str` | required | SQL query (must start with `SELECT` or `WITH`) |
| `params` | `list[str]` | `None` | Positional parameter values for `?` placeholders |
| `limit` | `int` | `1000` | Max rows to return (1--10,000) |
| `database` | `str` | `"default"` | Target database alias |

The query is automatically wrapped with `SELECT TOP (limit+1) * FROM (query) AS _limited_query` to enforce server-side limiting. If more than `limit` rows exist, `truncated` is set to `True`.

**Response:**
```json
{
  "success": true,
  "columns": ["id", "name"],
  "rows": [{"id": 1, "name": "Alice"}],
  "row_count": 1,
  "truncated": false
}
```

### `execute_statement`

Execute a data modification statement (INSERT, UPDATE, DELETE).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sql` | `str` | required | SQL statement (must start with `INSERT`, `UPDATE`, or `DELETE`) |
| `params` | `list[str]` | `None` | Positional parameter values for `?` placeholders |
| `database` | `str` | `"default"` | Target database alias |

**Response:**
```json
{
  "success": true,
  "affected_rows": 5
}
```

### `execute_query_file`

Execute a SQL query from a `.sql` file in the configured query directory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filename` | `str` | required | Filename (e.g., `"my_query.sql"` or `"my_query"`) |
| `database` | `str` | `"default"` | Target database alias |

Filename must match `^[a-zA-Z0-9_-]+\.sql$`. Path traversal is prevented.

### `list_tables`

List all tables in the database, optionally filtered by schema.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `schema` | `str` | `None` | Schema name to filter by |
| `database` | `str` | `"default"` | Target database alias |

**Response:**
```json
{
  "success": true,
  "tables": [{"schema": "dbo", "name": "Users", "type": "BASE TABLE"}],
  "count": 1
}
```

### `describe_table`

Get detailed column information for a table.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `table_name` | `str` | required | Table name |
| `schema` | `str` | `"dbo"` | Schema name |
| `database` | `str` | `"default"` | Target database alias |

**Response:**
```json
{
  "success": true,
  "table": "dbo.Users",
  "columns": [
    {"name": "id", "type": "int", "max_length": null, "precision": 10, "nullable": "NO", "default_value": null}
  ]
}
```

### `get_view_definition`

Get the SQL source code of a database view.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `view_name` | `str` | required | View name |
| `schema` | `str` | `"dbo"` | Schema name |
| `database` | `str` | `"default"` | Target database alias |

### `get_function_definition`

Get the SQL source code of a user-defined function.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `function_name` | `str` | required | Function name |
| `schema` | `str` | `"dbo"` | Schema name |
| `database` | `str` | `"default"` | Target database alias |

### `list_procedures`

List all stored procedures in the database.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `schema` | `str` | `None` | Schema name to filter by |
| `database` | `str` | `"default"` | Target database alias |

**Response:**
```json
{
  "success": true,
  "procedures": [{"schema": "dbo", "name": "usp_GetUsers", "created": "...", "modified": "..."}],
  "count": 1
}
```

### `execute_procedure`

Execute a stored procedure with optional named parameters.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `proc_name` | `str` | required | Procedure name |
| `schema` | `str` | `"dbo"` | Schema name |
| `params` | `dict[str, Any]` | `None` | Parameter name-value pairs |
| `database` | `str` | `"default"` | Target database alias |

System procedures (`xp_*`, `sp_*`) are blocked. Parameter names are validated as safe identifiers.

**Example:**
```python
execute_procedure(
    proc_name="GetUsersByDepartment",
    params={"DeptId": 5, "Status": "active"}
)
```

### `list_databases`

List all configured database connections (no parameters).

**Response:**
```json
{
  "success": true,
  "databases": [
    {"name": "default", "host": "server1", "port": 1433, "database": "MyDB"},
    {"name": "analytics", "host": "server2", "port": 1433, "database": "AnalyticsDB"}
  ],
  "count": 2
}
```

## Available Resources

Resources return formatted markdown strings for browsing database metadata.

| URI | Description |
|-----|-------------|
| `sqlserver://tables` | All tables grouped by schema with type indicators |
| `sqlserver://database/info` | Database version, edition, collation, and name |
| `sqlserver://functions` | All user-defined functions grouped by schema with return types |
| `sqlserver://pool/stats` | Connection pool metrics (acquisitions, health checks, peak usage, etc.) |
| `sqlserver://databases` | All configured database connections in a markdown table |

## Security

### SQL Validation

All queries and statements pass through security validation before execution.

**Blocked Keywords (DDL/DCL/Admin):**

| Category | Keywords |
|----------|----------|
| DDL | `DROP`, `TRUNCATE`, `ALTER`, `CREATE` |
| DCL | `GRANT`, `REVOKE` |
| Admin | `SHUTDOWN`, `BACKUP`, `RESTORE`, `DBCC`, `KILL` |
| External Access | `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `BULK` |

**Blocked Prefixes:** `xp_*`, `sp_*` (system stored procedures)

**Statement Type Enforcement:**
- `execute_query` only accepts `SELECT` and `WITH` as the first keyword
- `execute_statement` only accepts `INSERT`, `UPDATE`, and `DELETE`

### Identifier Validation

Table, column, schema, and parameter names are validated against `^[a-zA-Z_][a-zA-Z0-9_]*$` and bracket-quoted to prevent SQL injection.

### Query File Security

The `execute_query_file` tool validates filenames against `^[a-zA-Z0-9_-]+\.sql$` and uses `Path.relative_to()` to prevent path traversal attacks.

### Error Sanitization

Error messages are automatically scrubbed to remove:
- IP addresses
- Usernames and passwords
- Connection string details (SERVER, UID, PWD)

## Connection Pooling

The `ConnectionPool` class provides thread-safe connection management using Python's `queue.Queue`.

### Lifecycle

1. **Initialization:** Pre-creates `min_size` connections on first database access
2. **Acquisition:** Returns a healthy connection from the pool, or creates a new one (up to `max_size`)
3. **Health Check:** Executes `SELECT 1` before returning connections that exceed the `health_check_interval`
4. **Release:** Rolls back any pending transaction, checks staleness, and returns to pool
5. **Retirement:** Connections are closed when they exceed `max_lifetime`, `idle_timeout`, or fail health checks

### Pool Statistics

Available via the `sqlserver://pool/stats` resource:

| Metric | Description |
|--------|-------------|
| `total_connections` | Total connections currently created |
| `in_use` | Connections currently checked out |
| `available` | Connections waiting in the pool |
| `peak_usage` | Maximum concurrent connections observed |
| `total_acquisitions` | Cumulative connection checkouts |
| `total_releases` | Cumulative connection returns |
| `failed_acquisitions` | Timeouts or creation failures |
| `health_checks` | Total health checks performed |
| `transaction_resets` | Rollbacks performed on release |

## Caching

Schema metadata queries (`list_tables`, `describe_table`, `list_procedures`) are cached using a thread-safe `TTLCache` with a 60-second default TTL.

### Features

- Thread-safe get/set/invalidate/clear operations via `threading.Lock`
- Automatic expiration on access (lazy cleanup)
- `@cached` decorator for transparent function-level caching with key generation from arguments
- Global `invalidate_metadata_cache()` to clear all cached entries
- `cleanup_expired()` for bulk removal of stale entries
- `stats()` method returning total, valid, and expired entry counts

## Observability

### Audit Logging

The `AuditLogger` tracks all database operations:

- **Queries:** SQL hash, preview (first 100 chars), duration, row count, truncation status, target database
- **Statements:** SQL hash, statement type (INSERT/UPDATE/DELETE), duration, affected rows, target database
- **Procedures:** Procedure name, schema, duration, row count, target database
- **Validation Failures:** SQL hash, short preview, blocked keyword, target database

SQL content is hashed with SHA-256 (first 16 chars) for privacy-preserving audit trails.

### Structured Logging

Two output formats controlled by `LOG_FORMAT`:

- **`text`** (default): `2024-01-15 10:30:00 - module.name - INFO - message`
- **`json`**: `{"timestamp": "...", "level": "INFO", "logger": "...", "message": "...", "request_id": "..."}`

Request correlation IDs are tracked via `contextvars.ContextVar` for tracing operations across components.

### Error Handling

Custom exception hierarchy with consistent response format:

```
MCPError
  +-- ValidationError   (blocked keyword, invalid SQL)
  +-- ConnectionError   (database unreachable)
  +-- QueryError        (execution failure)
  +-- TimeoutError      (operation timed out)
```

All exceptions produce sanitized error responses:
```json
{
  "success": false,
  "error": "Simplified message",
  "error_context": "query"
}
```

Common SQL Server errors are automatically simplified (e.g., `"Invalid object name 'Foo'"` becomes `"Object not found: Foo"`).

## Testing

The test suite contains 262 tests covering all modules.

```bash
# Run all tests
.venv/bin/pytest tests/ -v

# Run with coverage report
.venv/bin/pytest tests/ -v --cov=mcp_sql_server --cov-report=term-missing

# Run a specific test file
.venv/bin/pytest tests/test_server.py -v

# Run tests matching a pattern
.venv/bin/pytest tests/ -v -k "test_execute_query"
```

### Test Files

| File | Tests | Description |
|------|-------|-------------|
| `test_server.py` | Tool and resource registration, end-to-end tool behavior |
| `test_database.py` | `DatabaseManager` query/statement execution, cursor management |
| `test_pool.py` | Connection pool lifecycle, health checks, retirement, metrics |
| `test_config.py` | Configuration loading, validation, connection string generation |
| `test_config_multi.py` | Multi-database config, alias parsing, prefixed env vars |
| `test_registry.py` | `DatabaseRegistry` lazy init, get/close, `from_env()` |
| `test_security.py` | SQL validation, blocked keywords, identifier checks |
| `test_cache.py` | TTL cache operations, expiry, `@cached` decorator, thread safety |
| `test_inject_top_clause.py` | TOP clause injection for server-side limiting |
| `test_query_dir.py` | Query directory resolution and file loading |
| `conftest.py` | Shared pytest fixtures (mock database, config objects) |

## Type Safety

The codebase uses strict type annotations validated with mypy in strict mode.

```bash
# Run type checker
.venv/bin/python -m mypy src/mcp_sql_server/
```

Mypy configuration (from `pyproject.toml`):
- `disallow_untyped_defs` and `disallow_incomplete_defs`
- `disallow_any_generics` (all generics must be parameterized)
- `warn_return_any`, `warn_unused_configs`, `warn_redundant_casts`
- `strict_equality` and `strict_concatenate`
- `no_implicit_reexport`

## Project Structure

```
mcp-server/
+-- pyproject.toml                         # Package metadata, dependencies, mypy config
+-- src/
|   +-- mcp_sql_server/
|       +-- __init__.py                    # Package version
|       +-- server.py                      # FastMCP server, tool/resource registration
|       +-- database.py                    # DatabaseManager with pooled/non-pooled modes
|       +-- pool.py                        # Thread-safe ConnectionPool
|       +-- config.py                      # Pydantic configs, .env loading, multi-DB support
|       +-- registry.py                    # DatabaseRegistry for named database management
|       +-- security.py                    # SQL validation, keyword blocking
|       +-- cache.py                       # TTLCache and @cached decorator
|       +-- audit.py                       # AuditLogger, SQL hashing, timed_operation
|       +-- errors.py                      # Exception hierarchy, error sanitization
|       +-- logging_config.py              # Structured/standard formatters, request IDs
|       +-- utils.py                       # Lazy import helpers (circular dep avoidance)
|       +-- tools/
|       |   +-- __init__.py                # Tool exports (ALL_TOOLS)
|       |   +-- query_execution.py         # execute_query, execute_statement, execute_query_file
|       |   +-- schema_discovery.py        # list_tables, describe_table
|       |   +-- object_definitions.py      # get_view_definition, get_function_definition
|       |   +-- stored_procedures.py       # list_procedures, execute_procedure
|       |   +-- registry_tools.py          # list_databases
|       +-- resources/
|           +-- __init__.py                # Resource exports (ALL_RESOURCES)
|           +-- database_info.py           # tables, db info, functions, pool stats, databases
+-- tests/
    +-- conftest.py                        # Shared fixtures
    +-- test_server.py                     # Server integration tests
    +-- test_database.py                   # DatabaseManager tests
    +-- test_pool.py                       # ConnectionPool tests
    +-- test_config.py                     # Config loading tests
    +-- test_config_multi.py               # Multi-database config tests
    +-- test_registry.py                   # DatabaseRegistry tests
    +-- test_security.py                   # Security validation tests
    +-- test_cache.py                      # TTLCache tests
    +-- test_inject_top_clause.py          # TOP clause injection tests
    +-- test_query_dir.py                  # Query directory tests
```

## Development Commands

```bash
# Install with dev dependencies
cd mcp-server && .venv/bin/pip install -e ".[dev]"

# Run the MCP server (stdio transport)
.venv/bin/python -m mcp_sql_server.server

# Run tests
.venv/bin/pytest tests/ -v

# Run tests with coverage
.venv/bin/pytest tests/ -v --cov=mcp_sql_server --cov-report=term-missing

# Type check
.venv/bin/python -m mypy src/mcp_sql_server/

# Run via entry point (after install)
.venv/bin/mcp-sql-server
```

## Dependencies

### Runtime

| Package | Version | Purpose |
|---------|---------|---------|
| `mcp` | >=1.2.0 | Model Context Protocol SDK (FastMCP) |
| `pyodbc` | >=5.0.0 | ODBC database connectivity |
| `python-dotenv` | >=1.0.0 | Environment variable loading from `.env` |
| `pydantic` | >=2.0.0 | Configuration validation and modeling |

### Development

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=7.0.0 | Test framework |
| `pytest-asyncio` | >=0.21.0 | Async test support |
| `pytest-cov` | >=4.0.0 | Coverage reporting |
| `mypy` | >=1.0.0 | Static type checking |

## License

MIT
