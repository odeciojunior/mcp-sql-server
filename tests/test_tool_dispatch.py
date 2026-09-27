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
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from mcp_sql_server import server
from mcp_sql_server import utils as utils_module
from mcp_sql_server.cache import invalidate_metadata_cache


@pytest.fixture
def dispatch_db(mock_db_manager: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Route every utils.get_db(...) call to the mocked DatabaseManager.

    Patches the cached getter itself, not server.get_db, because
    utils._db_getter holds a direct function reference once populated.
    """
    calls = MagicMock()

    def _getter(database: str = "default") -> Any:
        calls(database)
        return mock_db_manager

    monkeypatch.setattr(utils_module, "_db_getter", _getter)
    monkeypatch.setattr(utils_module, "_registry_getter", None)
    monkeypatch.setattr(server, "_registry", None)
    # list_tables/describe_table/list_procedures are @cached in a module-global
    # TTLCache that nothing resets between tests. Clearing on entry stops another
    # test's rows reaching this one; clearing on exit stops this one's rows
    # reaching whatever runs next.
    invalidate_metadata_cache()
    yield calls
    invalidate_metadata_cache()


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
    ("list_tables", {}),
    ("describe_table", {"table_name": "Users"}),
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
    assert body.get("success") is True, f"{name} did not succeed: {body}"


# The three tools below cannot succeed against the generic mock cursor: two need a
# row carrying a `definition`, and one needs a real file on disk. Parametrising them
# with the rest would only assert that a failure dict round-trips.


@pytest.fixture
def definition_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """A db whose execute_query returns a row with a non-empty definition."""
    db = MagicMock()
    db.execute_query.return_value = [{"definition": "CREATE VIEW vwUsers AS SELECT 1"}]
    monkeypatch.setattr(utils_module, "_db_getter", lambda database="default": db)
    monkeypatch.setattr(utils_module, "_registry_getter", None)
    monkeypatch.setattr(server, "_registry", None)
    invalidate_metadata_cache()
    yield db
    invalidate_metadata_cache()


async def test_get_view_definition_dispatches(definition_db):
    result = await server.mcp.call_tool("get_view_definition", {"view_name": "vwUsers"})
    assert result.is_error is False
    body = _payload(result)
    assert body["success"] is True, body
    assert "CREATE VIEW" in body["definition"]


async def test_get_function_definition_dispatches(definition_db):
    result = await server.mcp.call_tool("get_function_definition", {"function_name": "fnUsers"})
    assert result.is_error is False
    body = _payload(result)
    assert body["success"] is True, body


async def test_execute_query_file_dispatches(dispatch_db, query_dir, monkeypatch):
    """Runs a real .sql file, so the tool resolves a path instead of reporting not-found."""
    from mcp_sql_server.config import get_query_dir

    monkeypatch.setenv("QUERY_DIR", str(query_dir))
    get_query_dir.cache_clear()
    try:
        result = await server.mcp.call_tool("execute_query_file", {"filename": "select_users.sql"})
    finally:
        get_query_dir.cache_clear()
    assert result.is_error is False
    body = _payload(result)
    assert body["success"] is True, body


async def test_wrong_typed_argument_is_rejected(dispatch_db):
    """A non-integer limit is rejected by schema validation, not the handler."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="valid integer"):
        await server.mcp.call_tool("execute_query", {"sql": "SELECT 1", "limit": "abc"})
    dispatch_db.assert_not_called()


async def test_missing_required_argument_is_rejected(dispatch_db):
    """execute_query without sql never reaches the handler."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="Field required"):
        await server.mcp.call_tool("execute_query", {})
    dispatch_db.assert_not_called()


async def test_unknown_tool_raises(dispatch_db):
    """An unknown tool name is the one case that surfaces through dispatch."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="Unknown tool"):
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


RESOURCE_EXPECTATIONS = [
    ("sqlserver://tables", "Users"),
    ("sqlserver://database/info", "TestDb"),
    ("sqlserver://functions", "Users"),
    ("sqlserver://pool/stats", "Total Connections Created"),
    ("sqlserver://databases", "| default | testhost | 1433 | TestDb | ok |"),
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


# resource_pool_stats reads db.pool_stats and renders only the keys it knows.
# A MagicMock attribute is not None, so it takes the table path and emits an
# empty table — which passes a "not an error" assertion while showing nothing.
POOL_STATS = {
    "total_connections": 3,
    "pool_size": 5,
    "in_use": 1,
    "available": 2,
    "peak_usage": 4,
}


@pytest.fixture
def resource_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """A db and registry from which every resource can render real content.

    Three things are needed, and each was found by watching a resource render
    nothing while still passing a weaker assertion:
      - execute_query rows carrying every key the handlers read (RESOURCE_ROW),
      - a real pool_stats dict, not a MagicMock attribute,
      - valid DB_* values, because the autouse isolation fixture strips them and
        the registry then reports `default` as misconfigured.
    """
    for key, value in {
        "DB_HOST": "testhost",
        "DB_PORT": "1433",
        "DB_USER": "tester",
        "DB_PASSWORD": "secret",
        "DB_NAME": "TestDb",
    }.items():
        monkeypatch.setenv(key, value)

    db = MagicMock()
    db.execute_query.return_value = [RESOURCE_ROW]
    db.pool_stats = POOL_STATS
    monkeypatch.setattr(utils_module, "_db_getter", lambda database="default": db)
    monkeypatch.setattr(utils_module, "_registry_getter", None)
    monkeypatch.setattr(server, "_registry", None)
    invalidate_metadata_cache()
    yield db
    invalidate_metadata_cache()


@pytest.mark.parametrize("uri,expected", RESOURCE_EXPECTATIONS, ids=[u for u, _ in RESOURCE_EXPECTATIONS])
async def test_every_resource_dispatches(resource_db, uri, expected):
    """Each resource renders real content through dispatch, not an error.

    Each case asserts a substring only present when the resource actually
    rendered data. "Does not start with Error" is not enough: pool/stats and
    databases both satisfy it while emitting an empty table.
    """
    contents = list(await server.mcp.read_resource(uri))
    assert contents, f"{uri} returned nothing"
    body = contents[0].content
    assert isinstance(body, str)
    assert not body.startswith("Error"), f"{uri} reported an error: {body[:200]}"
    assert expected in body, f"{uri} rendered no data (missing {expected!r}): {body[:300]}"


async def test_unknown_resource_uri_raises(resource_db):
    """An unregistered URI is rejected rather than returning empty content."""
    from mcp.server.mcpserver.exceptions import ResourceNotFoundError

    with pytest.raises(ResourceNotFoundError):
        await server.mcp.read_resource("sqlserver://not-a-resource")
