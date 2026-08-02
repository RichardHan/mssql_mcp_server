"""Test error handling, resilience, and recovery scenarios."""
import pytest
import asyncio
from unittest.mock import Mock, patch, PropertyMock
from mssql_mcp_server.server import app, get_db_config, list_resources, read_resource, call_tool
from mcp.types import CallToolRequestParams, ReadResourceRequestParams
import pymssql


class TestConnectionErrors:
    """Test various connection error scenarios."""
    
    @pytest.mark.asyncio
    async def test_connection_timeout(self):
        """Test handling of connection timeouts."""
        with patch('pymssql.connect') as mock_connect:
            mock_connect.side_effect = pymssql.OperationalError("Connection timeout")
            
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await list_resources(None, None)
                assert result.resources == []  # Should return empty list on connection failure
    
    @pytest.mark.asyncio
    async def test_authentication_failure(self):
        """Test handling of authentication failures."""
        with patch('pymssql.connect') as mock_connect:
            mock_connect.side_effect = pymssql.OperationalError("Login failed for user 'test'")
            
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'wrong_password',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await list_resources(None, None)
                assert result.resources == []

    @pytest.mark.asyncio
    async def test_database_not_found(self):
        """Test handling when database doesn't exist."""
        with patch('pymssql.connect') as mock_connect:
            mock_connect.side_effect = pymssql.OperationalError("Database 'nonexistent' does not exist")
            
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'nonexistent'
            }):
                result = await list_resources(None, None)
                assert result.resources == []

    @pytest.mark.asyncio
    async def test_network_disconnection(self):
        """Test handling of network disconnections during query."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Simulate network error during query execution
        mock_cursor.execute.side_effect = pymssql.OperationalError("Network error")
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={"query": "SELECT * FROM users"}))
                # v2 catches the DB error and returns it as an error result
                # rather than raising.
                assert result.is_error is True
                assert "Error executing query" in result.content[0].text

                # Ensure cleanup attempted even on error (try/finally in server).
                mock_cursor.close.assert_called()
                mock_conn.close.assert_called()


class TestQueryErrors:
    """Test various query execution error scenarios."""
    
    @pytest.mark.asyncio
    async def test_syntax_error(self):
        """Test handling of SQL syntax errors."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.execute.side_effect = pymssql.ProgrammingError("Incorrect syntax near 'SELCT'")
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={"query": "SELCT * FROM users"}))
                assert "Error executing query" in result.content[0].text
                assert len(result.content) == 1
    
    @pytest.mark.asyncio
    async def test_permission_denied(self):
        """Test handling of permission denied errors."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.execute.side_effect = pymssql.DatabaseError("The SELECT permission was denied")
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={"query": "SELECT * FROM sensitive_table"}))
                assert "Error executing query" in result.content[0].text
    
    @pytest.mark.asyncio
    async def test_deadlock_handling(self):
        """Test handling of database deadlocks."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.execute.side_effect = pymssql.OperationalError("Transaction was deadlocked")
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={
                    "query": "UPDATE users SET status = 'active'"
                }))
                assert "Error executing query" in result.content[0].text


class TestResourceErrors:
    """Test resource access error scenarios."""
    
    @pytest.mark.asyncio
    async def test_invalid_uri_format(self):
        """Test handling of invalid resource URIs."""
        with patch.dict('os.environ', {
            'MSSQL_USER': 'test',
            'MSSQL_PASSWORD': 'test',
            'MSSQL_DATABASE': 'testdb'
        }):
            # Test invalid URI scheme
            with pytest.raises(ValueError, match="Invalid URI scheme"):
                await read_resource(None, ReadResourceRequestParams(uri="http://invalid/uri"))
    
    @pytest.mark.asyncio
    async def test_table_not_found(self):
        """Test handling when requested table doesn't exist."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.execute.side_effect = pymssql.ProgrammingError("Invalid object name 'nonexistent'")
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await read_resource(None, ReadResourceRequestParams(uri="mssql://nonexistent/data"))
                assert "Error reading resource" in result.contents[0].text


class TestRecoveryScenarios:
    """Test recovery and resilience scenarios."""
    
    @pytest.mark.asyncio
    async def test_connection_retry_logic(self):
        """Test that connection failures don't crash the server."""
        attempt_count = 0
        
        def mock_connect(**kwargs):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise pymssql.OperationalError("Connection failed")
            # Success on third attempt
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [('users',)]
            mock_conn.cursor.return_value = mock_cursor
            return mock_conn
        
        with patch('pymssql.connect', side_effect=mock_connect):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # First two calls should fail
                result1 = await list_resources(None, None)
                assert result1.resources == []

                result2 = await list_resources(None, None)
                assert result2.resources == []

                # Third call should succeed
                result3 = await list_resources(None, None)
                assert len(result3.resources) == 1
    
    @pytest.mark.asyncio
    async def test_partial_result_handling(self):
        """Test handling when cursor fails mid-iteration."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Simulate cursor failing during iteration
        def failing_fetchall():
            raise pymssql.OperationalError("Connection lost during query")
        
        mock_cursor.execute.return_value = None
        mock_cursor.fetchall = failing_fetchall
        mock_cursor.description = [('id',), ('name',)]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Should handle the error gracefully
                result = await read_resource(None, ReadResourceRequestParams(uri="mssql://users/data"))
                assert "Error reading resource" in result.contents[0].text
    
    @pytest.mark.asyncio
    async def test_long_running_query_handling(self):
        """Test handling of long-running queries."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        async def slow_execute(query):
            await asyncio.sleep(0.1)  # Simulate slow query
            return None
        
        mock_cursor.execute = Mock(side_effect=lambda q: None)
        mock_cursor.fetchall.return_value = [(1,)]
        mock_cursor.description = [('count',)]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Should complete without timeout
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={
                    "query": "SELECT COUNT(*) FROM large_table"
                }))
                assert "1" in result.content[0].text


class TestMemoryAndResourceManagement:
    """Test memory and resource leak prevention."""
    
    @pytest.mark.asyncio
    async def test_cursor_cleanup_on_error(self):
        """Ensure cursors are closed even on errors."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.execute.side_effect = Exception("Unexpected error")
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await call_tool(None, CallToolRequestParams(name="execute_sql", arguments={"query": "SELECT * FROM users"}))

                # v2 catches the error and returns an error result instead of
                # raising, so the failure is surfaced as content.
                assert result.is_error is True
                assert "Error executing query" in result.content[0].text

                # Cursor and connection should be closed despite the error.
                mock_cursor.close.assert_called()
                mock_conn.close.assert_called()

    @pytest.mark.asyncio
    async def test_connection_cleanup_on_exception(self):
        """Ensure connections are closed on exceptions."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Make cursor creation fail after connection
        mock_conn.cursor.side_effect = Exception("Cursor creation failed")
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                result = await list_resources(None, None)
                # v2 catches the error and returns an empty resource list rather
                # than raising.
                assert result.resources == []

                # Connection should still be closed even though cursor creation failed.
                mock_conn.close.assert_called()