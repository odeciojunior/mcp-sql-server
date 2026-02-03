# CLAUDE.md

SQL query development workspace for infrastructure request analysis. Contains SQL Server views and queries for NOC incident tracking with ticket linking.

## Quick Reference

```bash
# Setup
cd mcp-server && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Test
.venv/bin/pytest tests/ -v

# Type check
.venv/bin/python -m mypy src/sql_playground_mcp/

# Run server
.venv/bin/python -m sql_playground_mcp.server
```

## Project Structure

```
sql-playground/
├── .claude/
│   ├── agents/           # Custom Claude Code agents (10 specialists)
│   └── rules/            # Modular reference documentation
├── BdeskSumiCity/        # Database object documentation (views, tables, functions)
├── docs/                 # Documentation and troubleshooting guides
├── mcp-server/           # Python MCP server for SQL Server interaction
│   ├── src/sql_playground_mcp/
│   │   ├── server.py     # FastMCP server, tool/resource registration
│   │   ├── database.py   # DatabaseManager, pooled/non-pooled modes
│   │   ├── pool.py       # Thread-safe connection pooling
│   │   ├── config.py     # Pydantic configuration from .env
│   │   ├── security.py   # SQL validation, keyword blocking
│   │   └── tools/        # Tool implementations
│   └── tests/            # 262 tests, 85% coverage
└── CLAUDE.md             # This file
```

## MCP Server

Python MCP server enabling Claude to interact with SQL Server databases.

**Tools:** `execute_query`, `execute_statement`, `execute_query_file`, `list_tables`, `describe_table`, `get_view_definition`, `get_function_definition`, `list_procedures`, `execute_procedure`

**Resources:** `sqlserver://tables`, `sqlserver://database/info`, `sqlserver://functions`, `sqlserver://pool/stats`

See @.claude/rules/mcp-tools.md for detailed tool documentation.

### Claude Code CLI Integration

```bash
claude mcp add --transport stdio sql-playground -- /home/odecio/projects/sql-playground/mcp-server/.venv/bin/python -m sql_playground_mcp.server
```

## Database Configuration

**Target:** Microsoft SQL Server 2017 Enterprise Edition on Windows Server 2019

Copy `.env.example` to `.env` and configure:

### Required Settings

| Variable | Description |
|----------|-------------|
| `DB_HOST` | SQL Server host |
| `DB_USER` | Database username |
| `DB_PASSWORD` | Database password |
| `DB_NAME` | Database name |

### Optional Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PORT` | 1433 | SQL Server port |
| `DB_DRIVER` | ODBC Driver 18 | ODBC driver name |
| `DB_TIMEOUT` | 30 | Connection timeout (seconds) |
| `DB_QUERY_TIMEOUT` | 120 | Query timeout (seconds) |
| `DB_POOL_MIN_SIZE` | 1 | Minimum pool connections |
| `DB_POOL_MAX_SIZE` | 5 | Maximum pool connections |

## Key Database Objects

**Views:** `dbo.vwReq` (main request view, alias `r`), `dbo.vwRepTime` (SLA/freeze time tracking)

**Tables:** `Req`/`ReqConcluida` (active/concluded), `ReqHistorico`/`ReqHistConcluida` (history), `Formulario` (forms), `Tarefa` (tasks), `ReqVinculadaRef` (ticket linking)

**User-Defined Functions:**
- `dbo.udfDadoByIdDad(idReq, fieldId)` - Dynamic field values by field ID
- `dbo.udfPartRep(idReq, role)` - Participant by role ('REG', 'ANTE', 'RESP')
- `dbo.udfRepDad(type, idReq, fieldId, formId, flag)` - Report field data
- `dbo.udfSLA(minutes, format)` - Format SLA time values

**Performance Note:** Scalar UDFs execute per row. Consider materialized tables for high-frequency queries.

## Security

### Blocked SQL Keywords

| Category | Keywords |
|----------|----------|
| DDL | `DROP`, `TRUNCATE`, `ALTER`, `CREATE` |
| DCL | `GRANT`, `REVOKE` |
| Admin | `SHUTDOWN`, `BACKUP`, `RESTORE`, `DBCC`, `KILL` |
| External Access | `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `BULK` |
| System Procedures | `xp_*`, `sp_*` (prefix patterns) |

### Query File Security

`execute_query_file` validates filename pattern `^[a-zA-Z0-9_-]+\.sql$` and prevents path traversal.

## BdeskSumiCity Database Objects

Comprehensive database object documentation in `BdeskSumiCity/`:

```
BdeskSumiCity/
├── INDEX.md          # Complete object index and cross-references
├── custom_views/     # 56 vwCUSTOM_* view definitions
├── views/            # 6 external views (vwReq, vwRepTime, etc.)
├── tables/           # 20 table definitions
└── functions/        # 11 UDF definitions
```

See @BdeskSumiCity/INDEX.md for complete object catalog and dependency matrix.

## Custom Agents

Ten specialized agents in `.claude/agents/` for delegation:

| Agent | Model | When to Use | Example Prompt |
|-------|-------|-------------|----------------|
| `sql-performance-monitor` | Opus | CPU high, queries slow, database health check | "Analyze why database is slow" |
| `sql-schema-discovery` | Opus | Understanding relationships, finding dependencies | "What tables relate to vwReq?" |
| `sql-server-performance-tuner` | Opus | Optimize specific query, analyze execution plan | "Why is this query taking 30 seconds?" |
| `tsql-specialist` | Opus | Write complex queries, stored procedures | "Write a query with window functions" |
| `lesson-retriever` | Haiku | Find documented solutions, check gotchas | "Any known issues with CTEs?" |
| `session-lessons-documenter` | Opus | Capture learnings after solving problems | "Document this solution" |
| `code-reviewer` | Opus | Review code changes for quality and regressions | "Review the changes I just made" |
| `report-analyzer` | Opus | Analyze reports, create strategic roadmaps | "Analyze this report and create a roadmap" |
| `deep-research-analyst` | Opus | Multi-source internet research and synthesis | "Research best practices for X" |
| `set-baseline-metrics` | Haiku | Capture baseline performance metrics before optimizations | "Capture baseline for spCalculaSLA optimization" |

**Delegation tip**: For complex tasks, specify the agent explicitly: "Use sql-schema-discovery to find all foreign keys for the Req table"

**Tool-restricted agents**: `report-analyzer` and `deep-research-analyst` have restricted tool access (no Edit/Bash) and use `permissionMode: acceptEdits` for safe automation.

## Working with This Project

### Query Development Workflow
1. Explore schema: Use `sql-schema-discovery` agent or `list_tables`/`describe_table` MCP tools
2. Write query: Reference @.claude/rules/sql-conventions.md for patterns
3. Test with limit: Always use `execute_query` with `limit=100` first
4. Optimize if needed: Use `sql-server-performance-tuner` agent

### When to Use Agents vs Direct Tools
- **Simple queries**: Use MCP tools directly (`execute_query`, `describe_table`)
- **Complex analysis**: Delegate to specialized agents
- **Performance issues**: Always use `sql-performance-monitor` or `sql-server-performance-tuner`

## Common Gotchas

1. **CTEs are NOT materialized** - SQL Server re-executes on every reference
2. **Unicode literals required** - Use `N'Nao'` for Portuguese text
3. **FORMAT() is slow** - Use `CONVERT()` instead (10-50x faster)
4. **Indexed views limitations** - No LEFT JOIN, MAX(), UNION ALL, CTEs

See @.claude/rules/sql-conventions.md for full optimization guidance.

## References

| Document | Use When |
|----------|----------|
| @.claude/rules/field-reference.md | Working with UDF field IDs |
| @.claude/rules/sql-conventions.md | Writing or optimizing queries |
| @.claude/rules/mcp-tools.md | Using MCP database tools |
| @BdeskSumiCity/INDEX.md | Exploring database objects |
| @docs/troubleshooting.md | Debugging connection/query issues |

## Headless Usage

For automation or scripting:
```bash
# Run specific analysis
claude -p "Analyze database performance" --allowedTools execute_query

# Generate documentation
claude -p "Document the vwCUSTOM_IncidenteNacional view"
```
