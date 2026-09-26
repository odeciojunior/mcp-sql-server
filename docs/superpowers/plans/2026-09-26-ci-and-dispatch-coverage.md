# CI and Dispatch Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CI that catches upstream dependency breakage before users do, and cover the SDK dispatch layer and connection-pool cleanup paths that no test currently exercises.

**Architecture:** Two GitHub Actions workflows — `ci.yml` gates pushes and PRs across the declared Python floor and current release; `drift.yml` resolves dependencies fresh on a weekly schedule and additionally probes deliberately *above* the declared version ceilings, which is the only step that can see the class of failure that caused the outage. Two new test surfaces run in-process against the real `MCPServer` object and the real `ConnectionPool`, with no subprocess and no database.

**Tech Stack:** Python 3.10–3.14, pytest 9 + pytest-asyncio 1.4 (strict mode), mypy strict, mcp 2.2.0 (`MCPServer`), pyodbc (mocked), GitHub Actions, Dependabot.

**Spec:** `docs/superpowers/specs/2026-09-26-ci-and-dispatch-coverage-design.md`

## Global Constraints

- `requires-python = ">=3.10"` — CI matrix must include `"3.10"` and `"3.14"`.
- Dependency ceilings are declared and must not be widened: `mcp>=2.0,<3`, `pydantic>=2.12,<3`.
- **No live database.** Every test mocks the DB layer. Nothing may open a real connection.
- **Never read the repository's real `.env`** — it points at production. The autouse `_isolate_from_real_env` fixture (`tests/conftest.py:11-33`) handles this; do not bypass or disable it.
- Workflows declare `permissions: contents: read`; actions are pinned to full commit SHAs, never floating tags.
- No `src/` changes. `pyproject.toml` gains pytest configuration only.
- Characterization tests encode *current* behaviour. If one fails on first run, that is a bug found — stop and report it, do not adjust the test to match the code.

## Review Focus

Five failure modes the spec implies that no task's happy path would exercise. Each has a test assigned to the task owning that code.

1. **A tool called with a wrong-typed argument** (`limit="abc"`) — schema validation should reject it at dispatch rather than reaching the handler. → Task 1, Step 9.
2. **A tool called with a required argument missing** (`execute_query` with no `sql`) — same path, different trigger. → Task 1, Step 9.
3. **An unknown resource URI** — `read_resource` on a URI nothing registered. → Task 1, Step 11.
4. **`release()` of a connection whose session reset fails** — the `invalid` flag path sits beside the branches being covered and leaks a connection if wrong. → Task 2, Step 7.
5. **`acquire()` against a closed pool** — the counterpart to `release()` into a closed pool, which Task 2 already covers. → Task 2, Step 7.

---

### Task 1: Dispatch tests for tools and resources

Covers spec §3. Closes the 15 uncovered wrapper bodies in `server.py` (10 `@mcp.tool`, 5 `@mcp.resource`).

**Files:**
- Create: `tests/test_tool_dispatch.py`
- Modify: `pyproject.toml` (add `[tool.pytest.ini_options]`)

**Interfaces:**
- Consumes: `tests/conftest.py:197` `mock_db_manager` fixture; `tests/conftest.py:11` autouse `_isolate_from_real_env`.
- Produces: nothing other tasks depend on.

**Critical hazard — read before writing any code.** `src/mcp_sql_server/utils.py:26-30` caches the getter:

```python
global _db_getter
if _db_getter is None:
    from .server import get_db as server_get_db
    _db_getter = server_get_db
return _db_getter(database)
```

`_db_getter` holds a direct reference to the original function object. Once any earlier test triggers it, `patch("mcp_sql_server.server.get_db")` — what conftest's `mock_get_db` does — is **silently bypassed**, because the module attribute is replaced but `_db_getter` is not. Nothing in the suite resets it. These tests must patch `mcp_sql_server.utils._db_getter` directly and restore it, which is what the local fixture below does. Do not use `mock_get_db` here.

- [ ] **Step 1: Write the dispatch test module with one tool test**

Create `tests/test_tool_dispatch.py`:

```python
"""Tool and resource dispatch tests through the real MCPServer object.

These call mcp.call_tool / mcp.read_resource in-process, so they exercise
the decorator, JSON-schema coercion and the anyio.to_thread worker dispatch
that SDK 2.x uses for synchronous handlers. tests/test_server.py calls the
underlying functions directly and bypasses all of that.

Patching note: mcp_sql_server.utils caches the db getter in a module global
on first use, so patching server.get_db after that is a no-op. These tests
patch utils._db_getter directly. See the plan for detail.
"""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_sql_server import server
from mcp_sql_server import utils as utils_module


@pytest.fixture
def dispatch_db(mock_db_manager: Any, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Route every utils.get_db(...) call to the mocked DatabaseManager.

    Patches the cached getter itself, not server.get_db, because
    utils._db_getter holds a direct function reference once populated.
    """
    calls = MagicMock()

    def _getter(database: str = "default") -> Any:
        calls(database)
        return mock_db_manager

    monkeypatch.setattr(utils_module, "_db_getter", _getter)
    monkeypatch.setattr(server, "_registry", None)
    return calls


def _payload(result: Any) -> dict[str, Any]:
    """Extract the JSON body from a CallToolResult."""
    assert result.content, "tool returned no content"
    return json.loads(result.content[0].text)


async def test_execute_query_dispatches(dispatch_db):
    result = await server.mcp.call_tool("execute_query", {"sql": "SELECT 1"})
    assert result.is_error is False
    assert _payload(result)["success"] is True
```

- [ ] **Step 2: Run it and watch it NOT run**

Run: `.venv/bin/pytest tests/test_tool_dispatch.py -v`

Expected: the test is **skipped or errored**, not passed. The suite has no pytest configuration and `pytest-asyncio` 1.4 defaults to strict mode, so an unmarked `async def test` is not executed. This is the blocker the spec calls out; confirm it before fixing it.

- [ ] **Step 3: Add pytest-asyncio configuration**

Append to `pyproject.toml`, after the `[project.scripts]` block:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 4: Run again and watch it pass**

Run: `.venv/bin/pytest tests/test_tool_dispatch.py -v`
Expected: `1 passed`.

- [ ] **Step 5: Confirm the configuration changed nothing else**

Run: `.venv/bin/pytest tests/ -q`
Expected: `636 passed` (635 existing + 1 new). If any previously-passing test now fails, `asyncio_mode = "auto"` has affected it — stop and report rather than proceeding.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_tool_dispatch.py
git commit -m "test: dispatch tool calls through the real MCPServer

Adds pytest-asyncio auto mode, without which async tests silently skip."
```

- [ ] **Step 7: Add the remaining nine tools**

Append to `tests/test_tool_dispatch.py`. Arguments are the measured required ones:

```python
TOOL_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("execute_query", {"sql": "SELECT 1"}),
    ("execute_statement", {"sql": "UPDATE t SET c = 1"}),
    ("execute_query_file", {"filename": "example.sql"}),
    ("list_tables", {}),
    ("describe_table", {"table_name": "Users"}),
    ("get_view_definition", {"view_name": "vwUsers"}),
    ("get_function_definition", {"function_name": "fnUsers"}),
    ("list_procedures", {}),
    ("execute_procedure", {"proc_name": "spUsers"}),
    ("list_databases", {}),
]


@pytest.mark.parametrize("name,args", TOOL_CALLS, ids=[n for n, _ in TOOL_CALLS])
async def test_every_tool_dispatches(dispatch_db, name, args):
    """Each tool returns a well-formed CallToolResult through dispatch."""
    result = await server.mcp.call_tool(name, args)
    assert result.is_error is False, f"{name} reported is_error"
    body = _payload(result)
    assert "success" in body, f"{name} returned no success key: {body}"
```

- [ ] **Step 8: Run the parametrised tools**

Run: `.venv/bin/pytest tests/test_tool_dispatch.py -v`
Expected: 11 passed (1 original + 10 parametrised).

A tool that fails here has genuinely never been dispatched before. If `execute_query_file` fails on a missing file, give it a real path via the `query_dir` fixture in `tests/conftest.py` rather than deleting the case.

- [ ] **Step 9: Add argument-validation tests (Review Focus 1 and 2)**

```python
async def test_wrong_typed_argument_is_rejected(dispatch_db):
    """A non-integer limit is rejected by schema validation, not the handler."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        await server.mcp.call_tool("execute_query", {"sql": "SELECT 1", "limit": "abc"})


async def test_missing_required_argument_is_rejected(dispatch_db):
    """execute_query without sql never reaches the handler."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        await server.mcp.call_tool("execute_query", {})


async def test_unknown_tool_raises(dispatch_db):
    """An unknown tool name is the one case that surfaces through dispatch."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        await server.mcp.call_tool("no_such_tool", {})
```

- [ ] **Step 10: Add alias-routing and unknown-alias tests**

```python
async def test_database_argument_routes_to_alias(dispatch_db):
    """database="analytics" reaches get_db as that alias, not "default"."""
    await server.mcp.call_tool("list_tables", {"database": "analytics"})
    dispatch_db.assert_called_with("analytics")


async def test_unknown_alias_is_not_a_dispatch_error(monkeypatch):
    """An unknown alias returns a normal result whose body says success: false.

    Measured behaviour: the handler catches it, so is_error stays False. The
    intuitive assertion (that an error surfaces through dispatch) is wrong.
    """
    def _raise(database: str = "default") -> Any:
        raise KeyError(f"Unknown database '{database}'")

    monkeypatch.setattr(utils_module, "_db_getter", _raise)
    monkeypatch.setattr(server, "_registry", None)

    result = await server.mcp.call_tool("list_tables", {"database": "nope"})
    assert result.is_error is False
    assert _payload(result)["success"] is False
```

- [ ] **Step 11: Add the five resource dispatch tests (Review Focus 3)**

`read_resource` returns a list of `ReadResourceContents`, each with `.content` and `.mime_type` — measured, not assumed:

```python
RESOURCE_URIS = [
    "sqlserver://tables",
    "sqlserver://database/info",
    "sqlserver://functions",
    "sqlserver://pool/stats",
    "sqlserver://databases",
]


@pytest.mark.parametrize("uri", RESOURCE_URIS)
async def test_every_resource_dispatches(dispatch_db, uri):
    """Each resource returns readable content through dispatch."""
    contents = list(await server.mcp.read_resource(uri))
    assert contents, f"{uri} returned nothing"
    assert isinstance(contents[0].content, str)


async def test_unknown_resource_uri_raises(dispatch_db):
    """An unregistered URI is rejected rather than returning empty content."""
    from mcp.server.mcpserver.exceptions import ResourceNotFoundError

    with pytest.raises(ResourceNotFoundError):
        await server.mcp.read_resource("sqlserver://not-a-resource")
```

The exception type was measured against the installed mcp 2.2.0: `ResourceNotFoundError("Unknown resource: …")`.

- [ ] **Step 12: Run the full dispatch module**

Run: `.venv/bin/pytest tests/test_tool_dispatch.py -v`
Expected: all pass (≈20 tests).

- [ ] **Step 13: Confirm the uncovered wrapper bodies are now covered**

Run: `.venv/bin/pytest tests/ -q --cov=mcp_sql_server --cov-report=term-missing | grep 'server.py'`

Expected: `server.py` coverage rises from 81%, and lines `118, 139, 158, 177, 198, 219, 240, 259, 286, 298, 305, 311, 317, 323, 329` no longer appear in the missing list. Lines `51-56` (lifespan shutdown) and `365-367` (the `__main__` guard) legitimately remain.

- [ ] **Step 14: Run the whole suite and mypy**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/python -m mypy src/mcp_sql_server/`
Expected: all tests pass; `Success: no issues found in 21 source files`.

- [ ] **Step 15: Commit**

```bash
git add tests/test_tool_dispatch.py
git commit -m "test: cover all 10 tools and 5 resources through SDK dispatch

Only list_databases was dispatch-tested before, via test_entrypoint.py,
and only because it never opens a connection."
```

---

### Task 2: Pool cleanup characterization tests

Covers spec §4. **Not TDD** — these encode current behaviour on untested branches. A failure is a bug found; stop and report it.

**Files:**
- Modify: `tests/test_pool.py` (append a new test class)

**Interfaces:**
- Consumes: `pool_config` and `db_config` fixtures already defined at `tests/test_pool.py:15-37`.
- Produces: nothing other tasks depend on.

Relevant API, from `src/mcp_sql_server/pool.py:18-52`: `PooledConnection` is a dataclass with `connection`, `created_at`, `last_used_at`, `last_health_check`, `use_count`, `invalid`. `is_stale(max_lifetime)` and `is_idle(idle_timeout)` both compare against `time.time()` and return `False` when the limit is `<= 0`. Backdating a timestamp is how you trigger them.

- [ ] **Step 1: Write the idle-retirement test (`pool.py:205-207`)**

Append to `tests/test_pool.py`:

```python
class TestPoolCleanupBranches:
    """Cleanup paths in acquire() and release() that nothing else exercises."""

    def test_acquire_retires_idle_connection(self, db_config, pool_config):
        """A connection idle past idle_timeout is closed, not handed out."""
        import time

        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            pool = ConnectionPool(db_config, PoolConfig(min_size=1, max_size=2, idle_timeout=1))
            stale = pool.acquire()
            stale.last_used_at = time.time() - 3600
            pool.release(stale)

            fresh = pool.acquire()
            assert fresh is not stale, "idle connection was handed out instead of retired"
            pool.release(fresh)
            pool.close()
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/test_pool.py::TestPoolCleanupBranches -v`
Expected: PASS. A failure means idle retirement is broken — stop and report.

- [ ] **Step 3: Write the three release() branch tests (`257-258`, `266-267`, `276-278`)**

```python
    def test_release_into_closed_pool_closes_connection(self, db_config, pool_config):
        """Releasing after close() closes the connection instead of pooling it."""
        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            pool = ConnectionPool(db_config, pool_config)
            conn = pool.acquire()
            pool.close()
            pool.release(conn)
            conn.connection.close.assert_called()

    def test_release_retires_stale_connection(self, db_config):
        """A connection past max_lifetime is closed on release, not pooled."""
        import time

        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            pool = ConnectionPool(db_config, PoolConfig(min_size=1, max_size=2, max_lifetime=1))
            conn = pool.acquire()
            conn.created_at = time.time() - 3600
            pool.release(conn)
            conn.connection.close.assert_called()
            pool.close()

    def test_release_into_full_pool_closes_connection(self, db_config):
        """queue.Full on release closes the surplus connection rather than leaking it."""
        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            pool = ConnectionPool(db_config, PoolConfig(min_size=1, max_size=1))
            conn = pool.acquire()
            # Fill the queue behind its back so put_nowait raises.
            pool._pool.put_nowait(pool._create_connection())
            pool.release(conn)
            conn.connection.close.assert_called()
            pool.close()
```

- [ ] **Step 4: Run them**

Run: `.venv/bin/pytest tests/test_pool.py::TestPoolCleanupBranches -v`
Expected: 4 passed.

- [ ] **Step 5: Confirm the target lines are covered**

Run: `.venv/bin/pytest tests/ -q --cov=mcp_sql_server --cov-report=term-missing | grep 'pool.py'`

Expected: `205-207`, `257-258`, `266-267` and `276-278` no longer appear in the missing list. `103` and `146-147` may remain; they are not cleanup paths and are out of scope.

- [ ] **Step 6: Commit**

```bash
git add tests/test_pool.py
git commit -m "test: cover pool cleanup branches in acquire and release

Characterization tests for idle retirement, release into a closed pool,
stale retirement on release, and queue.Full — the paths where a leaked or
double-closed connection would hide."
```

- [ ] **Step 7: Add the two Review Focus tests (4 and 5)**

```python
    def test_release_of_invalid_connection_closes_it(self, db_config, pool_config):
        """A connection whose session reset failed is closed, never reused."""
        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            pool = ConnectionPool(db_config, pool_config)
            conn = pool.acquire()
            conn.invalid = True
            pool.release(conn)
            conn.connection.close.assert_called()
            pool.close()

    def test_acquire_from_closed_pool_raises(self, db_config, pool_config):
        """Acquiring after close() fails loudly rather than returning a dead handle."""
        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            pool = ConnectionPool(db_config, pool_config)
            pool.close()
            with pytest.raises(RuntimeError, match="Pool is closed"):
                pool.acquire()
```

Measured: `RuntimeError("Pool is closed")`.

- [ ] **Step 8: Run, then run the whole suite**

Run: `.venv/bin/pytest tests/test_pool.py::TestPoolCleanupBranches -v && .venv/bin/pytest tests/ -q`
Expected: 6 passed in the class; whole suite green.

- [ ] **Step 9: Commit**

```bash
git add tests/test_pool.py
git commit -m "test: cover invalid-connection release and acquire-after-close"
```

---

### Task 3: `ci.yml` — push and pull-request gate

Covers spec §1 and §5.

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: a workflow named `CI`, referenced by Task 5.

Action SHAs below were resolved from the GitHub API and are real; do not substitute floating tags.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.14"]
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: ${{ matrix.python-version }}
      # pyodbc links libodbc.so.2 from the system; the wheel bundles nothing,
      # so importing it fails without the driver manager. No ODBC *driver* is
      # installed: nothing in the suite opens a connection.
      - name: Install unixODBC
        run: sudo apt-get update && sudo apt-get install -y unixodbc
      - name: Install project
        run: pip install -e ".[dev]"
      - name: Tests
        run: pytest tests/ -q --cov=mcp_sql_server --cov-report=term-missing
      - name: Type check
        run: mypy src/mcp_sql_server/
```

- [ ] **Step 2: Validate the YAML parses**

Run:
```bash
.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml parses')"
```
Expected: `ci.yml parses`. If PyYAML is absent, `pip install pyyaml` into the venv first — it is a local check only, not a project dependency.

- [ ] **Step 3: Verify the commands the workflow runs actually work locally**

Run: `.venv/bin/pytest tests/ -q --cov=mcp_sql_server --cov-report=term-missing && .venv/bin/python -m mypy src/mcp_sql_server/`

Expected: both succeed. This does not prove the workflow works — only that the commands inside it do. The workflow itself is unproven until Task 5.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add push and pull-request workflow

Matrix covers the declared floor (3.10) and current release (3.14), so
requires-python >=3.10 becomes a tested fact rather than a claim.
Actions are pinned to commit SHAs; permissions are read-only."
```

---

### Task 4: `drift.yml` and Dependabot — upstream breakage detection

Covers spec §2, §5 and §6. This is the task that addresses the original outage.

**Files:**
- Create: `.github/workflows/drift.yml`
- Create: `.github/dependabot.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: a workflow named `Dependency drift`, referenced by Task 5.

- [ ] **Step 1: Write the drift workflow**

Create `.github/workflows/drift.yml`:

```yaml
name: Dependency drift

# Resolves dependencies fresh, rather than against whatever is already
# installed. The 2026-09-23 outage was caused by upstream moving while the
# repository sat still, so no push-triggered run could have caught it.
on:
  schedule:
    # Monday 06:23 UTC. Deliberately off the hour: GitHub delays or drops
    # scheduled jobs during the high load at the start of every hour.
    - cron: "23 6 * * 1"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: drift
  cancel-in-progress: false

jobs:
  fresh-resolve:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.14"
      - name: Install unixODBC
        run: sudo apt-get update && sudo apt-get install -y unixodbc

      - name: Install from a clean resolution
        run: pip install --no-cache-dir .

      - name: Import canary
        run: python -c "import mcp_sql_server.server"

      # Runs even if the canary failed: knowing WHICH dependency moved is
      # the whole diagnostic value of this job.
      - name: Resolved versions
        if: always()
        run: pip freeze

      - name: Install dev extras and run the suite
        run: |
          pip install --no-cache-dir ".[dev]"
          pytest tests/ -q

      # Steps above resolve within mcp<3 and pydantic<3, so they can never
      # meet a new major — the exact event that caused the outage. This step
      # deliberately breaches the ceilings. Advisory: it goes red without
      # failing the job.
      - name: Probe above the declared ceilings
        continue-on-error: true
        run: |
          pip install --no-cache-dir --upgrade mcp pydantic
          pip freeze | grep -E '^(mcp|pydantic)=='
          python -c "import mcp_sql_server.server"
```

- [ ] **Step 2: Write the Dependabot configuration**

Create `.github/dependabot.yml`:

```yaml
version: 2
updates:
  # Opens a PR when a dependency publishes a new version, including a major
  # beyond the declared ceilings, which ci.yml then adjudicates. The PR
  # traffic also counts as repository activity, which keeps GitHub from
  # auto-disabling the scheduled drift workflow after 60 days of quiet.
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
      day: monday
      time: "06:23"
    open-pull-requests-limit: 5
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
      day: monday
      time: "06:23"
```

- [ ] **Step 3: Validate both files parse**

Run:
```bash
.venv/bin/python -c "
import yaml
for f in ['.github/workflows/drift.yml', '.github/dependabot.yml']:
    yaml.safe_load(open(f)); print(f, 'parses')
"
```
Expected: both print `parses`.

- [ ] **Step 4: Rehearse the drift sequence locally**

The workflow cannot run here, but its core sequence can be rehearsed in a throwaway venv to catch an ordering mistake before pushing:

```bash
python3 -m venv /tmp/drift-rehearsal
/tmp/drift-rehearsal/bin/pip install --no-cache-dir --quiet .
/tmp/drift-rehearsal/bin/python -c "import mcp_sql_server.server; print('canary OK')"
/tmp/drift-rehearsal/bin/pip freeze | grep -E '^(mcp|pydantic|pyodbc)=='
rm -rf /tmp/drift-rehearsal
```

Expected: `canary OK`, and versions inside the declared bounds.

Note what this rehearsal exposes: a non-editable install puts `DEFAULT_ENV_PATH` (`config.py:16`, package-relative) inside site-packages, where no user will ever place a `.env`. The canary still passes. That is a pre-existing question about non-editable installs, out of scope here — record it, do not fix it in this task.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/drift.yml .github/dependabot.yml
git commit -m "ci: detect upstream drift on a schedule, plus Dependabot

The in-bounds resolve protects only the unbounded dependencies; the
above-ceiling probe is what can actually see a new major of mcp or
pydantic. Dependabot turns that signal into an actionable PR and keeps
the 60-day scheduled-workflow clock from expiring."
```

---

### Task 5: Prove the workflows on a real run

Covers spec Verification 3, 4 and 5. **The preceding tasks do not prove CI works.** A workflow is only proven by a real run.

**Files:** none — this task pushes and observes.

**Interfaces:**
- Consumes: `ci.yml` (Task 3), `drift.yml` (Task 4).
- Produces: confirmation, or defects to fix.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin HEAD
```

- [ ] **Step 2: Watch the CI run**

```bash
gh run watch --exit-status
```

Expected: the `CI` workflow succeeds on **both** matrix entries. If `pip install -e ".[dev]"` fails on 3.14, or `import pyodbc` fails despite the unixODBC step, fix it here and push again — that is precisely what this task is for.

- [ ] **Step 3: Trigger the drift job manually**

```bash
gh workflow run "Dependency drift"
sleep 15
gh run watch --exit-status
```

Expected: the job succeeds. The above-ceiling probe may show a red ✗ inside an otherwise green run — that is correct behaviour, not a failure to fix.

- [ ] **Step 4: Confirm the drift job resolved fresh**

```bash
gh run view --log | grep -A 20 'Resolved versions'
```

Expected: current releases of `mcp`, `pydantic`, `pyodbc`, not a cached or stale set. If versions look pinned or old, `--no-cache-dir` is not taking effect.

- [ ] **Step 5: Read the above-ceiling probe's outcome**

```bash
gh run view --log | grep -A 10 'Probe above the declared ceilings'
```

Record what it reports. A red probe today means a new major already exists and the code does not tolerate it — file that as an issue rather than silently widening a ceiling.

- [ ] **Step 6: Report honestly**

State which runs were green, on which Python versions, and what the probe said. Do not claim CI works on the strength of the YAML parsing — only on the strength of a green run.

---

## Notes for the implementer

- **`pyproject.toml` is the only non-test file this plan changes**, and only to add pytest configuration. If you find yourself editing `src/`, stop: either a characterization test found a real bug (report it, do not fix it inline) or the plan has drifted.
- **The `utils._db_getter` cache** described in Task 1 is the single most likely thing to waste your time. If a dispatch test appears to reach a real database, that cache is why.
- **635 is the current test count.** Each task's expectations assume the previous ones landed.
- **`.venv/bin/` prefixes are deliberate** — the repository's virtualenv is not activated by default in this environment.
