# MCP Server Tools Reference

## Available Tools

| Tool | Purpose | Parameters |
|------|---------|------------|
| `execute_query` | Run SELECT queries | `sql`, `params` (list), `limit` (1-10000, default 1000) |
| `execute_statement` | Run INSERT/UPDATE/DELETE | `sql`, `params` (list) |
| `execute_query_file` | Execute queries from query/ folder | `filename` |
| `list_tables` | List all tables | `schema` (optional) |
| `describe_table` | Get column definitions | `table_name`, `schema` (default: dbo) |
| `get_view_definition` | Get SQL source of views | `view_name`, `schema` (default: dbo) |
| `get_function_definition` | Get UDF source code | `function_name`, `schema` (default: dbo) |
| `list_procedures` | List stored procedures | `schema` (optional) |
| `execute_procedure` | Execute stored procedures | `proc_name`, `schema`, `params` (dict) |

## Resources

- `sqlserver://tables` - Formatted list of all tables with row counts
- `sqlserver://database/info` - Database metadata (version, collation, edition)
- `sqlserver://functions` - List of user-defined functions
- `sqlserver://pool/stats` - Connection pool statistics

## API Response Format

All tools return consistent response structures:

```python
# Query success
{
    "columns": ["col1", "col2", ...],
    "rows": [{"col1": value, "col2": value}, ...],
    "row_count": 150,
    "truncated": False  # True if results exceeded limit
}

# Statement success
{
    "affected_rows": 5,
    "success": True
}

# Error
{
    "error": "Error message here"
}
```

## Parameter Binding

Use `?` placeholders for positional parameters:

```python
# Positional parameters (list)
execute_query(
    sql="SELECT * FROM Users WHERE status = ? AND created > ?",
    params=["active", "2024-01-01"]
)

# Stored procedure parameters (dict)
execute_procedure(
    proc_name="GetUsersByDepartment",
    params={"@DeptId": 5, "@Status": "active"}
)
```

## Resource Limits

| Setting | Default | Maximum | Source |
|---------|---------|---------|--------|
| Query result rows | 1,000 | 10,000 | `execute_query` limit param |
| Query timeout | 120s | - | `config.py` QUERY_TIMEOUT |
| Connection timeout | 30s | - | `config.py` TIMEOUT |

## Connection Pooling

The MCP server uses thread-safe connection pooling for efficient database access:

**Pool Lifecycle:**
- Connections pre-created on first database access (up to `min_size`)
- Dynamic creation up to `max_size` when pool is exhausted
- Automatic health checks via `SELECT 1` before returning connections
- Three retirement conditions: age-based, idle-based, health-based

**Pool Statistics (via `sqlserver://pool/stats` resource):**
- `total_acquisitions` / `total_releases` - Connection usage tracking
- `in_use` / `available` - Current pool state
- `peak_usage` - Maximum concurrent connections used
- `health_checks` - Number of health checks performed
- `failed_acquisitions` - Timeout/failure count

## Metadata Caching

Frequently-accessed schema metadata is cached with 60-second TTL:
- `list_tables()` - Table listing
- `describe_table()` - Column definitions
- `list_procedures()` - Stored procedure listing

Cache is thread-safe and automatically invalidates expired entries on access.

## Observability

**Audit Logging (`audit.py`):**
- SQL query hashing for privacy-preserving logs
- Execution timing via context managers
- Query preview with sensitive data masking

**Error Handling (`errors.py`):**
- Automatic sanitization of error messages (hides IPs, credentials, paths)
- Custom exception hierarchy for specific error types
- Consistent error response format across all tools

**Structured Logging (`logging_config.py`):**
- JSON or text format output
- Request correlation IDs for tracing
- Configurable log levels via `LOG_LEVEL` env var

## Type Safety

The codebase uses strict type annotations validated by mypy:
- All function signatures have type hints
- Generic types properly parameterized (`dict[str, Any]`, `tuple[Any, ...]`)
- Full mypy strict mode compliance (0 errors)

Run type checking:
```bash
.venv/bin/python -m mypy src/mcp_sql_server/
```
