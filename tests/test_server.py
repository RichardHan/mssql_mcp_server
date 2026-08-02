import pytest
from unittest.mock import patch
from mssql_mcp_server.server import app, list_tools, list_resources, read_resource, call_tool
from mcp.types import CallToolRequestParams, ReadResourceRequestParams

# call_tool reads get_db_config() first, so a minimal valid config must be present
# for the name/query validation branches to be reached.
_TEST_DB_ENV = {
    "MSSQL_USER": "test",
    "MSSQL_PASSWORD": "test",
    "MSSQL_DATABASE": "testdb",
}

def test_server_initialization():
    """Test that the server initializes correctly."""
    assert app.name == "mssql_mcp_server"

@pytest.mark.asyncio
async def test_list_tools():
    """Test that list_tools returns expected tools."""
    result = await list_tools(None, None)
    assert len(result.tools) == 1
    assert result.tools[0].name == "execute_sql"
    assert "query" in result.tools[0].input_schema["properties"]

@pytest.mark.asyncio
async def test_call_tool_invalid_name():
    """Test calling a tool with an invalid name."""
    with patch.dict("os.environ", _TEST_DB_ENV):
        result = await call_tool(None, CallToolRequestParams(name="invalid_tool", arguments={}))
    assert result.is_error is True
    assert "Unknown tool" in result.content[0].text

@pytest.mark.asyncio
async def test_call_tool_missing_query():
    """Test calling execute_sql without a query."""
    with patch.dict("os.environ", _TEST_DB_ENV):
        result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={}))
    assert result.is_error is True
    assert "Query is required" in result.content[0].text

# Skip database-dependent tests if no database connection
@pytest.mark.asyncio
@pytest.mark.skipif(
    not all([
        pytest.importorskip("pymssql"),
        pytest.importorskip("mssql_mcp_server")
    ]),
    reason="SQL Server connection not available"
)
async def test_list_resources():
    """Test listing resources (requires database connection)."""
    try:
        result = await list_resources(None, None)
        assert isinstance(result.resources, list)
    except ValueError as e:
        if "Missing required database configuration" in str(e):
            pytest.skip("Database configuration not available")
        raise
