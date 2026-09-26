"""Regression test for `python -m mcp_sql_server.server` double-import.

Running the module with `-m` executes server.py as `__main__`, while tools
import it as `mcp_sql_server.server` via utils.py's lazy import. Without the
fix, that creates two module copies with two independent `_registry`
globals: `main()` validates one registry and `lifespan` closes it, but the
tools use the other copy's registry, so its pool is never closed on
shutdown.

This test launches the real entrypoint as a subprocess and drives it over
stdio with a minimal JSON-RPC handshake, asserting the registry is
initialized exactly once and closed on shutdown.

Safety: the repository's real `.env` points at a production database. This
test must never open a real connection. It uses port 1 (nothing listens
there) and only calls `list_databases`, which does not connect. The child
environment is built explicitly with no inherited DB_*/SQL_SERVER_* values,
and `DB_DATABASES` is set to empty so the real .env's production aliases
are not merged in either.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile


def _rpc(msg_id: int | None, method: str, params: dict) -> dict:
    payload: dict = {"jsonrpc": "2.0", "method": method, "params": params}
    if msg_id is not None:
        payload["id"] = msg_id
    return payload


def _write_message(proc: subprocess.Popen, message: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


def _read_responses_until(proc: subprocess.Popen, target_id: int, timeout: float) -> list[dict]:
    """Read newline-delimited JSON-RPC responses until `target_id` is seen."""
    import selectors

    assert proc.stdout is not None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)

    responses = []
    deadline = __import__("time").monotonic() + timeout
    buf = b""
    while __import__("time").monotonic() < deadline:
        remaining = deadline - __import__("time").monotonic()
        if remaining <= 0:
            break
        events = sel.select(timeout=remaining)
        if not events:
            continue
        chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            responses.append(obj)
            if obj.get("id") == target_id:
                return responses
    return responses


def test_entrypoint_single_registry_and_clean_shutdown():
    """`python -m mcp_sql_server.server` initializes the registry once and closes it."""
    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "SQL_SERVER_HOST": "127.0.0.1",
        "SQL_SERVER_PORT": "1",
        "SQL_SERVER_USER": "u",
        "SQL_SERVER_PASSWORD": "p",
        "SQL_SERVER_DATABASE": "d",
        "DB_DATABASES": "",
    }
    # Preserve interpreter discoverability for -m without inheriting the
    # rest of the real environment (no DB_*/SQL_SERVER_* leaking in).
    for var in ("SYSTEMROOT", "PYTHONHOME", "PYTHONPATH"):
        if var in os.environ:
            child_env[var] = os.environ[var]

    with tempfile.TemporaryDirectory() as tmpdir:
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_sql_server.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmpdir,
            env=child_env,
        )
        try:
            _write_message(
                proc,
                _rpc(
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "0.0.1"},
                    },
                ),
            )
            responses = _read_responses_until(proc, 1, timeout=10)
            assert any(r.get("id") == 1 for r in responses), responses

            _write_message(
                proc,
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )

            _write_message(proc, _rpc(2, "tools/call", {"name": "list_databases", "arguments": {}}))
            responses = _read_responses_until(proc, 2, timeout=10)
            call_response = next((r for r in responses if r.get("id") == 2), None)
            assert call_response is not None, responses
            assert "error" not in call_response, call_response
            result = call_response.get("result", {})
            assert result.get("isError") is not True, result

            assert proc.stdin is not None
            proc.stdin.close()
            proc.wait(timeout=20)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

        stderr = proc.stderr.read().decode() if proc.stderr else ""

        assert proc.returncode == 0, stderr
        assert stderr.count("Database registry initialized") == 1, stderr
        assert "Database registry closed" in stderr, stderr
