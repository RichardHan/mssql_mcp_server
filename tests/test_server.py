import os
from unittest.mock import patch

import pytest
from pydantic import AnyUrl

from mssql_mcp_server.server import (
    app,
    call_tool,
    get_db_config,
    list_resources,
    list_tools,
    read_resource,
)


# Set test environment variables
@pytest.fixture(autouse=True)
def setup_test_env():
    """Set up test environment variables for all tests."""
    with patch.dict(
        os.environ,
        {
            "MSSQL_SERVER": "test-server",
            "MSSQL_USER": "test-user",
            "MSSQL_PASSWORD": "test-password",
            "MSSQL_DATABASE": "test-db",
            "MSSQL_PORT": "1435",
        },
    ):
        yield


def test_server_initialization():
    """Test that the server initializes correctly."""
    assert app.name == "mssql_mcp_server"


@pytest.mark.asyncio
async def test_list_tools():
    """Test that list_tools returns expected tools."""
    tools = await list_tools()
    assert len(tools) == 1
    assert tools[0].name == "execute_sql"
    assert "query" in tools[0].inputSchema["properties"]


@pytest.mark.asyncio
async def test_call_tool_invalid_name():
    """Test calling a tool with an invalid name."""
    with pytest.raises(ValueError, match="Unknown tool"):
        await call_tool("invalid_tool", {})


@pytest.mark.asyncio
async def test_call_tool_missing_query():
    """Test calling execute_sql without a query."""
    with pytest.raises(ValueError, match="Query is required"):
        await call_tool("execute_sql", {})


@pytest.mark.asyncio
async def test_get_db_config():
    """Test that database configuration is loaded correctly from environment variables."""
    config = get_db_config()
    assert config["server"] == "test-server"
    assert config["user"] == "test-user"
    assert config["password"] == "test-password"
    assert config["database"] == "test-db"
    assert config["port"] == "1435"


@pytest.mark.asyncio
async def test_list_resources(mock_mssql_connection):
    """Test list_resources using mock connection."""
    resources = await list_resources()

    assert len(resources) == 2
    assert resources[0].name == "Table: test_table1"
    assert resources[1].name == "Table: test_table2"
    assert "mssql://" in str(resources[0].uri)


@pytest.mark.asyncio
async def test_read_resource(mock_mssql_connection):
    """Test read_resource using mock connection."""
    result = await read_resource(AnyUrl("mssql://test_table/data"))

    assert "id,name,value" in result
    assert "1,test1,100" in result
    assert "2,test2,200" in result


@pytest.mark.asyncio
async def test_execute_sql_query(mock_mssql_connection):
    """Test executing SQL query using mock connection."""
    result = await call_tool("execute_sql", {"query": "SELECT * FROM test_table"})

    assert len(result) == 1
    assert result[0].type == "text"
    assert "id,name,value" in result[0].text
    assert "1,test1,100" in result[0].text
    assert "2,test2,200" in result[0].text


@pytest.mark.asyncio
async def test_execute_non_select_query(mock_mssql_connection):
    """Test executing non-SELECT SQL query using mock connection."""
    result = await call_tool(
        "execute_sql", {"query": "UPDATE test_table SET value = 0"}
    )

    assert len(result) == 1
    assert result[0].type == "text"
    assert "executed successfully" in result[0].text
    assert "Rows affected: 2" in result[0].text
