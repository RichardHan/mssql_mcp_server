"""Test database configuration and environment variable handling."""

import json
import pytest
import os
import tempfile
from unittest.mock import patch
from mssql_mcp_server.server import (
    get_db_config,
    validate_table_name,
    build_connection_config,
    get_all_connections,
    is_multi_host,
    get_connection_config,
)


class TestDatabaseConfiguration:
    """Test database configuration from environment variables."""

    def test_default_configuration(self):
        """Test default configuration values."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_USER": "testuser",
                "MSSQL_PASSWORD": "testpass",
                "MSSQL_DATABASE": "testdb",
            },
            clear=True,
        ):
            config = get_db_config()
            assert config["server"] == "localhost"
            assert config["user"] == "testuser"
            assert config["password"] == "testpass"
            assert config["database"] == "testdb"
            assert "port" not in config

    def test_custom_server_and_port(self):
        """Test custom server and port configuration."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_SERVER": "custom-server.com",
                "MSSQL_PORT": "1433",
                "MSSQL_USER": "testuser",
                "MSSQL_PASSWORD": "testpass",
                "MSSQL_DATABASE": "testdb",
            },
        ):
            config = get_db_config()
            assert config["server"] == "custom-server.com"
            assert config["port"] == 1433

    def test_invalid_port(self):
        """Test invalid port handling."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_PORT": "invalid",
                "MSSQL_USER": "testuser",
                "MSSQL_PASSWORD": "testpass",
                "MSSQL_DATABASE": "testdb",
            },
        ):
            config = get_db_config()
            assert "port" not in config  # Invalid port should be ignored

    def test_azure_sql_configuration(self):
        """Test Azure SQL automatic encryption configuration."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_SERVER": "myserver.database.windows.net",
                "MSSQL_USER": "testuser",
                "MSSQL_PASSWORD": "testpass",
                "MSSQL_DATABASE": "testdb",
            },
        ):
            config = get_db_config()
            assert config["encrypt"] == True
            assert config["tds_version"] == "7.4"

    def test_localdb_configuration(self):
        """Test LocalDB connection string conversion."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_SERVER": "(localdb)\\MSSQLLocalDB",
                "MSSQL_DATABASE": "testdb",
                "MSSQL_WINDOWS_AUTH": "true",
            },
        ):
            config = get_db_config()
            assert config["server"] == ".\\MSSQLLocalDB"
            assert "user" not in config
            assert "password" not in config

    def test_windows_authentication(self):
        """Test Windows authentication configuration."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_SERVER": "localhost",
                "MSSQL_DATABASE": "testdb",
                "MSSQL_WINDOWS_AUTH": "true",
            },
        ):
            config = get_db_config()
            assert "user" not in config
            assert "password" not in config

    def test_missing_required_config_sql_auth(self):
        """Test missing required configuration for SQL authentication."""
        with patch.dict(os.environ, {"MSSQL_SERVER": "localhost"}, clear=True):
            with pytest.raises(
                ValueError, match="Missing required database configuration"
            ):
                get_db_config()

    def test_missing_database_windows_auth(self):
        """Test missing database for Windows authentication."""
        with patch.dict(os.environ, {"MSSQL_WINDOWS_AUTH": "true"}, clear=True):
            with pytest.raises(
                ValueError, match="Missing required database configuration"
            ):
                get_db_config()

    def test_encryption_settings(self):
        """Test various encryption settings."""
        # Non-Azure with encryption
        with patch.dict(
            os.environ,
            {
                "MSSQL_SERVER": "localhost",
                "MSSQL_ENCRYPT": "true",
                "MSSQL_USER": "testuser",
                "MSSQL_PASSWORD": "testpass",
                "MSSQL_DATABASE": "testdb",
            },
        ):
            config = get_db_config()
            assert config["encrypt"] == True

        # Non-Azure without encryption (default)
        with patch.dict(
            os.environ,
            {
                "MSSQL_SERVER": "localhost",
                "MSSQL_USER": "testuser",
                "MSSQL_PASSWORD": "testpass",
                "MSSQL_DATABASE": "testdb",
            },
        ):
            config = get_db_config()
            assert config["encrypt"] == False


class TestTableNameValidation:
    """Test SQL table name validation and escaping."""

    def test_valid_table_names(self):
        """Test validation of valid table names."""
        valid_names = [
            "users",
            "UserAccounts",
            "user_accounts",
            "table123",
            "dbo.users",
            "schema_name.table_name",
        ]

        for name in valid_names:
            escaped = validate_table_name(name)
            assert escaped is not None
            assert "[" in escaped and "]" in escaped

    def test_invalid_table_names(self):
        """Test rejection of invalid table names."""
        invalid_names = [
            "users; DROP TABLE users",  # SQL injection
            "users OR 1=1",  # SQL injection
            "users--",  # SQL comment
            "users/*comment*/",  # SQL comment
            "users'",  # Quote
            'users"',  # Double quote
            "schema.name.table",  # Too many dots
            "user@table",  # Invalid character
            "user#table",  # Invalid character
            "",  # Empty
            ".",  # Just dot
            "..",  # Double dot
        ]

        for name in invalid_names:
            with pytest.raises(ValueError, match="Invalid table name"):
                validate_table_name(name)

    def test_table_name_escaping(self):
        """Test proper escaping of table names."""
        assert validate_table_name("users") == "[users]"
        assert validate_table_name("dbo.users") == "[dbo].[users]"
        assert validate_table_name("my_table_123") == "[my_table_123]"


class TestBuildConnectionConfig:
    """Test build_connection_config() for individual connection dicts."""

    def test_basic_sql_auth(self):
        """Test basic SQL authentication connection config."""
        cfg = build_connection_config(
            {
                "server": "myhost.example.com",
                "database": "mydb",
                "user": "admin",
                "password": "secret",
            }
        )
        assert cfg["server"] == "myhost.example.com"
        assert cfg["database"] == "mydb"
        assert cfg["user"] == "admin"
        assert cfg["password"] == "secret"

    def test_port_is_parsed(self):
        """Test port is converted to int."""
        cfg = build_connection_config(
            {
                "server": "host",
                "database": "db",
                "user": "u",
                "password": "p",
                "port": "5000",
            }
        )
        assert cfg["port"] == 5000

    def test_invalid_port_ignored(self):
        """Test invalid port values are silently ignored."""
        cfg = build_connection_config(
            {
                "server": "host",
                "database": "db",
                "user": "u",
                "password": "p",
                "port": "not_a_number",
            }
        )
        assert "port" not in cfg

    def test_windows_auth(self):
        """Test Windows authentication removes user/password."""
        cfg = build_connection_config(
            {
                "server": "host",
                "database": "db",
                "windows_auth": "true",
            }
        )
        assert "user" not in cfg
        assert "password" not in cfg
        assert cfg["database"] == "db"

    def test_windows_auth_missing_database(self):
        """Test Windows auth without database raises."""
        with pytest.raises(ValueError, match="Missing required database"):
            build_connection_config(
                {
                    "server": "host",
                    "windows_auth": "true",
                }
            )

    def test_sql_auth_missing_fields(self):
        """Test SQL auth missing user/password/database raises."""
        with pytest.raises(ValueError, match="Missing required database"):
            build_connection_config({"server": "host"})

    def test_azure_sql_encryption(self):
        """Test Azure SQL auto-encryption."""
        cfg = build_connection_config(
            {
                "server": "myserver.database.windows.net",
                "database": "db",
                "user": "u",
                "password": "p",
            }
        )
        assert cfg["tds_version"] == "7.4"
        assert "Encrypt=yes" in cfg["server"]

    def test_explicit_encryption(self):
        """Test explicit encryption flag on non-Azure."""
        cfg = build_connection_config(
            {
                "server": "host",
                "database": "db",
                "user": "u",
                "password": "p",
                "encrypt": "true",
            }
        )
        assert cfg["tds_version"] == "7.4"
        assert "Encrypt=yes" in cfg["server"]

    def test_localdb_conversion(self):
        """Test LocalDB server name conversion."""
        cfg = build_connection_config(
            {
                "server": "(localdb)\\MSSQLLocalDB",
                "database": "db",
                "windows_auth": "true",
            }
        )
        assert cfg["server"] == ".\\MSSQLLocalDB"


class TestGetAllConnections:
    """Test get_all_connections() resolution logic."""

    def test_fallback_to_legacy_env_vars(self):
        """When no multi-host env vars set, falls back to get_db_config()."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_USER": "u",
                "MSSQL_PASSWORD": "p",
                "MSSQL_DATABASE": "db",
            },
            clear=True,
        ):
            conns = get_all_connections()
            assert "default" in conns
            assert conns["default"]["user"] == "u"

    def test_indexed_env_vars_single_host(self):
        """Test MSSQL_HOST_1_* env vars produce one connection."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_NAME": "prod",
                "MSSQL_HOST_1_SERVER": "prod.host",
                "MSSQL_HOST_1_DATABASE": "proddb",
                "MSSQL_HOST_1_USER": "admin",
                "MSSQL_HOST_1_PASSWORD": "s3cret",
            },
            clear=True,
        ):
            conns = get_all_connections()
            assert set(conns.keys()) == {"prod"}
            assert conns["prod"]["server"] == "prod.host"
            assert conns["prod"]["user"] == "admin"

    def test_indexed_env_vars_multiple_hosts(self):
        """Test MSSQL_HOST_1_* + MSSQL_HOST_2_* produce two connections."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_NAME": "prod",
                "MSSQL_HOST_1_SERVER": "prod.host",
                "MSSQL_HOST_1_DATABASE": "proddb",
                "MSSQL_HOST_1_USER": "admin",
                "MSSQL_HOST_1_PASSWORD": "s3cret",
                "MSSQL_HOST_2_NAME": "dev",
                "MSSQL_HOST_2_SERVER": "dev.host",
                "MSSQL_HOST_2_DATABASE": "devdb",
                "MSSQL_HOST_2_USER": "dev",
                "MSSQL_HOST_2_PASSWORD": "devpass",
            },
            clear=True,
        ):
            conns = get_all_connections()
            assert set(conns.keys()) == {"prod", "dev"}
            assert conns["prod"]["server"] == "prod.host"
            assert conns["dev"]["database"] == "devdb"

    def test_indexed_env_vars_non_contiguous_indexes(self):
        """Indexes don't have to be 1,2,3 — gaps are fine."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_5_NAME": "five",
                "MSSQL_HOST_5_SERVER": "h5",
                "MSSQL_HOST_5_DATABASE": "db5",
                "MSSQL_HOST_5_USER": "u",
                "MSSQL_HOST_5_PASSWORD": "p",
                "MSSQL_HOST_10_NAME": "ten",
                "MSSQL_HOST_10_SERVER": "h10",
                "MSSQL_HOST_10_DATABASE": "db10",
                "MSSQL_HOST_10_USER": "u",
                "MSSQL_HOST_10_PASSWORD": "p",
            },
            clear=True,
        ):
            conns = get_all_connections()
            assert set(conns.keys()) == {"five", "ten"}

    def test_indexed_env_vars_with_optional_fields(self):
        """Test port, encrypt, windows_auth are picked up."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_NAME": "custom",
                "MSSQL_HOST_1_SERVER": "host",
                "MSSQL_HOST_1_DATABASE": "db",
                "MSSQL_HOST_1_USER": "u",
                "MSSQL_HOST_1_PASSWORD": "p",
                "MSSQL_HOST_1_PORT": "5000",
                "MSSQL_HOST_1_ENCRYPT": "true",
            },
            clear=True,
        ):
            conns = get_all_connections()
            assert conns["custom"]["port"] == 5000
            assert "Encrypt=yes" in conns["custom"]["server"]

    def test_indexed_env_vars_take_precedence_over_file(self):
        """MSSQL_HOST_* should win when both file and env vars are set."""
        file_payload = [
            {
                "name": "from_file",
                "server": "file.host",
                "database": "db",
                "user": "u",
                "password": "p",
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(file_payload, f)
            f.flush()
            fpath = f.name

        try:
            with patch.dict(
                os.environ,
                {
                    "MSSQL_CONNECTIONS_FILE": fpath,
                    "MSSQL_HOST_1_NAME": "from_env",
                    "MSSQL_HOST_1_SERVER": "env.host",
                    "MSSQL_HOST_1_DATABASE": "db",
                    "MSSQL_HOST_1_USER": "u",
                    "MSSQL_HOST_1_PASSWORD": "p",
                },
                clear=True,
            ):
                conns = get_all_connections()
                assert "from_env" in conns
                assert "from_file" not in conns
        finally:
            os.unlink(fpath)

    def test_file_based_config(self):
        """Test MSSQL_CONNECTIONS_FILE parsing."""
        payload = [
            {
                "name": "staging",
                "server": "staging.host",
                "database": "stgdb",
                "user": "stg",
                "password": "stgpass",
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            f.flush()
            fpath = f.name

        try:
            with patch.dict(
                os.environ,
                {
                    "MSSQL_CONNECTIONS_FILE": fpath,
                },
                clear=True,
            ):
                conns = get_all_connections()
                assert "staging" in conns
                assert conns["staging"]["server"] == "staging.host"
        finally:
            os.unlink(fpath)

    def test_duplicate_connection_names_rejected(self):
        """Duplicate names in indexed env vars should raise."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_NAME": "dup",
                "MSSQL_HOST_1_SERVER": "h1",
                "MSSQL_HOST_1_DATABASE": "db",
                "MSSQL_HOST_1_USER": "u",
                "MSSQL_HOST_1_PASSWORD": "p",
                "MSSQL_HOST_2_NAME": "dup",
                "MSSQL_HOST_2_SERVER": "h2",
                "MSSQL_HOST_2_DATABASE": "db",
                "MSSQL_HOST_2_USER": "u",
                "MSSQL_HOST_2_PASSWORD": "p",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="Duplicate connection name"):
                get_all_connections()

    def test_missing_name_rejected(self):
        """Host entries without a NAME should raise."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_SERVER": "h1",
                "MSSQL_HOST_1_DATABASE": "db",
                "MSSQL_HOST_1_USER": "u",
                "MSSQL_HOST_1_PASSWORD": "p",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="missing a valid 'name'"):
                get_all_connections()


class TestIsMultiHost:
    """Test is_multi_host() detection."""

    def test_single_host(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_multi_host() is False

    def test_multi_host_indexed_env_vars(self):
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_NAME": "x",
                "MSSQL_HOST_1_SERVER": "h",
            },
            clear=True,
        ):
            assert is_multi_host() is True

    def test_multi_host_file(self):
        with patch.dict(
            os.environ, {"MSSQL_CONNECTIONS_FILE": "/some/path"}, clear=True
        ):
            assert is_multi_host() is True

    def test_legacy_env_vars_not_multi_host(self):
        """Plain MSSQL_SERVER/USER/etc. should NOT trigger multi-host."""
        with patch.dict(
            os.environ,
            {
                "MSSQL_SERVER": "localhost",
                "MSSQL_USER": "sa",
                "MSSQL_PASSWORD": "pass",
                "MSSQL_DATABASE": "db",
            },
            clear=True,
        ):
            assert is_multi_host() is False


class TestGetConnectionConfig:
    """Test get_connection_config() name resolution."""

    def test_existing_connection(self):
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_NAME": "alpha",
                "MSSQL_HOST_1_SERVER": "a.host",
                "MSSQL_HOST_1_DATABASE": "adb",
                "MSSQL_HOST_1_USER": "u",
                "MSSQL_HOST_1_PASSWORD": "p",
            },
            clear=True,
        ):
            cfg = get_connection_config("alpha")
            assert cfg["server"] == "a.host"

    def test_unknown_connection_raises(self):
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST_1_NAME": "alpha",
                "MSSQL_HOST_1_SERVER": "a.host",
                "MSSQL_HOST_1_DATABASE": "adb",
                "MSSQL_HOST_1_USER": "u",
                "MSSQL_HOST_1_PASSWORD": "p",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="Unknown connection 'nope'"):
                get_connection_config("nope")
