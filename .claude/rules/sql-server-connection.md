# SQL Server Connection Setup

## Connection Param Detection

At the start of any session in this project, check whether SQL Server connection parameters are configured by verifying the presence of either:

- `SQL_SERVER_HOST` env var (preferred — set via `.claude/settings.local.json`)
- `DB_HOST` env var (fallback — set via `.env` file)

If **neither** is set, immediately show the user the setup instructions below before attempting any database operation.

---

## Setup Instructions (show when connection not configured)

### Option A — Project-level (recommended for Claude Code users)

Create `.claude/settings.local.json` in the project root:

```json
{
  "env": {
    "SQL_SERVER_HOST": "localhost",
    "SQL_SERVER_PORT": "1433",
    "SQL_SERVER_USER": "your_user",
    "SQL_SERVER_PASSWORD": "your_password",
    "SQL_SERVER_DATABASE": "your_database",
    "SQL_SERVER_DRIVER": "ODBC Driver 18 for SQL Server",
    "SQL_SERVER_ENCRYPT": "false",
    "SQL_SERVER_TRUST_CERT": "true"
  }
}
```

> **Note:** `.claude/settings.local.json` is gitignored by default — credentials stay local.

### Option B — `.env` file (classic approach)

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Required fields:

```env
DB_HOST=localhost
DB_PORT=1433
DB_USER=your_user
DB_PASSWORD=your_password
DB_NAME=your_database
DB_DRIVER=ODBC Driver 18 for SQL Server
DB_ENCRYPT=false
DB_TRUST_CERT=true
```

---

## Env Var Reference

**Precedence** (highest first) for the default connection:

1. `DB_*` set in the process environment — explicit configuration, e.g. an MCP
   client injecting them via `.mcp.json`
2. `SQL_SERVER_*` — injected via `.claude/settings.local.json`
3. `DB_*` read from the `.env` file

Named databases (`DB_{ALIAS}_*`) do not consult `SQL_SERVER_*` at all.


| `SQL_SERVER_*` (settings.local.json) | `DB_*` (.env) | Required | Default |
|--------------------------------------|---------------|----------|---------|
| `SQL_SERVER_HOST` | `DB_HOST` | ✅ | — |
| `SQL_SERVER_PORT` | `DB_PORT` | — | `1433` |
| `SQL_SERVER_USER` | `DB_USER` | ✅ | — |
| `SQL_SERVER_PASSWORD` | `DB_PASSWORD` | ✅ | — |
| `SQL_SERVER_DATABASE` | `DB_NAME` | ✅ | — |
| `SQL_SERVER_DRIVER` | `DB_DRIVER` | — | `ODBC Driver 17 for SQL Server` |
| `SQL_SERVER_ENCRYPT` | `DB_ENCRYPT` | — | `false` |
| `SQL_SERVER_TRUST_CERT` | `DB_TRUST_CERT` | — | `false` |

`SQL_SERVER_*` vars take priority over `DB_*` vars when both are present.

---

## ODBC Driver Check

If the user has connection errors mentioning "driver not found", help them verify installed drivers:

```bash
odbcinst -q -d
```

Common driver names:
- `ODBC Driver 18 for SQL Server` (latest)
- `ODBC Driver 17 for SQL Server`
- `ODBC Driver 13 for SQL Server`

Install on Ubuntu/Debian:

```bash
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
```

---

## Multi-Database Setup

To connect to multiple databases simultaneously, add to `.env`:

```env
DB_DATABASES=analytics,reporting

DB_ANALYTICS_HOST=host2
DB_ANALYTICS_USER=user2
DB_ANALYTICS_PASSWORD=pass2
DB_ANALYTICS_NAME=AnalyticsDB

DB_REPORTING_HOST=host3
DB_REPORTING_USER=user3
DB_REPORTING_PASSWORD=pass3
DB_REPORTING_NAME=ReportingDB
```

Each alias gets its own connection pool and is accessible via the `database` parameter on all tools.
