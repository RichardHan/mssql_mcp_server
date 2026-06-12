"""Test error handling, resilience, and recovery scenarios."""
import pytest
import asyncio
from unittest.mock import Mock, patch, PropertyMock
from mssql_mcp_server.server import app, get_db_config
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
                resources = await app.list_resources()
                assert resources == []  # Should return empty list on connection failure
    
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
                resources = await app.list_resources()
                assert resources == []
    
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
                resources = await app.list_resources()
                assert resources == []
    
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
                result = await app.call_tool("query", {"query": "SELECT * FROM users"})
                assert "Error executing query" in result[0].text
                
                # Ensure cleanup attempted
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
                result = await app.call_tool("query", {"query": "SELCT * FROM users"})
                assert "Error executing query" in result[0].text
                assert len(result) == 1
    
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
                result = await app.call_tool("query", {"query": "SELECT * FROM sensitive_table"})
                assert "Error executing query" in result[0].text
    
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
                result = await app.call_tool("query", {
                    "query": "UPDATE users SET status = 'active'"
                })
                assert "Error executing query" in result[0].text


class TestResourceErrors:
    """Test resource access error scenarios."""
    
    @pytest.mark.asyncio
    async def test_invalid_uri_format(self):
        """Test handling of invalid resource URIs."""
        from pydantic import AnyUrl
        
        with patch.dict('os.environ', {
            'MSSQL_USER': 'test',
            'MSSQL_PASSWORD': 'test',
            'MSSQL_DATABASE': 'testdb'
        }):
            # Test invalid URI scheme
            with pytest.raises(ValueError, match="Invalid URI scheme"):
                await app.read_resource(AnyUrl("http://invalid/uri"))
    
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
                from pydantic import AnyUrl
                with pytest.raises(RuntimeError, match="Database error"):
                    await app.read_resource(AnyUrl("mssql://nonexistent/data"))


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
                resources1 = await app.list_resources()
                assert resources1 == []
                
                resources2 = await app.list_resources()
                assert resources2 == []
                
                # Third call should succeed
                resources3 = await app.list_resources()
                assert len(resources3) == 1
    
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
                from pydantic import AnyUrl
                with pytest.raises(RuntimeError):
                    await app.read_resource(AnyUrl("mssql://users/data"))
    
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
                result = await app.call_tool("query", {
                    "query": "SELECT COUNT(*) FROM large_table"
                })
                assert "1" in result[0].text


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
                result = await app.call_tool("query", {"query": "SELECT * FROM users"})
                
                # Cursor should be closed despite error
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
                resources = await app.list_resources()
                assert resources == []
                
                # Connection should still be closed
                mock_conn.close.assert_called()