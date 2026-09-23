"""Tests for the MCP SDK registration surface.

These tests exercise the server object itself rather than the plain functions
behind it. The rest of the suite imports tools and resources directly, so an
SDK-level break -- a renamed class, a moved module, a changed decorator -- would
not fail anything. That is how the mcp 1.x to 2.x rename reached users: the
package installed cleanly and only failed at first run.

Importing this module is itself the first assertion.
"""

import asyncio

import pytest

from mcp_sql_server import server

EXPECTED_TOOLS = {
    "_describe_table",
    "_execute_procedure",
    "_execute_query",
    "_execute_query_file",
    "_execute_statement",
    "_get_function_definition",
    "_get_view_definition",
    "_list_databases",
    "_list_procedures",
    "_list_tables",
}

EXPECTED_RESOURCES = {
    "sqlserver://database/info",
    "sqlserver://databases",
    "sqlserver://functions",
    "sqlserver://pool/stats",
    "sqlserver://tables",
}

# Every tool that reaches a database takes a `database` argument. Only
# _list_databases inspects the registry itself and so takes none.
TOOLS_WITHOUT_DATABASE_ARG = {"_list_databases"}


@pytest.fixture(scope="module")
def registered_tools():
    """All tools registered on the server object."""
    return asyncio.run(server.mcp.list_tools())


@pytest.fixture(scope="module")
def registered_resources():
    """All resources registered on the server object."""
    return asyncio.run(server.mcp.list_resources())


class TestServerObject:
    """The server object is constructed and importable."""

    def test_server_imports(self):
        assert server.mcp is not None

    def test_server_name(self):
        assert server.mcp.name == "MCP SQL Server"

    def test_server_reports_a_version(self):
        """2.x reports an empty version unless one is passed explicitly."""
        from mcp_sql_server import __version__

        assert server.mcp.version == __version__
        assert server.mcp.version

    def test_name_is_not_shifted_into_title(self):
        """Guards the positional-argument trap.

        MCPServer's positional order is (name, title, description,
        instructions, ...). Passing anything but `name` positionally silently
        populates `title` instead, with no error.
        """
        assert getattr(server.mcp, "title", None) in (None, "MCP SQL Server")


class TestToolRegistration:
    """The expected tools are registered, with the expected shape."""

    def test_tool_count(self, registered_tools):
        assert len(registered_tools) == len(EXPECTED_TOOLS)

    def test_tool_names(self, registered_tools):
        assert {t.name for t in registered_tools} == EXPECTED_TOOLS

    def test_every_tool_has_a_description(self, registered_tools):
        undocumented = [t.name for t in registered_tools if not t.description]
        assert undocumented == []

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS - TOOLS_WITHOUT_DATABASE_ARG))
    def test_tool_exposes_database_parameter(self, registered_tools, tool_name):
        """The multi-database contract the setup wizard depends on.

        Each tool must accept `database`, defaulting to "default", so a caller
        can target a configured alias.
        """
        tool = next(t for t in registered_tools if t.name == tool_name)
        properties = tool.input_schema.get("properties", {})
        assert "database" in properties, f"{tool_name} has no database parameter"
        assert properties["database"].get("default") == "default"

    def test_list_databases_takes_no_database_argument(self, registered_tools):
        tool = next(t for t in registered_tools if t.name == "_list_databases")
        assert "database" not in tool.input_schema.get("properties", {})

    def test_execute_query_schema(self, registered_tools):
        """Spot-check a representative tool's full parameter set."""
        tool = next(t for t in registered_tools if t.name == "_execute_query")
        properties = tool.input_schema.get("properties", {})
        assert {"sql", "params", "limit", "database"} <= set(properties)
        assert "sql" in tool.input_schema.get("required", [])


class TestResourceRegistration:
    """The expected resources are registered at the expected URIs."""

    def test_resource_count(self, registered_resources):
        assert len(registered_resources) == len(EXPECTED_RESOURCES)

    def test_resource_uris(self, registered_resources):
        assert {str(r.uri) for r in registered_resources} == EXPECTED_RESOURCES

    def test_every_resource_has_a_description(self, registered_resources):
        undocumented = [str(r.uri) for r in registered_resources if not r.description]
        assert undocumented == []
