"""Test database configuration and environment variable handling."""
import pytest
import os
from unittest.mock import patch
from mssql_mcp_server.server import get_db_config, validate_table_name


class TestDatabaseConfiguration:
    """Test database configuration from environment variables."""
    
    def test_default_configuration(self):
        """Test default configuration values."""
        with patch.dict(os.environ, {
            'MSSQL_USER': 'testuser',
            'MSSQL_PASSWORD': 'testpass',
            'MSSQL_DATABASE': 'testdb'
        }, clear=True):
            config = get_db_config()
            assert config['server'] == 'localhost'
            assert config['user'] == 'testuser'
            assert config['password'] == 'testpass'
            assert config['database'] == 'testdb'
            assert 'port' not in config
    
    def test_custom_server_and_port(self):
        """Test custom server and port configuration."""
        with patch.dict(os.environ, {
            'MSSQL_SERVER': 'custom-server.com',
            'MSSQL_PORT': '1433',
            'MSSQL_USER': 'testuser',
            'MSSQL_PASSWORD': 'testpass',
            'MSSQL_DATABASE': 'testdb'
        }):
            config = get_db_config()
            assert config['server'] == 'custom-server.com'
            assert config['port'] == 1433
    
    def test_invalid_port(self):
        """Test invalid port handling."""
        with patch.dict(os.environ, {
            'MSSQL_PORT': 'invalid',
            'MSSQL_USER': 'testuser',
            'MSSQL_PASSWORD': 'testpass',
            'MSSQL_DATABASE': 'testdb'
        }):
            config = get_db_config()
            assert 'port' not in config  # Invalid port should be ignored
    
    def test_azure_sql_configuration(self):
        """Test Azure SQL automatic encryption configuration."""
        with patch.dict(os.environ, {
            'MSSQL_SERVER': 'myserver.database.windows.net',
            'MSSQL_USER': 'testuser',
            'MSSQL_PASSWORD': 'testpass',
            'MSSQL_DATABASE': 'testdb'
        }):
            config = get_db_config()
            assert config['encryption'] == 'require'
            assert config['tds_version'] == '7.4'
            assert ';Encrypt=' not in config['server']
    
    def test_localdb_configuration(self):
        """Test LocalDB connection string conversion."""
        with patch.dict(os.environ, {
            'MSSQL_SERVER': '(localdb)\\MSSQLLocalDB',
            'MSSQL_DATABASE': 'testdb',
            'MSSQL_WINDOWS_AUTH': 'true'
        }):
            config = get_db_config()
            assert config['server'] == '.\\MSSQLLocalDB'
            assert 'user' not in config
            assert 'password' not in config
    
    def test_windows_authentication(self):
        """Test Windows authentication configuration."""
        with patch.dict(os.environ, {
            'MSSQL_SERVER': 'localhost',
            'MSSQL_DATABASE': 'testdb',
            'MSSQL_WINDOWS_AUTH': 'true'
        }):
            config = get_db_config()
            assert 'user' not in config
            assert 'password' not in config
    
    def test_missing_required_config_sql_auth(self):
        """Test missing required configuration for SQL authentication."""
        with patch.dict(os.environ, {
            'MSSQL_SERVER': 'localhost'
        }, clear=True):
            with pytest.raises(ValueError, match="Missing required database configuration"):
                get_db_config()
    
    def test_missing_database_windows_auth(self):
        """Test missing database for Windows authentication."""
        with patch.dict(os.environ, {
            'MSSQL_WINDOWS_AUTH': 'true'
        }, clear=True):
            with pytest.raises(ValueError, match="Missing required database configuration"):
                get_db_config()
    
    def test_encryption_settings(self):
        """Test various encryption settings."""
        # Non-Azure with encryption
        with patch.dict(os.environ, {
            'MSSQL_SERVER': 'localhost',
            'MSSQL_ENCRYPT': 'true',
            'MSSQL_USER': 'testuser',
            'MSSQL_PASSWORD': 'testpass',
            'MSSQL_DATABASE': 'testdb'
        }):
            config = get_db_config()
            assert config['encryption'] == 'require'
            assert config['tds_version'] == '7.4'
        
        # Non-Azure without encryption (default)
        with patch.dict(os.environ, {
            'MSSQL_SERVER': 'localhost',
            'MSSQL_USER': 'testuser',
            'MSSQL_PASSWORD': 'testpass',
            'MSSQL_DATABASE': 'testdb'
        }):
            config = get_db_config()
            assert 'encryption' not in config


class TestTableNameValidation:
    """Test SQL table name validation and escaping."""
    
    def test_valid_table_names(self):
        """Test validation of valid table names."""
        valid_names = [
            'users',
            'UserAccounts',
            'user_accounts',
            'table123',
            'dbo.users',
            'schema_name.table_name'
        ]
        
        for name in valid_names:
            escaped = validate_table_name(name)
            assert escaped is not None
            assert '[' in escaped and ']' in escaped
    
    def test_invalid_table_names(self):
        """Test rejection of invalid table names."""
        invalid_names = [
            'users; DROP TABLE users',  # SQL injection
            'users OR 1=1',              # SQL injection
            'users--',                   # SQL comment
            'users/*comment*/',          # SQL comment
            'users\'',                   # Quote
            'users"',                    # Double quote
            'schema.name.table',         # Too many dots
            'user@table',                # Invalid character
            'user#table',                # Invalid character
            '',                          # Empty
            '.',                         # Just dot
            '..',                        # Double dot
        ]
        
        for name in invalid_names:
            with pytest.raises(ValueError, match="Invalid table name"):
                validate_table_name(name)
    
    def test_table_name_escaping(self):
        """Test proper escaping of table names."""
        assert validate_table_name('users') == '[users]'
        assert validate_table_name('dbo.users') == '[dbo].[users]'
        assert validate_table_name('my_table_123') == '[my_table_123]'