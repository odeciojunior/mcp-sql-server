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

The obvious reading is that test coverage was missing. That reading is half right, and getting the halves straight determines what this work should build.

At the broken commit (`b1bdf3d`), `tests/test_server.py:9` contained `from mcp_sql_server import server`, and `src/mcp_sql_server/server.py:8` was `from mcp.server.fastmcp import FastMCP`. Under mcp 2.x that is an `ImportError` at collection time and pytest exits non-zero. So one test would have failed — by accident. **A single incidental top-level import was the entire safety net.** Registration-surface coverage was genuinely absent.

`tests/test_server_registration.py` and `tests/test_entrypoint.py` are sometimes cited as proof the suite was adequate. They are not evidence of anything: `git log --diff-filter=A` shows the first was added by `ba55cdc` — the migration commit that *fixed* the break — and the second by `0dfb09e`, three days later. Both were written in response to this incident. Neither could have caught it.

What was missing is that nothing ever ran the suite against freshly resolved dependencies. No commit caused the break — the outside world moved while the repository sat still. Push-triggered CI, had it existed, would also have stayed green, because it would have run against whatever was already resolved and nothing was being pushed.

The gap is therefore specific: **no scheduled job resolves dependencies fresh and runs the suite.** That is the primary thing being built here. The additional test coverage in this spec is worthwhile on its own merits, but it is not what closes the gap, and this document should not be read as claiming otherwise.

## Goals

1. An upstream dependency major that breaks installation fails visibly before a user encounters it. Note that the declared ceilings (`mcp<3`, `pydantic<3`) mean an in-bounds resolution can never surface a new major; meeting this goal needs a deliberate above-ceiling probe and Dependabot, not the drift job alone (see §2).
2. Ordinary regressions are caught on push and pull request.
3. The `requires-python = ">=3.10"` claim is verified rather than asserted.
4. The SDK dispatch layer is exercised for every tool, not just the one that avoids connecting.
5. The connection-pool cleanup paths, currently untested, are covered — they are where a leak would hide.

## Non-goals

- **No live database in CI.** Every test mocks `pyodbc.connect`; nothing here changes that.
- **No dependency on the developer machine's `.env`.** That file points at a production database. The mechanism that actually protects the suite is the autouse `_isolate_from_real_env` fixture at `conftest.py:11-33`, which monkeypatches `DEFAULT_ENV_PATH`; the new tests rely on that plus `conftest.py:197`'s patch of `server.get_db`. `tests/test_entrypoint.py` should *not* be treated as the model here — its docstring claims `DB_DATABASES=""` suppresses the real aliases, and that is false: `config.py:45-47` treats the empty string as falsy and falls through to the `.env` value, and `config.py:16` resolves `DEFAULT_ENV_PATH` package-relative so the test's `cwd` is decorative. The production aliases do load; the test is safe only because `list_databases` never connects. Fixing that docstring and defence is separate follow-on work. In CI this is all moot — `.gitignore:1` excludes `.env`, so a fresh checkout has none.
- **No coverage threshold gate.** Coverage is 96%; a hard gate invites gaming without improving the suite.
- **No auto-filed issues on drift failure.** GitHub's own notification suffices initially.
- **No rebuild of the subprocess harness.** `test_entrypoint.py` covers the `-m` double-import case it was written for; that concern is distinct and already handled.
- **No change to dependency floors.** `pytest>=7.0.0` and `mypy>=1.0.0` are effectively decorative — the strict mypy config would likely fail under literal 1.0 — but they are dev-only and never affect the shipped server. Making them honest is separate work.

## Design

### 1. `.github/workflows/ci.yml`

Triggers on `push` to `main` and on `pull_request`.

One job, matrix `python-version: ["3.10", "3.14"]` — the declared floor and the current release (3.14, which mcp 2.2.0 supports explicitly via its `anyio>=4.10; python_version >= '3.14'` marker). The floor entry is the point: it turns `requires-python >=3.10` from a claim into a tested fact. Intermediate minors are omitted; for a pure-Python server the endpoints are where version-specific breakage appears. Worth noting that neither entry matches the development interpreter (3.12), so a 3.12-only regression would be invisible to both CI and local work.

Steps: checkout, `setup-python`, install the ODBC driver manager (below), `pip install -e ".[dev]"`, `pytest tests/`, `mypy src/mcp_sql_server/`.

No secrets, no database, no network beyond PyPI.

**`pyodbc` requires unixODBC — resolved, not open.** The manylinux wheel bundles nothing:

```
$ ldd .venv/lib/python3.12/site-packages/pyodbc*.so | grep odbc
    libodbc.so.2 => /lib/x86_64-linux-gnu/libodbc.so.2
```

`libodbc.so.2` resolves from the system. Every test imports `pyodbc` even though `connect` is mocked, so `apt-get install -y unixodbc` is a required step — the driver manager only, not the Microsoft ODBC driver, since nothing opens a connection. (`ubuntu-latest` may ship it preinstalled, but relying on an undeclared image detail is worse than one apt line.)

### 2. `.github/workflows/drift.yml`

**This is the workflow that addresses the failure described in Context.**

Triggers on `schedule` — weekly, Monday 06:23 UTC (`23 6 * * 1`) — and on `workflow_dispatch`, so it can be run by hand. The offset is deliberate: GitHub documents that scheduled jobs are delayed or dropped during high load at the start of every hour.

Python 3.14 only — one version, for cost. (Resolution varies by version marker, and the only marker in the tree points at 3.14; but the honest reason to pick one is that the matrix belongs in `ci.yml`, not here.) The essential property is that resolution is **fresh**: no pip cache, no lockfile, no pre-existing virtualenv. Steps, in order:

1. `pip install .` into a clean environment — resolves the newest versions the declared bounds permit.
2. `python -c "import mcp_sql_server.server"` — the canary. This one line is what would have caught the mcp 2.x break.
3. `pip freeze` — printed unconditionally, so a failure identifies *which* dependency moved rather than merely that something did.
4. `pip install ".[dev]"` and run the full suite.
5. **Above-ceiling probe**, `continue-on-error: true`: `pip install --upgrade mcp pydantic` — deliberately breaching the declared caps — then the import canary again.

Step 3 runs whether or not step 2 passed; diagnosis depends on it.

Step 5 is what actually serves Goal 1. Steps 1-4 resolve *within* `mcp<3` and `pydantic<3`, so they can never encounter the class of event that caused the outage; the only unbounded dependencies they protect are `pyodbc` and `python-dotenv`. Step 5 is allowed to fail without failing the job: a red mark there means a new major exists and the code does not yet tolerate it — information, not a regression.

A failure here means the outside world has moved in a way the declared bounds allow but the code does not tolerate. The response is to fix the code or tighten the bound — the same choice made when `mcp>=2.0,<3` was introduced.

### 3. `tests/test_tool_dispatch.py` (new)

Calls `await server.mcp.call_tool(name, arguments)` in-process, reusing conftest's `mock_get_db` fixture and resetting `server._registry` the way `tests/test_server.py` does.

This path runs the real decorator, the real schema coercion, and the real `anyio.to_thread.run_sync` worker dispatch that SDK 2.x uses for synchronous handlers — for all ten tools, with no subprocess and no database. Today only `list_databases` is dispatch-tested, via `test_entrypoint.py`, and only because it is the one tool that never connects.

Coverage:

- Each of the ten tools returns a well-formed result through dispatch.
- Each of the five resources returns through `mcp.read_resource(uri)`. Coverage shows their wrapper bodies are equally undispatched (`server.py:286,298,305,311,317`), and they use the same fixtures, so excluding them would close two thirds of the surface for no reason.
- `database="<alias>"` routes to that alias rather than to `default`.
- An unknown **tool name** raises `ToolError` — the one case that does surface through the dispatch layer.
- An unknown **alias** does *not*. Measured: it returns a normal `CallToolResult` with `is_error=False` whose body is `{"success": false, "error": "Unknown database …"}`. The handler catches it before dispatch sees it. Assert both halves — `is_error is False` and `json.loads(content[0].text)["success"] is False` — because the intuitive assertion ("an error surfaces") is wrong and would fail.

**Return shape — resolved, not open.** Measured against the installed mcp 2.2.0: `MCPServer.call_tool(name, arguments, context=None)` returns a Pydantic `mcp_types._types.CallToolResult` with fields `meta`, `content`, `structured_content`, `is_error`, `result_type`. The in-process shape is the client-side shape. `call_tool` also documents `ToolError` for argument-validation failures and `UnexpectedToolError` (carrying `__cause__`) otherwise.

**pytest-asyncio configuration is a prerequisite.** The suite currently contains zero `async def test` functions and the repository has no pytest configuration at all — no `[tool.pytest.ini_options]`, no `pytest.ini`, no `setup.cfg`. Installed `pytest-asyncio` 1.4.0 defaults to strict mode, so these tests would silently skip or error. Add `asyncio_mode = "auto"`, or mark every test explicitly. This is a blocker, not a detail.

### 4. Pool cleanup tests — extend `tests/test_pool.py`

Measured uncovered lines in `pool.py` (`pytest --cov --cov-report=term-missing`): `103, 146-147, 205-207, 257-258, 266-267, 276-278`. The retirement branches in `acquire()` for `max_lifetime` and health-check failure are **already covered**; an earlier draft of this spec listed them in error. The genuinely unexercised cleanup paths are:

- `205-207` — `acquire()` retires a connection past `idle_timeout`.
- `257-258` — `release()` into an already-closed pool.
- `266-267` — `release()` retires a connection past `max_lifetime`.
- `276-278` — `release()` when the pool queue is already full.

(`103` and `146-147` are also uncovered and are in scope if cheap to reach; they are not cleanup paths.)

Each asserts the connection is closed rather than leaked, and that `size` and `available` accounting remains consistent afterwards.

These are **characterization tests, not TDD**. They encode current behaviour on paths nothing exercises. If one fails on its first run, that is a bug discovered — not a test to adjust to match the code.

### 5. Workflow hygiene

Applies to both workflows, and easy to omit by accident:

- **`permissions: contents: read`** declared at the top of each. Neither needs write access; omitting the block inherits the repository default, which can be read/write for `GITHUB_TOKEN`.
- **`concurrency` with `cancel-in-progress: true`**, keyed on ref, so pushing to a PR does not stack redundant matrix runs.
- **Actions pinned to full commit SHAs**, not floating tags. This is a public repository; `actions/checkout@v4` is a mutable reference and a supply-chain decision worth making deliberately rather than by default.
- **Scheduled workflows auto-disable after 60 days without repository activity** on public repos (GitHub Docs, *Events that trigger workflows* → `schedule`). This repository going quiet is exactly the state `drift.yml` exists to cover, so the mechanism that protects against drift is also the one most likely to be switched off silently. Dependabot PRs count as activity and mitigate this as a side effect; the risk should still be recorded so a future reader knows to check the workflow is still enabled.
- **`--cov-report=term-missing` printed in CI.** There is no coverage gate by choice, but printing the report costs nothing and keeps the 96% figure visible rather than letting it decay unobserved.

### 6. `.github/dependabot.yml`

Weekly `pip` ecosystem updates against `pyproject.toml`. This is the standard control for "a dependency published a new major", it produces an actionable PR that `ci.yml` adjudicates rather than a bare red mark, and its PR traffic keeps the 60-day scheduled-workflow clock from expiring.

## Files

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | new — push/PR, Python 3.10 + 3.14, pytest + mypy |
| `.github/workflows/drift.yml` | new — weekly + manual, fresh resolve, import canary, suite |
| `tests/test_tool_dispatch.py` | new — 10 tools via `mcp.call_tool`, 5 resources via `mcp.read_resource` |
| `tests/test_pool.py` | extended — the four measured uncovered cleanup branches |
| `.github/dependabot.yml` | new — weekly pip updates, also keeps the schedule alive |
| `pyproject.toml` | `[tool.pytest.ini_options] asyncio_mode = "auto"` — prerequisite for async tests |

No `src/` files change; `pyproject.toml` gains only pytest configuration. If a characterization test exposes a real defect in `pool.py`, fixing it is follow-on work, tracked separately rather than folded in silently.

## Verification

1. `pytest tests/` — 635 tests today, plus the new ones, all passing.
2. `mypy src/mcp_sql_server/` — clean under strict mode.
3. **CI cannot be verified locally.** A workflow is only proven by a real run. The branch gets pushed, and the work is not complete until a run is green on both matrix entries. No claim that CI works will be made before that.
4. `drift.yml` gets one manual `workflow_dispatch` run, confirmed green, before the schedule is trusted.
5. Confirm the drift job genuinely resolves fresh — its `pip freeze` output should show current releases, not a cached set.

## Risks

- **The scheduled workflow may be silently switched off.** GitHub auto-disables scheduled workflows after 60 days without repository activity on public repos. A repository that sits still is precisely what `drift.yml` protects, so the control and the condition it guards against coincide. Dependabot traffic mitigates this; it is not a guarantee, and a future reader should check the workflow is still enabled before trusting a quiet history.
- **The above-ceiling probe is advisory only.** Being `continue-on-error`, it goes red without failing the job, which is the intent — but a red mark nobody reads is the same as no probe. It is worth reviewing whenever the drift run is looked at.
- **`mypy>=1.0.0` is unbounded and the config uses a deprecated option.** mypy 2.1.0 already warns that `strict_concatenate` is deprecated in favour of `extra_checks`. A future mypy that removes it turns CI red for a configuration reason rather than a code one — the exact scheduled-job fatigue described below. Worth fixing before it fires.
- **Scheduled-job fatigue.** A weekly job that fails for an unrelated reason trains you to ignore it. Keeping the failure signal specific — the import canary plus `pip freeze` — is what keeps it worth reading.
- **Drift failures are not the repo's fault.** The job goes red when an upstream project releases a breaking major. That is the intended behaviour, and worth remembering before treating red as a regression.
- **A characterization test may fail immediately**, revealing an existing pool defect. That is a success for the exercise and a new piece of work, not a reason to weaken the test.
