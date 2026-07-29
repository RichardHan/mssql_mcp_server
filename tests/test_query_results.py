import base64
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    ListResourcesRequest,
    ListToolsRequest,
    ReadResourceRequest,
    ReadResourceRequestParams,
)
from pydantic import AnyUrl

from mssql_mcp_server.serialization import build_result
from mssql_mcp_server.server import (
    GET_RESULT_TOOL,
    READ_RESULT_CHUNK_TOOL,
    app,
    call_tool,
    list_resources,
    list_tools,
    read_resource,
)

DB_ENV = {
    "MSSQL_USER": "test",
    "MSSQL_PASSWORD": "test-password",
    "MSSQL_DATABASE": "testdb",
    "MSSQL_RESULT_FORMAT": "json",
}


def stored_path(result_dir: Path, summary: dict) -> Path:
    return result_dir / f'{summary["result_id"]}.json'


def make_connection(columns, rows):
    cursor = Mock()
    cursor.description = [(column,) for column in columns]
    cursor.fetchall.return_value = rows
    connection = Mock()
    connection.cursor.return_value = cursor
    return connection, cursor


@pytest.mark.asyncio
async def test_call_tool_returns_json_when_configured():
    connection, cursor = make_connection(
        ["key_columns", "definition"],
        [["Region, Segment", "SELECT a, b\r\nFROM 表格"]],
    )

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        content = await call_tool("execute_sql", {"query": "SELECT 1"})

    assert json.loads(content[0].text) == {
        "columns": ["key_columns", "definition"],
        "rows": [["Region, Segment", "SELECT a, b\r\nFROM 表格"]],
        "row_count": 1,
        "truncated": False,
    }
    cursor.close.assert_called_once_with()
    connection.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_call_tool_csv_override_is_legal_csv():
    connection, _ = make_connection(["value"], [["one,two"]])

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        content = await call_tool(
            "execute_sql", {"query": "SELECT 1", "result_format": "csv"}
        )

    assert content[0].text == 'value\n"one,two"'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_format", "expected"),
    [
        ("legacy", "value\none,two"),
        ("csv", 'value\n"one,two"'),
    ],
)
async def test_storage_disabled_legacy_and_csv_skip_canonical_json(
    result_format, expected
):
    connection, _ = make_connection(["value"], [["one,two"]])
    store = Mock(enabled=False)

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
        patch(
            "mssql_mcp_server.server.get_result_store", return_value=store
        ) as get_store,
        patch(
            "mssql_mcp_server.server.build_result",
            side_effect=AssertionError("canonical JSON should not be built"),
        ),
    ):
        content = await call_tool(
            "execute_sql",
            {"query": "SELECT value", "result_format": result_format},
        )

    assert content[0].text == expected
    get_store.assert_called_once_with()
    store.should_store.assert_not_called()


@pytest.mark.asyncio
async def test_storage_disabled_json_builds_canonical_without_checking_thresholds():
    connection, _ = make_connection(["id"], [[1]])
    store = Mock(enabled=False)

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
        patch("mssql_mcp_server.server.get_result_store", return_value=store),
        patch(
            "mssql_mcp_server.server.build_result", wraps=build_result
        ) as build_canonical,
    ):
        content = await call_tool("execute_sql", {"query": "SELECT id"})

    assert json.loads(content[0].text)["rows"] == [[1]]
    build_canonical.assert_called_once_with(["id"], [[1]], truncated=False)
    store.should_store.assert_not_called()


@pytest.mark.asyncio
async def test_call_tool_defaults_to_legacy_output_for_compatibility():
    connection, _ = make_connection(
        ["key_columns", "nullable"], [["Region, Segment", None]]
    )
    legacy_env = {
        key: value for key, value in DB_ENV.items() if key != "MSSQL_RESULT_FORMAT"
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", legacy_env, clear=True),
    ):
        content = await call_tool("execute_sql", {"query": "SELECT 1"})

    assert content[0].text == "key_columns,nullable\nRegion, Segment,None"


@pytest.mark.asyncio
async def test_legacy_table_listing_preserves_original_header():
    connection, _ = make_connection(["TABLE_NAME"], [["users"], ["products"]])
    legacy_env = {
        key: value for key, value in DB_ENV.items() if key != "MSSQL_RESULT_FORMAT"
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", legacy_env, clear=True),
    ):
        content = await call_tool(
            "execute_sql",
            {"query": "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"},
        )

    assert content[0].text == "Tables_in_testdb\nusers\nproducts"


@pytest.mark.asyncio
async def test_resource_and_tool_share_the_same_json_shape():
    tool_connection, _ = make_connection(["id", "value"], [[1, None]])
    resource_connection, _ = make_connection(["id", "value"], [[1, None]])

    with (
        patch("pymssql.connect", side_effect=[tool_connection, resource_connection]),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        tool_content = await call_tool("execute_sql", {"query": "SELECT 1"})
        resource_content = await read_resource(AnyUrl("mssql://users/data"))

    assert json.loads(tool_content[0].text) == json.loads(resource_content)


@pytest.mark.asyncio
async def test_resource_reports_when_top_100_view_is_truncated():
    connection, cursor = make_connection(["id"], [[index] for index in range(101)])

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        resource_content = await read_resource(AnyUrl("mssql://users/data"))

    result = json.loads(resource_content)
    assert result["row_count"] == 100
    assert result["truncated"] is True
    assert result["rows"][-1] == [99]
    cursor.execute.assert_called_once_with("SELECT TOP 101 * FROM [users]")


@pytest.mark.asyncio
@pytest.mark.parametrize("result_format", ["legacy", "csv"])
async def test_resource_preserves_top_100_without_json_or_storage(result_format):
    connection, cursor = make_connection(["id"], [[1]])
    env = {**DB_ENV, "MSSQL_RESULT_FORMAT": result_format}

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        await read_resource(AnyUrl("mssql://users/data"))

    cursor.execute.assert_called_once_with("SELECT TOP 100 * FROM [users]")


@pytest.mark.asyncio
async def test_resource_uses_sentinel_row_when_storage_is_enabled(tmp_path):
    connection, cursor = make_connection(["id"], [[1]])
    env = {
        **DB_ENV,
        "MSSQL_RESULT_FORMAT": "legacy",
        "MSSQL_RESULT_OUTPUT_DIR": str(tmp_path),
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        await read_resource(AnyUrl("mssql://users/data"))

    cursor.execute.assert_called_once_with("SELECT TOP 101 * FROM [users]")


@pytest.mark.asyncio
@pytest.mark.parametrize("result_format", ["legacy", "json", "csv", "invalid"])
async def test_resource_catalog_always_advertises_text_plain(result_format):
    connection, _ = make_connection(["TABLE_NAME"], [["users"]])
    env = {**DB_ENV, "MSSQL_RESULT_FORMAT": result_format}

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
        patch("mssql_mcp_server.server.get_result_store") as get_store,
    ):
        resources = await list_resources()

    assert len(resources) == 1
    assert resources[0].mimeType == "text/plain"
    get_store.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "SELECT id FROM records",
        "WITH data AS (SELECT 1 AS id) SELECT id FROM data",
        "UPDATE records SET active = 1 OUTPUT inserted.id",
        "DELETE FROM records OUTPUT deleted.id",
        "UPDATE records SET active = 1; SELECT id FROM records",
        "EXEC dbo.update_and_get_data",
    ],
    ids=[
        "select",
        "cte",
        "update-output",
        "delete-output",
        "write-select",
        "write-procedure",
    ],
)
async def test_row_returning_statements_fetch_before_committing_once(query):
    connection, cursor = make_connection(["id"], [[1]])
    operations = []

    def fetch_rows():
        operations.append("fetch")
        return [[1]]

    def commit_transaction():
        operations.append("commit")

    cursor.fetchall.side_effect = fetch_rows
    connection.commit.side_effect = commit_transaction

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        content = await call_tool("execute_sql", {"query": query})

    assert json.loads(content[0].text)["rows"] == [[1]]
    assert operations == ["fetch", "commit"]
    cursor.fetchall.assert_called_once_with()
    connection.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_non_query_success_semantics_are_unchanged():
    connection, cursor = make_connection([], [])
    cursor.description = None
    cursor.rowcount = 3

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        content = await call_tool("execute_sql", {"query": "UPDATE t SET value = 1"})

    assert content[0].text == "Query executed successfully. Rows affected: 3"
    connection.commit.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_operation", ["execute", "fetch", "commit"])
async def test_database_operation_errors_raise_as_sql_errors(failing_operation):
    connection, cursor = make_connection([], [])
    cursor.description = [("id",)]
    error = RuntimeError(f"{failing_operation} failed")
    if failing_operation == "execute":
        cursor.execute.side_effect = error
    elif failing_operation == "fetch":
        cursor.fetchall.side_effect = error
    else:
        cursor.fetchall.return_value = [[1]]
        connection.commit.side_effect = error

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
        pytest.raises(
            RuntimeError,
            match=rf"Error executing query: {failing_operation} failed",
        ),
    ):
        await call_tool("execute_sql", {"query": "SELECT nope"})

    if failing_operation in {"execute", "fetch"}:
        connection.commit.assert_not_called()
    else:
        connection.commit.assert_called_once_with()
    cursor.close.assert_called_once_with()
    connection.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_post_processing_errors_are_not_mislabeled_as_sql_errors():
    connection, cursor = make_connection(["id"], [[1]])

    def fail_after_cleanup(*_args, **_kwargs):
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        raise RuntimeError("result post-processing failed")

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
        patch(
            "mssql_mcp_server.server.format_tabular_result",
            side_effect=fail_after_cleanup,
        ),
        pytest.raises(RuntimeError) as exc_info,
    ):
        await call_tool("execute_sql", {"query": "SELECT id FROM records"})

    assert str(exc_info.value) == "result post-processing failed"
    assert "Error executing query" not in str(exc_info.value)
    connection.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_sql_errors_redact_configured_passwords():
    connection, cursor = make_connection([], [])
    cursor.execute.side_effect = RuntimeError(
        "Login failed with password 'test-password'"
    )

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
        pytest.raises(RuntimeError) as exc_info,
    ):
        await call_tool("execute_sql", {"query": "SELECT nope"})

    assert "test-password" not in str(exc_info.value)
    assert "password <redacted>" in str(exc_info.value)


@pytest.mark.asyncio
async def test_protocol_marks_sql_exceptions_as_errors():
    connection, cursor = make_connection([], [])
    cursor.execute.side_effect = RuntimeError("invalid SQL")
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="execute_sql", arguments={"query": "SELECT nope"}
        ),
    )

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        result = await app.request_handlers[CallToolRequest](request)

    assert result.root.isError is True
    assert "Error executing query" in result.root.content[0].text


@pytest.mark.asyncio
async def test_protocol_dispatches_successful_tool_call():
    connection, _ = make_connection(["id", "value"], [[1, "one,two"]])
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="execute_sql", arguments={"query": "SELECT id, value"}
        ),
    )

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        result = await app.request_handlers[CallToolRequest](request)

    assert result.root.isError is False
    assert json.loads(result.root.content[0].text) == {
        "columns": ["id", "value"],
        "rows": [[1, "one,two"]],
        "row_count": 1,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_protocol_dispatches_tool_catalog():
    request = ListToolsRequest(method="tools/list")

    with patch.dict("os.environ", {}, clear=True):
        result = await app.request_handlers[ListToolsRequest](request)

    assert [tool.name for tool in result.root.tools] == ["execute_sql"]


@pytest.mark.asyncio
async def test_protocol_dispatches_resource_catalog():
    connection, _ = make_connection(["TABLE_NAME"], [["users"]])
    request = ListResourcesRequest(method="resources/list")

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        result = await app.request_handlers[ListResourcesRequest](request)

    assert len(result.root.resources) == 1
    assert str(result.root.resources[0].uri) == "mssql://users/data"
    assert result.root.resources[0].mimeType == "text/plain"


@pytest.mark.asyncio
async def test_protocol_dispatches_resource_read_as_text_plain():
    connection, _ = make_connection(["id", "value"], [[1, "one,two"]])
    request = ReadResourceRequest(
        method="resources/read",
        params=ReadResourceRequestParams(uri=AnyUrl("mssql://users/data")),
    )

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
    ):
        result = await app.request_handlers[ReadResourceRequest](request)

    assert len(result.root.contents) == 1
    assert result.root.contents[0].mimeType == "text/plain"
    assert json.loads(result.root.contents[0].text) == {
        "columns": ["id", "value"],
        "rows": [[1, "one,two"]],
        "row_count": 1,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_tool_schema_advertises_optional_result_format():
    with patch.dict("os.environ", {}, clear=True):
        tools = await list_tools()

    schema = tools[0].inputSchema
    assert schema["properties"]["result_format"]["enum"] == [
        "legacy",
        "json",
        "csv",
    ]
    assert schema["properties"]["store_result"]["type"] == "boolean"
    assert schema["required"] == ["query"]
    assert len(tools) == 1


@pytest.mark.asyncio
async def test_store_using_handlers_resolve_one_store_per_invocation():
    store = Mock(enabled=False)
    store.read_page.return_value = {"rows": []}
    store.read_chunk.return_value = {"data_base64": "", "eof": True}
    resource_connection, _ = make_connection(["id"], [[1]])
    legacy_env = {
        key: value for key, value in DB_ENV.items() if key != "MSSQL_RESULT_FORMAT"
    }

    with (
        patch("pymssql.connect", return_value=resource_connection),
        patch.dict("os.environ", legacy_env, clear=True),
        patch(
            "mssql_mcp_server.server.get_result_store", return_value=store
        ) as get_store,
    ):
        await list_tools()
        get_store.assert_called_once_with()
        get_store.reset_mock()

        await read_resource(AnyUrl("mssql://users/data"))
        get_store.assert_called_once_with()
        get_store.reset_mock()

        await call_tool(GET_RESULT_TOOL, {"result_id": "0" * 32})
        get_store.assert_called_once_with()
        get_store.reset_mock()

        await call_tool(READ_RESULT_CHUNK_TOOL, {"result_id": "0" * 32})
        get_store.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("result_format", ["legacy", "csv"])
async def test_storage_enabled_formats_use_canonical_json_threshold(
    tmp_path, result_format
):
    rows = [[1], [2], [3]]
    connection, _ = make_connection(["id"], rows)
    env = {
        **DB_ENV,
        "MSSQL_RESULT_FORMAT": result_format,
        "MSSQL_RESULT_OUTPUT_DIR": str(tmp_path),
        "MSSQL_RESULT_MAX_INLINE_ROWS": "2",
        "MSSQL_RESULT_MAX_INLINE_BYTES": "0",
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        content = await call_tool("execute_sql", {"query": "SELECT id"})

    summary = json.loads(content[0].text)
    assert summary["stored"] is True
    assert "result_path" not in summary
    stored = json.loads(stored_path(tmp_path, summary).read_text(encoding="utf-8"))
    assert stored["rows"] == rows


@pytest.mark.asyncio
async def test_240_rows_are_stored_instead_of_returned_inline(tmp_path):
    rows = [[index, f"key,{index}"] for index in range(240)]
    connection, _ = make_connection(["id", "key_columns"], rows)
    env = {
        **DB_ENV,
        "MSSQL_RESULT_OUTPUT_DIR": str(tmp_path),
        "MSSQL_RESULT_MAX_INLINE_ROWS": "200",
        "MSSQL_RESULT_MAX_INLINE_BYTES": "0",
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        content = await call_tool("execute_sql", {"query": "SELECT many rows"})

    summary = json.loads(content[0].text)
    assert summary["stored"] is True
    assert summary["row_count"] == 240
    assert "rows" not in summary
    assert "result_path" not in summary
    stored = json.loads(stored_path(tmp_path, summary).read_text(encoding="utf-8"))
    assert stored["rows"] == rows
    assert len(content[0].text.encode("utf-8")) < len(
        json.dumps(stored, ensure_ascii=False).encode("utf-8")
    )


@pytest.mark.asyncio
async def test_62kb_definition_is_stored_without_preview_leak(tmp_path):
    definition = "SELECT x, y\r\n" + ("x," * 31_000)
    connection, _ = make_connection(["definition"], [[definition]])
    env = {
        **DB_ENV,
        "MSSQL_RESULT_OUTPUT_DIR": str(tmp_path),
        "MSSQL_RESULT_MAX_INLINE_ROWS": "0",
        "MSSQL_RESULT_MAX_INLINE_BYTES": "32768",
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        content = await call_tool("execute_sql", {"query": "SELECT definition"})

    summary = json.loads(content[0].text)
    assert summary["stored"] is True
    assert summary["preview_rows"] == []
    assert definition not in content[0].text
    stored = json.loads(stored_path(tmp_path, summary).read_text(encoding="utf-8"))
    assert stored["rows"] == [[definition]]


@pytest.mark.asyncio
async def test_stored_result_path_can_be_explicitly_enabled(tmp_path):
    connection, _ = make_connection(["id"], [[1]])
    env = {
        **DB_ENV,
        "MSSQL_RESULT_OUTPUT_DIR": str(tmp_path),
        "MSSQL_RESULT_INCLUDE_PATH": "true",
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        content = await call_tool(
            "execute_sql", {"query": "SELECT id", "store_result": True}
        )

    summary = json.loads(content[0].text)
    assert summary["result_path"] == str(stored_path(tmp_path, summary))


@pytest.mark.asyncio
async def test_storage_tools_are_advertised_only_when_enabled(tmp_path):
    with patch.dict(
        "os.environ", {"MSSQL_RESULT_OUTPUT_DIR": str(tmp_path)}, clear=True
    ):
        tools = await list_tools()

    assert [tool.name for tool in tools] == [
        "execute_sql",
        GET_RESULT_TOOL,
        READ_RESULT_CHUNK_TOOL,
    ]


@pytest.mark.asyncio
async def test_stored_result_tools_page_and_reconstruct_payload(tmp_path):
    rows = [[index, f"value-{index}"] for index in range(3)]
    connection, _ = make_connection(["id", "value"], rows)
    env = {
        **DB_ENV,
        "MSSQL_RESULT_OUTPUT_DIR": str(tmp_path),
        "MSSQL_RESULT_MAX_INLINE_ROWS": "0",
        "MSSQL_RESULT_MAX_INLINE_BYTES": "0",
    }

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        stored_content = await call_tool(
            "execute_sql", {"query": "SELECT rows", "store_result": True}
        )
        summary = json.loads(stored_content[0].text)
        page_content = await call_tool(
            GET_RESULT_TOOL,
            {"result_id": summary["result_id"], "offset": 1, "limit": 2},
        )
        chunk_content = await call_tool(
            READ_RESULT_CHUNK_TOOL,
            {"result_id": summary["result_id"], "max_bytes": 16_384},
        )

    page = json.loads(page_content[0].text)
    assert page["rows"] == rows[1:]
    assert page["total_row_count"] == 3
    assert page["returned_rows"] == 2
    assert "row_count" not in page
    assert page["has_more"] is False
    chunk = json.loads(chunk_content[0].text)
    assert json.loads(base64.b64decode(chunk["data_base64"]))["rows"] == rows


@pytest.mark.asyncio
async def test_protocol_dispatches_complete_stored_result_workflow(tmp_path):
    rows = [[index, f"value-{index}"] for index in range(3)]
    connection, _ = make_connection(["id", "value"], rows)
    env = {
        **DB_ENV,
        "MSSQL_RESULT_OUTPUT_DIR": str(tmp_path),
        "MSSQL_RESULT_TTL_SECONDS": "0",
    }
    execute_request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="execute_sql",
            arguments={"query": "SELECT rows", "store_result": True},
        ),
    )

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", env, clear=True),
    ):
        execute_result = await app.request_handlers[CallToolRequest](execute_request)
        summary = json.loads(execute_result.root.content[0].text)

        tools_result = await app.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )

        page_result = await app.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name=GET_RESULT_TOOL,
                    arguments={
                        "result_id": summary["result_id"],
                        "offset": 1,
                        "limit": 2,
                    },
                ),
            )
        )
        page = json.loads(page_result.root.content[0].text)

        chunk_result = await app.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name=READ_RESULT_CHUNK_TOOL,
                    arguments={
                        "result_id": summary["result_id"],
                        "max_bytes": 16_384,
                    },
                ),
            )
        )
        chunk = json.loads(chunk_result.root.content[0].text)

    assert execute_result.root.isError is False
    assert summary["stored"] is True
    assert [tool.name for tool in tools_result.root.tools] == [
        "execute_sql",
        GET_RESULT_TOOL,
        READ_RESULT_CHUNK_TOOL,
    ]
    assert page_result.root.isError is False
    assert page["rows"] == rows[1:]
    assert page["total_row_count"] == 3
    assert "row_count" not in page
    assert chunk_result.root.isError is False
    assert chunk["eof"] is True
    assert "sha256" not in chunk
    assert json.loads(base64.b64decode(chunk["data_base64"])) == {
        "columns": ["id", "value"],
        "rows": rows,
        "row_count": 3,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_force_storage_requires_configured_output_directory():
    connection, _ = make_connection(["id"], [[1]])

    with (
        patch("pymssql.connect", return_value=connection),
        patch.dict("os.environ", DB_ENV, clear=True),
        pytest.raises(ValueError, match="MSSQL_RESULT_OUTPUT_DIR"),
    ):
        await call_tool("execute_sql", {"query": "SELECT 1", "store_result": True})
