"""Tests for read-only vs write mode (MSSQL_ALLOW_WRITES)."""
import os
import pytest
from unittest.mock import patch, MagicMock
from mssql_mcp_server.server import allow_writes, list_tools, call_tool, is_select_query


class TestAllowWrites:
    def test_default_is_read_only(self):
        with patch.dict(os.environ, {}, clear=True):
            assert allow_writes() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes"])
    def test_truthy_values(self, value):
        with patch.dict(os.environ, {"MSSQL_ALLOW_WRITES": value}, clear=True):
            assert allow_writes() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_falsy_values(self, value):
        with patch.dict(os.environ, {"MSSQL_ALLOW_WRITES": value}, clear=True):
            assert allow_writes() is False


class TestIsSelectQuery:
    def test_select(self):
        assert is_select_query("SELECT * FROM users") is True

    def test_with_cte(self):
        assert is_select_query("WITH cte AS (SELECT 1 AS n) SELECT * FROM cte") is True

    def test_insert(self):
        assert is_select_query("INSERT INTO users VALUES (1)") is False


@pytest.mark.asyncio
class TestListToolsAnnotations:
    async def test_read_only_mode_has_annotation(self):
        with patch.dict(os.environ, {}, clear=True):
            tools = await list_tools()
            assert tools[0].name == "query"
            assert tools[0].annotations is not None
            assert tools[0].annotations.readOnlyHint is True
            assert "read-only" in tools[0].description.lower()

    async def test_write_mode_has_no_read_only_annotation(self):
        with patch.dict(os.environ, {"MSSQL_ALLOW_WRITES": "1"}, clear=True):
            tools = await list_tools()
            assert tools[0].name == "execute_sql"
            assert tools[0].annotations is None
            assert "read-only" not in tools[0].description.lower()


@pytest.mark.asyncio
class TestCallToolReadOnlyGate:
    async def test_rejects_insert_in_read_only_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await call_tool("query", {"query": "INSERT INTO t VALUES (1)"})
            assert "read-only mode" in result[0].text

    async def test_allows_select_without_db(self):
        """SELECT passes the read-only gate (DB may fail later)."""
        env = {
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("mssql_mcp_server.server.pymssql.connect") as mock_connect:
                mock_cursor = MagicMock()
                mock_cursor.description = [("id",)]
                mock_cursor.fetchall.return_value = [(1,)]
                mock_conn = MagicMock()
                mock_conn.cursor.return_value = mock_cursor
                mock_connect.return_value = mock_conn

                result = await call_tool("query", {"query": "SELECT id FROM t"})
                assert "read-only mode" not in result[0].text
                mock_connect.assert_called_once()

    async def test_allows_insert_in_write_mode(self):
        env = {
            "MSSQL_ALLOW_WRITES": "1",
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("mssql_mcp_server.server.pymssql.connect") as mock_connect:
                mock_cursor = MagicMock()
                mock_cursor.rowcount = 1
                mock_conn = MagicMock()
                mock_conn.cursor.return_value = mock_cursor
                mock_connect.return_value = mock_conn

                result = await call_tool("execute_sql", {"query": "INSERT INTO t VALUES (1)"})
                assert "Rows affected: 1" in result[0].text
                mock_connect.assert_called_once()
