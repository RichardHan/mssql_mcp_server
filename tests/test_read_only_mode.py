"""Tests for read-only vs write mode (MSSQL_ALLOW_WRITES)."""
import os
from unittest.mock import MagicMock, patch

import pytest

from mssql_mcp_server.server import allow_writes, call_tool, is_select_query, list_tools


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

    def test_semicolon_inside_string_allowed(self):
        assert is_select_query("SELECT id FROM t WHERE name = 'a;b'") is True

    def test_union_allowed(self):
        assert is_select_query("(SELECT 1 AS n) UNION ALL (SELECT 2)") is True

    def test_trailing_semicolon_allowed(self):
        assert is_select_query("SELECT 1;") is True

    def test_stacked_select_insert_rejected(self):
        assert is_select_query("SELECT 1; INSERT INTO t VALUES (1)") is False

    def test_stacked_select_drop_rejected(self):
        assert is_select_query("SELECT 1; DROP TABLE t") is False

    def test_select_into_rejected(self):
        assert is_select_query("SELECT * INTO t2 FROM t") is False

    def test_comment_disguised_insert_rejected(self):
        assert is_select_query("/* SELECT */ INSERT INTO t VALUES (1)") is False

    def test_line_comment_before_insert_rejected(self):
        assert is_select_query("-- SELECT\nINSERT INTO t VALUES (1)") is False

    def test_exec_rejected(self):
        assert is_select_query("EXEC sp_who") is False

    def test_declare_batch_rejected(self):
        assert is_select_query("DECLARE @x INT; SELECT 1") is False

    def test_unparseable_rejected(self):
        assert is_select_query("/* unterminated") is False

    def test_update_rejected(self):
        assert is_select_query("UPDATE t SET x = 1") is False

    def test_delete_rejected(self):
        assert is_select_query("DELETE FROM t") is False

    def test_merge_rejected(self):
        assert (
            is_select_query(
                "MERGE t USING s ON 1=1 WHEN MATCHED THEN UPDATE SET x=1;"
            )
            is False
        )


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

    async def test_rejects_stacked_select_insert_without_connect(self):
        """Multi-statement bypass must fail closed before pymssql.connect."""
        env = {
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("mssql_mcp_server.server.pymssql.connect") as mock_connect,
        ):
            result = await call_tool(
                "query",
                {"query": "SELECT 1; INSERT INTO t VALUES (1)"},
            )
            assert "read-only mode" in result[0].text
            mock_connect.assert_not_called()

    async def test_rejects_select_into_without_connect(self):
        env = {
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("mssql_mcp_server.server.pymssql.connect") as mock_connect,
        ):
            result = await call_tool(
                "query",
                {"query": "SELECT * INTO t2 FROM t"},
            )
            assert "read-only mode" in result[0].text
            mock_connect.assert_not_called()

    async def test_allows_select_without_db(self):
        """SELECT passes the read-only gate (DB may fail later)."""
        env = {
            "MSSQL_USER": "u",
            "MSSQL_PASSWORD": "p",
            "MSSQL_DATABASE": "db",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("mssql_mcp_server.server.pymssql.connect") as mock_connect,
        ):
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
        with (
            patch.dict(os.environ, env, clear=True),
            patch("mssql_mcp_server.server.pymssql.connect") as mock_connect,
        ):
            mock_cursor = MagicMock()
            mock_cursor.rowcount = 1
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            result = await call_tool("execute_sql", {"query": "INSERT INTO t VALUES (1)"})
            assert "Rows affected: 1" in result[0].text
            mock_connect.assert_called_once()
