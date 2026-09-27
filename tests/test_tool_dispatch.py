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
