import json
import os
import time
from pathlib import Path

import pytest

from mssql_mcp_server.result_export import (
    load_result_file,
    load_stored_result,
    main,
)
from mssql_mcp_server.result_store import ResultStore, ResultStoreConfig
from mssql_mcp_server.serialization import build_result, serialize_json


def store_result(result_dir: Path, envelope: dict) -> dict:
    store = ResultStore(ResultStoreConfig(output_dir=result_dir, ttl_seconds=0))
    payload = serialize_json(envelope).encode("utf-8")
    return store.store(envelope, payload)


def test_file_loader_preserves_unicode_commas_and_crlf(tmp_path):
    envelope = build_result(
        ["key_columns", "definition", "nullable"],
        [["Region, Segment", "SELECT x, y\r\nFROM 表格", None]],
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(serialize_json(envelope), encoding="utf-8")

    assert load_result_file(result_path) == envelope


def test_cli_exports_file_as_compact_json_stdout(tmp_path, capsys):
    envelope = build_result(["name"], [["中文\r\nvalue,with,commas"]])
    result_path = tmp_path / "result.json"
    result_path.write_text(serialize_json(envelope), encoding="utf-8")

    assert main(["--results-json", str(result_path)]) == 0

    assert capsys.readouterr().out == serialize_json(envelope) + "\n"


def test_cli_exports_result_id_using_directory_override(tmp_path, capsys, monkeypatch):
    result_dir = tmp_path / "results"
    envelope = build_result(["id", "value"], [[1, "one,two"]])
    summary = store_result(result_dir, envelope)
    monkeypatch.delenv("MSSQL_RESULT_OUTPUT_DIR", raising=False)

    assert (
        main(
            [
                "--result-id",
                summary["result_id"],
                "--result-dir",
                str(result_dir),
            ]
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == envelope


def test_cli_exports_result_id_using_configured_directory(
    tmp_path, capsys, monkeypatch
):
    result_dir = tmp_path / "results"
    envelope = build_result(["id"], [[1]])
    summary = store_result(result_dir, envelope)
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", str(result_dir))
    monkeypatch.setenv("MSSQL_RESULT_TTL_SECONDS", "0")

    assert main(["--result-id", summary["result_id"]]) == 0

    assert json.loads(capsys.readouterr().out) == envelope


@pytest.mark.parametrize(
    "payload",
    [
        {
            "columns": ["id"],
            "row_count": 1,
            "stored": True,
            "result_id": "0" * 32,
            "preview_rows": [],
            "truncated": False,
        },
        {
            "columns": ["id"],
            "rows": [[1]],
            "total_row_count": 2,
            "result_id": "0" * 32,
            "offset": 0,
            "returned_rows": 1,
            "next_offset": 1,
            "has_more": True,
            "truncated": False,
        },
    ],
)
def test_file_loader_rejects_summary_and_page_payloads(tmp_path, payload):
    result_path = tmp_path / "partial.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical result envelope"):
        load_result_file(result_path)


def test_file_loader_rejects_corrupt_json(tmp_path):
    result_path = tmp_path / "corrupt.json"
    result_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Unable to read result JSON"):
        load_result_file(result_path)


def test_file_loader_rejects_nonstandard_json_constants(tmp_path):
    result_path = tmp_path / "nonstandard.json"
    result_path.write_text(
        '{"columns":["value"],"rows":[[NaN]],"row_count":1,' '"truncated":false}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Non-standard JSON constant"):
        load_result_file(result_path)


def test_stored_loader_rejects_corrupt_result(tmp_path, monkeypatch):
    result_dir = tmp_path / "results"
    envelope = build_result(["id"], [[1]])
    summary = store_result(result_dir, envelope)
    Path(summary["result_path"]).write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("MSSQL_RESULT_TTL_SECONDS", "0")

    with pytest.raises(RuntimeError, match="corrupt"):
        load_stored_result(summary["result_id"], result_dir)


def test_directory_override_honors_configured_ttl(tmp_path, monkeypatch):
    result_dir = tmp_path / "results"
    envelope = build_result(["id"], [[1]])
    summary = store_result(result_dir, envelope)
    old_time = time.time() - 10
    os.utime(summary["result_path"], (old_time, old_time))
    monkeypatch.setenv("MSSQL_RESULT_TTL_SECONDS", "1")

    with pytest.raises(FileNotFoundError, match="expired"):
        load_stored_result(summary["result_id"], result_dir)


def test_stored_loader_reuses_result_id_validation(tmp_path, monkeypatch):
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    monkeypatch.setenv("MSSQL_RESULT_TTL_SECONDS", "0")

    with pytest.raises(ValueError, match="result_id"):
        load_stored_result("../secret", result_dir)


def test_stored_loader_does_not_create_a_missing_source_directory(
    tmp_path, monkeypatch
):
    missing_dir = tmp_path / "missing-results"
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", str(missing_dir))

    with pytest.raises(ValueError, match="Result directory does not exist"):
        load_stored_result("0" * 32)

    assert not missing_dir.exists()


def test_directory_override_takes_precedence_over_configured_directory(
    tmp_path, monkeypatch
):
    result_dir = tmp_path / "override-results"
    envelope = build_result(["source"], [["override"]])
    summary = store_result(result_dir, envelope)
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", "relative/invalid")
    monkeypatch.setenv("MSSQL_RESULT_TTL_SECONDS", "0")

    assert load_stored_result(summary["result_id"], result_dir) == envelope


def test_result_directory_is_only_valid_with_result_id(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(
        serialize_json(build_result(["id"], [[1]])), encoding="utf-8"
    )

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--results-json",
                str(result_path),
                "--result-dir",
                str(tmp_path),
            ]
        )

    assert exc_info.value.code == 2
