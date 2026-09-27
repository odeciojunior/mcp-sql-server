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
from mcp_sql_server.cache import invalidate_metadata_cache


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
    # list_tables/describe_table/list_procedures are @cached in a module-global
    # TTLCache that nothing resets between tests, so another test's rows would
    # be served instead of this fixture's.
    invalidate_metadata_cache()
    return calls


def _payload(result: Any) -> dict[str, Any]:
    """Extract the JSON body from a CallToolResult."""
    assert result.content, "tool returned no content"
    return json.loads(result.content[0].text)


async def test_execute_query_dispatches(dispatch_db):
    result = await server.mcp.call_tool("execute_query", {"sql": "SELECT 1"})
    assert result.is_error is False
    assert _payload(result)["success"] is True


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


RESOURCE_URIS = [
    "sqlserver://tables",
    "sqlserver://database/info",
    "sqlserver://functions",
    "sqlserver://pool/stats",
    "sqlserver://databases",
]


# Union of every key the resource handlers read: schema/name/type for the
# table listing, schema/name/return_type for functions, and the four columns
# resource_database_info selects. One row shape satisfies all of them.
RESOURCE_ROW = {
    "schema": "dbo",
    "name": "Users",
    "type": "BASE TABLE",
    "return_type": "int",
    "version": "Microsoft SQL Server 2017",
    "database_name": "TestDb",
    "collation": "SQL_Latin1_General_CP1_CI_AS",
    "edition": "Enterprise Edition",
}


@pytest.fixture
def resource_db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A db whose execute_query returns rows every resource can render.

    The generic mock_cursor returns (id, name, value) rows, which make the
    schema-reading resources raise or emit an error string — passing a
    "returns a string" assertion while proving nothing.
    """
    db = MagicMock()
    db.execute_query.return_value = [RESOURCE_ROW]
    monkeypatch.setattr(utils_module, "_db_getter", lambda database="default": db)
    monkeypatch.setattr(server, "_registry", None)
    invalidate_metadata_cache()
    return db


@pytest.mark.parametrize("uri", RESOURCE_URIS)
async def test_every_resource_dispatches(resource_db, uri):
    """Each resource renders real content through dispatch, not an error."""
    contents = list(await server.mcp.read_resource(uri))
    assert contents, f"{uri} returned nothing"
    body = contents[0].content
    assert isinstance(body, str)
    assert not body.startswith("Error"), f"{uri} reported an error: {body[:200]}"


async def test_unknown_resource_uri_raises(resource_db):
    """An unregistered URI is rejected rather than returning empty content."""
    from mcp.server.mcpserver.exceptions import ResourceNotFoundError

    with pytest.raises(ResourceNotFoundError):
        await server.mcp.read_resource("sqlserver://not-a-resource")
