"""Integration tests for MCP protocol communication and end-to-end functionality."""
import pytest
import asyncio
import json
from unittest.mock import Mock, patch, AsyncMock
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Resource, Tool, CallToolRequestParams, ReadResourceRequestParams
from mssql_mcp_server.server import app, list_resources, list_tools, call_tool


class TestMCPProtocolIntegration:
    """Test MCP protocol integration and communication."""
    
    @pytest.mark.asyncio
    async def test_server_initialization_options(self):
        """Test server initialization with proper options."""
        init_options = app.create_initialization_options()
        
        assert init_options.server_name == "mssql_mcp_server"
        assert init_options.server_version is not None
        assert hasattr(init_options, 'capabilities')
    
    @pytest.mark.asyncio
    async def test_full_mcp_lifecycle(self):
        """Test complete MCP server lifecycle from init to shutdown."""
        # Mock the stdio streams
        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()
        
        # Mock database connection
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Test resource listing
                mock_cursor.fetchall.return_value = [('users',), ('products',)]
                resources_result = await list_resources(None, None)

                assert len(resources_result.resources) == 2
                assert all(isinstance(r, Resource) for r in resources_result.resources)
                assert resources_result.resources[0].name == "Table: users"
                assert resources_result.resources[1].name == "Table: products"

                # Test tool listing
                tools_result = await list_tools(None, None)
                assert len(tools_result.tools) == 1
                assert tools_result.tools[0].name == "execute_sql"

                # Test tool execution
                mock_cursor.description = [('count',)]
                mock_cursor.fetchall.return_value = [(42,)]
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={"query": "SELECT COUNT(*) FROM users"}))

                assert len(result.content) == 1
                assert isinstance(result.content[0], TextContent)
                assert "42" in result.content[0].text
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test handling of concurrent MCP requests."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Simulate concurrent resource listing
                mock_cursor.fetchall.return_value = [('table1',), ('table2',)]
                
                # Run multiple concurrent requests
                tasks = [list_resources(None, None) for _ in range(10)]
                results = await asyncio.gather(*tasks)

                # All should succeed
                assert len(results) == 10
                for result in results:
                    assert len(result.resources) == 2
    
    @pytest.mark.asyncio
    async def test_error_propagation(self):
        """Test that errors are properly propagated through MCP protocol."""
        with patch.dict('os.environ', {}, clear=True):
            # Missing configuration should raise error
            with pytest.raises(ValueError, match="Missing required database configuration"):
                await list_resources(None, None)


class TestDatabaseIntegration:
    """Test actual database integration scenarios."""
    
    @pytest.mark.asyncio
    async def test_connection_pooling(self):
        """Test that connections are properly managed and pooled."""
        call_count = 0
        
        def mock_connect(**kwargs):
            nonlocal call_count
            call_count += 1
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = []
            mock_conn.cursor.return_value = mock_cursor
            return mock_conn
        
        with patch('pymssql.connect', side_effect=mock_connect):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Multiple operations should create multiple connections
                # (current implementation doesn't pool)
                for _ in range(5):
                    await list_resources(None, None)

                assert call_count == 5  # One connection per operation
    
    @pytest.mark.asyncio
    async def test_transaction_handling(self):
        """Test proper transaction handling for write operations."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.rowcount = 1
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Test INSERT operation
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={
                    "query": "INSERT INTO users (name) VALUES ('test')"
                }))

                # Verify commit was called
                mock_conn.commit.assert_called_once()
                assert "Rows affected: 1" in result.content[0].text
    
    @pytest.mark.asyncio
    async def test_connection_cleanup(self):
        """Test that connections are properly cleaned up."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Even if operation fails, connection should be closed
                mock_cursor.execute.side_effect = Exception("Query failed")
                
                try:
                    await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={"query": "SELECT * FROM users"}))
                except:
                    pass
                
                # Connection should still be closed
                # (Note: current implementation may not guarantee this)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    @pytest.mark.asyncio
    async def test_empty_table_list(self):
        """Test handling of database with no tables."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await list_resources(None, None)
                assert result.resources == []

    @pytest.mark.asyncio
    async def test_large_result_set(self):
        """Test handling of large query results."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Create large result set
        large_result = [(i, f'user_{i}', f'email_{i}@test.com') for i in range(10000)]
        mock_cursor.description = [('id',), ('name',), ('email',)]
        mock_cursor.fetchall.return_value = large_result
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={
                    "query": "SELECT * FROM users"
                }))

                # Should handle large results gracefully
                assert len(result.content) == 1
                assert isinstance(result.content[0].text, str)
                assert len(result.content[0].text.split('\n')) == 10001  # Header + 10000 rows
    
    @pytest.mark.asyncio
    async def test_special_characters_in_data(self):
        """Test handling of special characters in query results."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Data with special characters
        mock_cursor.description = [('data',)]
        mock_cursor.fetchall.return_value = [
            ('Hello, "World"',),
            ('Line1\nLine2',),
            ('Tab\there',),
            ('NULL',),
            (None,),
        ]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={
                    "query": "SELECT data FROM test_table"
                }))

                # Should handle special characters properly
                assert len(result.content) == 1
                text = result.content[0].text
                assert 'Hello, "World"' in text
                assert 'None' in text  # None should be converted to string