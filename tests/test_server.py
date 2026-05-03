import os
import pytest
from unittest.mock import patch
from mssql_mcp_server.server import (
    app,
    list_tools,
    list_resources,
    read_resource,
    call_tool,
)
from pydantic import AnyUrl


def test_server_initialization():
    """Test that the server initializes correctly."""
    assert app.name == "mssql_mcp_server"


@pytest.mark.asyncio
async def test_list_tools():
    """Test that list_tools returns expected tools in single-host mode."""
    with patch.dict(
        os.environ,
        {
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        },
        clear=True,
    ):
        tools = await list_tools()
        assert len(tools) == 1
        assert tools[0].name == "execute_sql"
        assert "query" in tools[0].inputSchema["properties"]
        assert "connection" not in tools[0].inputSchema["properties"]


@pytest.mark.asyncio
async def test_list_tools_multi_host():
    """Test that list_tools exposes connection param + list_connections in multi-host mode."""
    with patch.dict(
        os.environ,
        {
            "MSSQL_HOST_1_NAME": "a",
            "MSSQL_HOST_1_SERVER": "h",
            "MSSQL_HOST_1_DATABASE": "db",
            "MSSQL_HOST_1_USER": "u",
            "MSSQL_HOST_1_PASSWORD": "p",
            "MSSQL_HOST_2_NAME": "b",
            "MSSQL_HOST_2_SERVER": "h",
            "MSSQL_HOST_2_DATABASE": "db",
            "MSSQL_HOST_2_USER": "u",
            "MSSQL_HOST_2_PASSWORD": "p",
        },
        clear=True,
    ):
        tools = await list_tools()
        names = [t.name for t in tools]
        assert "execute_sql" in names
        assert "list_connections" in names
        exec_tool = next(t for t in tools if t.name == "execute_sql")
        assert "connection" in exec_tool.inputSchema["properties"]
        assert "connection" in exec_tool.inputSchema["required"]
        assert set(exec_tool.inputSchema["properties"]["connection"]["enum"]) == {
            "a",
            "b",
        }


@pytest.mark.asyncio
async def test_call_tool_invalid_name():
    """Test calling a tool with an invalid name."""
    with patch.dict(
        os.environ,
        {
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        },
        clear=True,
    ):
        with pytest.raises(ValueError, match="Unknown tool"):
            await call_tool("invalid_tool", {})


@pytest.mark.asyncio
async def test_call_tool_missing_query():
    """Test calling execute_sql without a query."""
    with patch.dict(
        os.environ,
        {
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        },
        clear=True,
    ):
        with pytest.raises(ValueError, match="Query is required"):
            await call_tool("execute_sql", {})


@pytest.mark.asyncio
async def test_call_tool_multi_host_missing_connection():
    """Test that multi-host mode requires connection parameter."""
    with patch.dict(
        os.environ,
        {
            "MSSQL_HOST_1_NAME": "x",
            "MSSQL_HOST_1_SERVER": "h",
            "MSSQL_HOST_1_DATABASE": "db",
            "MSSQL_HOST_1_USER": "u",
            "MSSQL_HOST_1_PASSWORD": "p",
        },
        clear=True,
    ):
        with pytest.raises(ValueError, match="'connection' parameter is required"):
            await call_tool("execute_sql", {"query": "SELECT 1"})


@pytest.mark.asyncio
async def test_call_tool_list_connections():
    """Test list_connections tool returns connection info."""
    with patch.dict(
        os.environ,
        {
            "MSSQL_HOST_1_NAME": "prod",
            "MSSQL_HOST_1_SERVER": "prod.host",
            "MSSQL_HOST_1_DATABASE": "proddb",
            "MSSQL_HOST_1_USER": "admin",
            "MSSQL_HOST_1_PASSWORD": "p",
        },
        clear=True,
    ):
        result = await call_tool("list_connections", {})
        assert len(result) == 1
        assert "prod" in result[0].text
        assert "prod.host" in result[0].text


# Skip database-dependent tests if no database connection
@pytest.mark.asyncio
@pytest.mark.skipif(
    not all([pytest.importorskip("pymssql"), pytest.importorskip("mssql_mcp_server")]),
    reason="SQL Server connection not available",
)
async def test_list_resources():
    """Test listing resources (requires database connection)."""
    try:
        resources = await list_resources()
        assert isinstance(resources, list)
    except ValueError as e:
        if "Missing required database configuration" in str(e):
            pytest.skip("Database configuration not available")
        raise
