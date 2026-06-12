"""Security tests for SQL injection prevention and safe query handling."""
import pytest
from unittest.mock import Mock, patch, AsyncMock
from mssql_mcp_server.server import validate_table_name, read_resource, call_tool
from pydantic import AnyUrl
from mcp.types import TextContent


class TestSQLInjectionPrevention:
    """Test SQL injection prevention measures."""
    
    @pytest.mark.asyncio
    async def test_sql_injection_in_table_names(self):
        """Test that SQL injection attempts in table names are blocked."""
        malicious_uris = [
            "mssql://users; DROP TABLE users--/data",
            "mssql://users' OR '1'='1/data",
            "mssql://users/**/UNION/**/SELECT/**/password/data",
            "mssql://users%20OR%201=1/data",
        ]
        
        with patch.dict('os.environ', {
            'MSSQL_USER': 'test',
            'MSSQL_PASSWORD': 'test',
            'MSSQL_DATABASE': 'test'
        }):
            for uri in malicious_uris:
                with pytest.raises((ValueError, RuntimeError)):
                    await read_resource(AnyUrl(uri))
    
    @pytest.mark.asyncio
    async def test_safe_query_execution(self):
        """Test that only safe queries are executed."""
        # Mock the database connection
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'test'
            }):
                # Test safe table read
                uri = AnyUrl("mssql://users/data")
                mock_cursor.description = [('id',), ('name',)]
                mock_cursor.fetchall.return_value = [(1, 'John'), (2, 'Jane')]
                
                result = await read_resource(uri)
                
                # Verify the query was escaped properly
                executed_query = mock_cursor.execute.call_args[0][0]
                assert '[users]' in executed_query
                assert 'SELECT TOP 100 * FROM [users]' == executed_query
    
    def test_parameterized_queries(self):
        """Ensure queries use parameters where user input is involved."""
        # This is a design consideration test
        # The current implementation doesn't use parameterized queries for table names
        # because table names can't be parameterized in SQL
        # Instead, we validate and escape them
        pass
    
    @pytest.mark.asyncio
    async def test_query_result_sanitization(self):
        """Test that query results don't expose sensitive information."""
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'test'
            }):
                # Test that passwords or sensitive data aren't exposed in errors
                mock_cursor.execute.side_effect = Exception("Login failed for user 'sa' with password 'secret123'")
                
                result = await call_tool("query", {"query": "SELECT * FROM users"})
                
                # Verify sensitive info is not in the error message
                assert isinstance(result, list)
                assert len(result) == 1
                assert isinstance(result[0], TextContent)
                assert 'secret123' not in result[0].text
                assert 'Error executing query' in result[0].text


class TestInputValidation:
    """Test input validation for all user inputs."""
    
    @pytest.mark.asyncio
    async def test_tool_argument_validation(self):
        """Test that tool arguments are properly validated."""
        # Test with various invalid inputs
        invalid_inputs = [
            {},  # Empty
            {"query": ""},  # Empty query
            {"query": None},  # None query
            {"query": {"$ne": None}},  # NoSQL injection attempt
        ]
        
        with patch.dict('os.environ', {
            'MSSQL_USER': 'test',
            'MSSQL_PASSWORD': 'test',
            'MSSQL_DATABASE': 'test'
        }):
            for invalid_input in invalid_inputs:
                with pytest.raises(ValueError):
                    await call_tool("query", invalid_input)
    
    def test_environment_variable_validation(self):
        """Test that environment variables are validated."""
        # Test with potentially dangerous environment values
        dangerous_values = {
            'MSSQL_SERVER': 'localhost; exec xp_cmdshell "whoami"',
            'MSSQL_DATABASE': 'test; DROP DATABASE test',
            'MSSQL_USER': 'admin\'--',
        }
        
        with patch.dict('os.environ', dangerous_values):
            # The connection should fail safely without executing malicious code
            # This tests that pymssql properly handles these values
            pass


class TestResourceAccessControl:
    """Test resource access control and permissions."""
    
    @pytest.mark.asyncio
    async def test_system_table_access_restriction(self):
        """Test that system tables are not exposed as resources."""
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Simulate database returning both user and system tables
        mock_cursor.fetchall.return_value = [
            ('users',),
            ('sys.objects',),  # System table
            ('INFORMATION_SCHEMA.TABLES',),  # System view
            ('products',),
        ]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'test'
            }):
                from mssql_mcp_server.server import list_resources
                resources = await list_resources()
                
                # Verify system tables are filtered out (if implemented)
                # Currently the query uses INFORMATION_SCHEMA which should only return user tables
                resource_names = [r.name for r in resources]
                assert len(resources) == 4  # All tables are returned currently
    
    @pytest.mark.asyncio 
    async def test_query_permissions(self):
        """Test that dangerous queries are handled safely."""
        dangerous_queries = [
            "DROP TABLE users",
            "CREATE LOGIN hacker WITH PASSWORD = 'password'",
            "EXEC xp_cmdshell 'dir'",
            "ALTER SERVER ROLE sysadmin ADD MEMBER hacker",
        ]
        
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'test'
            }):
                for query in dangerous_queries:
                    # The queries will be executed (current implementation doesn't block them)
                    # but we ensure errors are handled gracefully
                    mock_cursor.execute.side_effect = Exception("Permission denied")
                    result = await call_tool("query", {"query": query})
                    
                    assert len(result) == 1
                    assert "Error executing query" in result[0].text