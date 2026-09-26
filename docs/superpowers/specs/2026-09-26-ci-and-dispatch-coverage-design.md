# CI and dispatch coverage

**Date:** 2026-09-26
**Status:** design, awaiting review

## Context

On 2026-09-23 this project shipped a version that no new user could run. `pyproject.toml` declared `mcp>=1.2.0` with no upper bound; MCP SDK 2.x removed `mcp.server.fastmcp` with no compatibility shim, so a fresh `pip install git+…` resolved to 2.x and died at import:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

Existing installs were unaffected, because their virtualenvs predated 2.x. The break was invisible locally and reached users regardless.

### What actually failed

The obvious reading is that test coverage was missing. That reading is wrong, and getting it right determines what this work should build.

`tests/test_server_registration.py` imports the server object. `tests/test_entrypoint.py` spawns `python -m mcp_sql_server.server` and drives a real `initialize` plus `tools/call` over stdio. **Either would have failed loudly on the 2.x break.** The suite was adequate.

What was missing is that nothing ever ran that suite against freshly resolved dependencies. No commit caused the break — the outside world moved while the repository sat still. Push-triggered CI, had it existed, would also have stayed green, because it would have run against whatever was already resolved and nothing was being pushed.

The gap is therefore specific: **no scheduled job resolves dependencies fresh and runs the suite.** That is the primary thing being built here. The additional test coverage in this spec is worthwhile on its own merits, but it is not what closes the gap, and this document should not be read as claiming otherwise.

## Goals

1. An upstream dependency major that breaks installation fails visibly, on a timer, before a user encounters it.
2. Ordinary regressions are caught on push and pull request.
3. The `requires-python = ">=3.10"` claim is verified rather than asserted.
4. The SDK dispatch layer is exercised for every tool, not just the one that avoids connecting.
5. The connection-pool cleanup paths, currently untested, are covered — they are where a leak would hide.

## Non-goals

- **No live database in CI.** Every test mocks `pyodbc.connect`; nothing here changes that.
- **No dependency on the developer machine's `.env`.** That file points at a production database. `tests/test_entrypoint.py` already defends against it explicitly, and this work inherits that discipline rather than weakening it.
- **No coverage threshold gate.** Coverage is 96%; a hard gate invites gaming without improving the suite.
- **No auto-filed issues on drift failure.** GitHub's own notification suffices initially.
- **No rebuild of the subprocess harness.** `test_entrypoint.py` covers the `-m` double-import case it was written for; that concern is distinct and already handled.
- **No change to dependency floors.** `pytest>=7.0.0` and `mypy>=1.0.0` are effectively decorative — the strict mypy config would likely fail under literal 1.0 — but they are dev-only and never affect the shipped server. Making them honest is separate work.

## Design

### 1. `.github/workflows/ci.yml`

Triggers on `push` to `main` and on `pull_request`.

One job, matrix `python-version: ["3.10", "3.13"]` — the declared floor and a current release. The floor entry is the point: it turns `requires-python >=3.10` from a claim into a tested fact. Intermediate minors are omitted; for a pure-Python server the endpoints are where version-specific breakage appears.

Steps: checkout, `setup-python`, install the ODBC driver manager (below), `pip install -e ".[dev]"`, `pytest tests/`, `mypy src/mcp_sql_server/`.

No secrets, no database, no network beyond PyPI.

**Open implementation question — `pyodbc` import requirements.** `pyodbc` links against unixODBC, and every test imports it even though `connect` is mocked. CI will most likely need `apt-get install -y unixodbc` — the driver manager only, not the Microsoft ODBC driver, since nothing opens a connection. This must be verified during implementation rather than assumed: if the manylinux wheel proves self-contained, the step is dropped.

### 2. `.github/workflows/drift.yml`

**This is the workflow that addresses the failure described in Context.**

Triggers on `schedule` — weekly, Monday 06:00 UTC (`0 6 * * 1`) — and on `workflow_dispatch`, so it can be run by hand.

Python 3.13 only. Drift is about what the newest permitted resolution does, and the newest Python is where a fresh resolution differs most from the pinned one; the floor is already covered by `ci.yml`. The essential property is that resolution is **fresh**: no pip cache, no lockfile, no pre-existing virtualenv. Steps, in order:

1. `pip install .` into a clean environment — resolves the newest versions the declared bounds permit.
2. `python -c "import mcp_sql_server.server"` — the canary. This one line is what would have caught the mcp 2.x break.
3. `pip freeze` — printed unconditionally, so a failure identifies *which* dependency moved rather than merely that something did.
4. `pip install ".[dev]"` and run the full suite.

Step 3 runs whether or not step 2 passed; diagnosis depends on it.

A failure here means the outside world has moved in a way the declared bounds allow but the code does not tolerate. The response is to fix the code or tighten the bound — the same choice made when `mcp>=2.0,<3` was introduced.

### 3. `tests/test_tool_dispatch.py` (new)

Calls `await server.mcp.call_tool(name, arguments)` in-process, reusing conftest's `mock_get_db` fixture and resetting `server._registry` the way `tests/test_server.py` does.

This path runs the real decorator, the real schema coercion, and the real `anyio.to_thread.run_sync` worker dispatch that SDK 2.x uses for synchronous handlers — for all ten tools, with no subprocess and no database. Today only `list_databases` is dispatch-tested, via `test_entrypoint.py`, and only because it is the one tool that never connects.

Coverage:

- Each of the ten tools returns a well-formed result through dispatch.
- `database="<alias>"` routes to that alias rather than to `default`.
- An unknown alias surfaces an error through the dispatch layer instead of crashing the handler.

**Open implementation question — return shape.** `call_tool` exists on `MCPServer`; its in-process return value in 2.x has not been confirmed (the client-side shape is `content` plus `structured_content`). Pin this down against the installed package before writing assertions.

### 4. Pool cleanup tests — extend `tests/test_pool.py`

Four branches at `pool.py:199-213` and `pool.py:264-278` are currently unexercised, all of them resource-cleanup paths where a defect leaks or double-closes connections:

- Retirement of a connection past `max_lifetime`.
- Retirement of a connection past `idle_timeout`.
- Retirement of a connection that fails its health check.
- `release()` when the pool queue is already full.

Each asserts the connection is closed rather than leaked, and that `size` and `available` accounting remains consistent afterwards.

These are **characterization tests, not TDD**. They encode current behaviour on paths nothing exercises. If one fails on its first run, that is a bug discovered — not a test to adjust to match the code.

## Files

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | new — push/PR, Python 3.10 + 3.13, pytest + mypy |
| `.github/workflows/drift.yml` | new — weekly + manual, fresh resolve, import canary, suite |
| `tests/test_tool_dispatch.py` | new — all 10 tools through `mcp.call_tool` |
| `tests/test_pool.py` | extended — four retirement/`queue.Full` branches |

No source files change. If a characterization test exposes a real defect in `pool.py`, fixing it is follow-on work, tracked separately rather than folded in silently.

## Verification

1. `pytest tests/` — 635 tests today, plus the new ones, all passing.
2. `mypy src/mcp_sql_server/` — clean under strict mode.
3. **CI cannot be verified locally.** A workflow is only proven by a real run. The branch gets pushed, and the work is not complete until a run is green on both matrix entries. No claim that CI works will be made before that.
4. `drift.yml` gets one manual `workflow_dispatch` run, confirmed green, before the schedule is trusted.
5. Confirm the drift job genuinely resolves fresh — its `pip freeze` output should show current releases, not a cached set.

## Risks

- **`pyodbc` may not import in CI** without unixODBC. Verify early; it blocks every job if wrong.
- **`call_tool`'s in-process shape is unconfirmed.** Assertions written against a guess would be brittle.
- **Scheduled-job fatigue.** A weekly job that fails for an unrelated reason trains you to ignore it. Keeping the failure signal specific — the import canary plus `pip freeze` — is what keeps it worth reading.
- **Drift failures are not the repo's fault.** The job goes red when an upstream project releases a breaking major. That is the intended behaviour, and worth remembering before treating red as a regression.
- **A characterization test may fail immediately**, revealing an existing pool defect. That is a success for the exercise and a new piece of work, not a reason to weaken the test.
