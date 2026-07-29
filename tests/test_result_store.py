import base64
import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mssql_mcp_server.result_store import ResultStore, ResultStoreConfig
from mssql_mcp_server.serialization import build_result, serialize_json


def make_store(tmp_path: Path, **overrides) -> ResultStore:
    defaults = {
        "output_dir": tmp_path,
        "max_inline_rows": 2,
        "max_inline_bytes": 1024,
        "preview_rows": 2,
        "preview_bytes": 256,
        "default_page_rows": 2,
        "max_page_rows": 10,
        "max_page_bytes": 1024,
        "max_page_source_bytes": 64 * 1024 * 1024,
        "max_chunk_bytes": 16,
        "ttl_seconds": 3600,
        "max_file_bytes": 1024 * 1024,
        "max_store_bytes": 1024 * 1024,
    }
    defaults.update(overrides)
    return ResultStore(ResultStoreConfig(**defaults))


def persist(store: ResultStore, rows):
    envelope = build_result(["id", "value"], rows)
    payload = serialize_json(envelope).encode("utf-8")
    return envelope, payload, store.store(envelope, payload)


def stored_path(result_dir: Path, summary: dict) -> Path:
    return result_dir / f'{summary["result_id"]}.json'


def test_storage_is_disabled_without_an_output_directory():
    store = ResultStore(ResultStoreConfig())
    payload = b"{}"

    assert store.enabled is False
    assert store.should_store(payload, 1000, force=False) is False
    with pytest.raises(ValueError, match="MSSQL_RESULT_OUTPUT_DIR"):
        store.should_store(payload, 1, force=True)


def test_config_requires_an_absolute_output_directory(monkeypatch):
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", "relative/results")

    with pytest.raises(ValueError, match="absolute path"):
        ResultStoreConfig.from_env()


@pytest.mark.parametrize("max_page_bytes", [-1, 1, 511, True])
def test_config_rejects_invalid_positive_page_byte_caps(max_page_bytes):
    with pytest.raises(ValueError, match="MSSQL_RESULT_MAX_PAGE_BYTES"):
        ResultStoreConfig(max_page_bytes=max_page_bytes)


def test_zero_disables_the_page_byte_cap():
    assert ResultStoreConfig(max_page_bytes=0).max_page_bytes == 0


def test_page_source_limit_defaults_to_64_mib():
    assert ResultStoreConfig().max_page_source_bytes == 64 * 1024 * 1024


@pytest.mark.parametrize("max_page_source_bytes", [-1, True])
def test_config_rejects_invalid_page_source_limits(max_page_source_bytes):
    with pytest.raises(ValueError, match="MSSQL_RESULT_MAX_PAGE_SOURCE_BYTES"):
        ResultStoreConfig(max_page_source_bytes=max_page_source_bytes)


def test_config_rejects_negative_page_source_limit_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("MSSQL_RESULT_MAX_PAGE_SOURCE_BYTES", "-1")

    with pytest.raises(ValueError, match="MSSQL_RESULT_MAX_PAGE_SOURCE_BYTES"):
        ResultStoreConfig.from_env()


def test_zero_page_source_limit_is_preserved_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("MSSQL_RESULT_MAX_PAGE_SOURCE_BYTES", "0")

    assert ResultStoreConfig.from_env().max_page_source_bytes == 0


def test_file_limit_and_path_policy_are_loaded_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("MSSQL_RESULT_MAX_FILE_BYTES", "1234")
    monkeypatch.setenv("MSSQL_RESULT_INCLUDE_PATH", "true")

    config = ResultStoreConfig.from_env()

    assert config.max_file_bytes == 1234
    assert config.include_path is True


def test_invalid_include_path_value_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("MSSQL_RESULT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("MSSQL_RESULT_INCLUDE_PATH", "yes")

    with pytest.raises(ValueError, match="MSSQL_RESULT_INCLUDE_PATH"):
        ResultStoreConfig.from_env()


@pytest.mark.parametrize(
    ("row_count", "payload_size", "expected"),
    [
        (2, 1024, False),
        (3, 1024, True),
        (2, 1025, True),
    ],
)
def test_thresholds_trigger_only_when_exceeded(
    tmp_path, row_count, payload_size, expected
):
    store = make_store(tmp_path)

    assert store.should_store(b"x" * payload_size, row_count, force=False) is expected


def test_stored_file_is_canonical_json_and_summary_is_bounded(tmp_path):
    store = make_store(tmp_path)
    rows = [[1, "Region, Segment"], [2, "中文\r\n多行"], [3, None]]
    envelope, payload, summary = persist(store, rows)

    result_path = stored_path(tmp_path, summary)
    assert result_path.parent == tmp_path.resolve()
    assert result_path.read_bytes() == payload
    assert json.loads(payload) == envelope
    assert summary["columns"] == ["id", "value"]
    assert summary["row_count"] == 3
    assert summary["stored"] is True
    assert summary["byte_count"] == len(payload)
    assert summary["sha256"] == hashlib.sha256(payload).hexdigest()
    assert summary["preview_rows"] == rows[:2]
    assert summary["truncated"] is False
    assert summary["expires_at"] is not None
    assert "rows" not in summary
    assert "result_path" not in summary


def test_result_path_is_returned_only_when_explicitly_enabled(tmp_path):
    store = make_store(tmp_path, include_path=True)

    _, _, summary = persist(store, [[1, "value"]])

    assert summary["result_path"] == str(stored_path(tmp_path, summary))


def test_single_result_limit_is_independent_from_store_quota(tmp_path):
    envelope = build_result(["value"], [["x" * 100]])
    payload = serialize_json(envelope).encode("utf-8")
    store = make_store(
        tmp_path,
        max_file_bytes=len(payload) - 1,
        max_store_bytes=len(payload) * 2,
    )

    with pytest.raises(ValueError, match="MSSQL_RESULT_MAX_FILE_BYTES"):
        store.store(envelope, payload)

    assert list(tmp_path.iterdir()) == []


def test_store_quota_evicts_oldest_result(tmp_path):
    first = build_result(["value"], [["first"]])
    second = build_result(["value"], [["second"]])
    first_payload = serialize_json(first).encode("utf-8")
    second_payload = serialize_json(second).encode("utf-8")
    store = make_store(
        tmp_path,
        max_file_bytes=0,
        max_store_bytes=len(first_payload) + len(second_payload) - 1,
    )
    first_summary = store.store(first, first_payload)
    first_path = stored_path(tmp_path, first_summary)
    os.utime(first_path, (time.time() - 10, time.time() - 10))

    second_summary = store.store(second, second_payload)

    assert not first_path.exists()
    assert stored_path(tmp_path, second_summary).exists()


def test_result_larger_than_store_quota_is_rejected_without_eviction(tmp_path):
    existing = build_result(["value"], [["kept"]])
    existing_payload = serialize_json(existing).encode("utf-8")
    oversized = build_result(["value"], [["x" * 100]])
    oversized_payload = serialize_json(oversized).encode("utf-8")
    store = make_store(
        tmp_path,
        max_file_bytes=0,
        max_store_bytes=len(existing_payload),
    )
    existing_summary = store.store(existing, existing_payload)

    with pytest.raises(ValueError, match="MSSQL_RESULT_MAX_STORE_BYTES"):
        store.store(oversized, oversized_payload)

    assert stored_path(tmp_path, existing_summary).exists()


def test_large_single_cell_is_not_leaked_in_preview(tmp_path):
    store = make_store(tmp_path, preview_bytes=128)
    _, _, summary = persist(store, [[1, "x," * 31_000]])

    assert summary["preview_rows"] == []
    assert len(serialize_json(summary).encode("utf-8")) < 1024


def test_row_pagination_preserves_order_without_overlap(tmp_path):
    store = make_store(tmp_path)
    rows = [[index, f"value-{index}"] for index in range(5)]
    _, _, summary = persist(store, rows)

    first = store.read_page(summary["result_id"], offset=0, limit=2)
    second = store.read_page(summary["result_id"], offset=2, limit=2)
    last = store.read_page(summary["result_id"], offset=4, limit=2)

    assert first["rows"] + second["rows"] + last["rows"] == rows
    assert first["next_offset"] == 2 and first["has_more"] is True
    assert second["next_offset"] == 4 and second["has_more"] is True
    assert last["next_offset"] == 5 and last["has_more"] is False
    assert last["total_row_count"] == 5
    assert last["returned_rows"] == 1
    assert "row_count" not in last


def test_row_page_stays_within_byte_cap(tmp_path):
    store = make_store(tmp_path, max_page_bytes=512)
    rows = [[index, "x" * 180] for index in range(5)]
    _, _, summary = persist(store, rows)

    page = store.read_page(summary["result_id"], offset=0, limit=5)

    assert 0 < page["returned_rows"] < 5
    assert len(serialize_json(page).encode("utf-8")) <= 512


def test_oversized_row_requires_chunks_without_stalling_pagination(tmp_path):
    store = make_store(tmp_path, max_page_bytes=512)
    _, _, summary = persist(store, [[1, "x" * 1000]])

    page = store.read_page(summary["result_id"], offset=0, limit=1)

    assert "rows" not in page
    assert "next_offset" not in page
    assert "has_more" not in page
    assert page["chunk_required"] is True
    assert page["reason"] == "row_exceeds_page_byte_limit"
    assert page["row_index"] == 0
    assert page["total_row_count"] == 1
    assert page["truncated"] is False
    assert len(serialize_json(page).encode("utf-8")) <= 512


def test_oversized_columns_return_a_bounded_chunk_descriptor(tmp_path):
    store = make_store(tmp_path, max_page_bytes=512)
    envelope = build_result(["c" * 1000], [[1]])
    payload = serialize_json(envelope).encode("utf-8")
    summary = store.store(envelope, payload)

    page = store.read_page(summary["result_id"], offset=0, limit=1)

    assert page["chunk_required"] is True
    assert page["reason"] == "columns_exceed_page_byte_limit"
    assert "next_offset" not in page
    assert "has_more" not in page
    assert "columns" not in page
    assert "row_index" not in page
    assert page["total_row_count"] == 1
    assert page["truncated"] is False
    assert len(serialize_json(page).encode("utf-8")) <= 512


def test_page_source_limit_hands_off_before_loading_the_envelope(tmp_path):
    store = make_store(
        tmp_path,
        max_page_bytes=512,
        max_page_source_bytes=64,
    )
    _, payload, summary = persist(store, [[1, "x" * 1000]])
    assert len(payload) > 64

    with patch.object(
        store,
        "_load_envelope",
        side_effect=AssertionError("oversized source must not be loaded"),
    ):
        descriptor = store.read_page(summary["result_id"], offset=0, limit=1)

    assert descriptor == {
        "result_id": summary["result_id"],
        "offset": 0,
        "returned_rows": 0,
        "chunk_required": True,
        "reason": "result_exceeds_page_source_limit",
        "source_bytes": len(payload),
        "max_page_source_bytes": 64,
    }
    assert "total_row_count" not in descriptor
    assert "truncated" not in descriptor
    assert "next_offset" not in descriptor
    assert "has_more" not in descriptor
    assert len(serialize_json(descriptor).encode("utf-8")) <= 512


def test_zero_disables_the_page_source_limit(tmp_path):
    store = make_store(
        tmp_path,
        max_page_bytes=0,
        max_page_source_bytes=0,
    )
    row = [1, "x" * 1000]
    _, _, summary = persist(store, [row])

    page = store.read_page(summary["result_id"], offset=0, limit=1)

    assert page["rows"] == [row]
    assert "chunk_required" not in page


def test_page_size_accounting_is_exact_for_unicode_rows(tmp_path):
    uncapped_store = make_store(tmp_path, max_page_bytes=0)
    rows = [[index, "中文\r\n" * 40] for index in range(3)]
    _, _, summary = persist(uncapped_store, rows)
    full_page = uncapped_store.read_page(summary["result_id"], offset=0, limit=3)
    exact_size = len(serialize_json(full_page).encode("utf-8"))
    assert exact_size >= 512

    exact_store = make_store(tmp_path, max_page_bytes=exact_size)
    exact_page = exact_store.read_page(summary["result_id"], offset=0, limit=3)
    assert exact_page["rows"] == rows
    assert len(serialize_json(exact_page).encode("utf-8")) == exact_size

    smaller_store = make_store(tmp_path, max_page_bytes=exact_size - 1)
    smaller_page = smaller_store.read_page(summary["result_id"], offset=0, limit=3)
    assert smaller_page["returned_rows"] == 2
    assert len(serialize_json(smaller_page).encode("utf-8")) <= exact_size - 1


def test_page_selection_serializes_each_candidate_row_once(tmp_path):
    store = make_store(tmp_path, max_page_bytes=512)
    _, _, summary = persist(store, [[index, "x" * 180] for index in range(5)])
    original_serialize_json = serialize_json
    row_calls = []

    def track_row_serialization(value):
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
            row_calls.append(value[0])
        return original_serialize_json(value)

    with patch(
        "mssql_mcp_server.result_store.serialize_json",
        side_effect=track_row_serialization,
    ):
        page = store.read_page(summary["result_id"], offset=0, limit=5)

    assert page["returned_rows"] == 1
    assert row_calls == [0, 1]


def test_zero_page_cap_returns_an_oversized_row_inline(tmp_path):
    store = make_store(tmp_path, max_page_bytes=0)
    row = [1, "x" * 1000]
    _, _, summary = persist(store, [row])

    page = store.read_page(summary["result_id"], offset=0, limit=1)

    assert page["rows"] == [row]
    assert "chunk_required" not in page


def test_chunk_pagination_reconstructs_full_json(tmp_path):
    store = make_store(tmp_path, max_chunk_bytes=13)
    _, payload, summary = persist(store, [[1, "中文"], [2, None]])
    chunks = []
    offset = 0

    while True:
        chunk = store.read_chunk(
            summary["result_id"], offset_bytes=offset, max_bytes=13
        )
        chunks.append(base64.b64decode(chunk["data_base64"]))
        assert "sha256" not in chunk
        offset = chunk["next_offset"]
        if chunk["eof"]:
            break

    assert b"".join(chunks) == payload
    assert summary["sha256"] == hashlib.sha256(payload).hexdigest()


def test_chunk_reader_does_not_load_the_complete_payload(tmp_path):
    store = make_store(tmp_path, max_chunk_bytes=16)
    _, payload, summary = persist(store, [[1, "x" * 10_000]])

    with patch.object(
        store, "_read_payload", side_effect=AssertionError("full read is forbidden")
    ):
        chunk = store.read_chunk(summary["result_id"], offset_bytes=100, max_bytes=16)

    assert base64.b64decode(chunk["data_base64"]) == payload[100:116]
    assert chunk["returned_bytes"] == 16


def test_chunk_offset_boundaries(tmp_path):
    store = make_store(tmp_path)
    _, payload, summary = persist(store, [[1, "value"]])

    eof = store.read_chunk(summary["result_id"], offset_bytes=len(payload), max_bytes=1)
    assert eof["returned_bytes"] == 0
    assert eof["next_offset"] == len(payload)
    assert eof["eof"] is True

    with pytest.raises(ValueError, match="offset_bytes must not exceed"):
        store.read_chunk(
            summary["result_id"], offset_bytes=len(payload) + 1, max_bytes=1
        )


def test_page_offset_boundaries(tmp_path):
    store = make_store(tmp_path)
    _, _, summary = persist(store, [[1, "value"]])

    terminal = store.read_page(summary["result_id"], offset=1, limit=1)
    assert terminal["rows"] == []
    assert terminal["returned_rows"] == 0
    assert terminal["next_offset"] == 1
    assert terminal["has_more"] is False

    with pytest.raises(ValueError, match="offset must not exceed"):
        store.read_page(summary["result_id"], offset=2, limit=1)


@pytest.mark.parametrize(
    "result_id",
    ["../secret", "A" * 32, "0" * 31, "0" * 33, "C:\\secret"],
)
def test_result_id_rejects_traversal_and_noncanonical_values(tmp_path, result_id):
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="result_id"):
        store.read_page(result_id)


@pytest.mark.parametrize(
    ("offset", "limit"),
    [(-1, 1), (0, 0), (0, -1), (True, 1), (0, True), (0, 11)],
)
def test_page_argument_validation(tmp_path, offset, limit):
    store = make_store(tmp_path)
    _, _, summary = persist(store, [[1, "value"]])

    with pytest.raises(ValueError):
        store.read_page(summary["result_id"], offset=offset, limit=limit)


def test_expired_results_cannot_be_read(tmp_path):
    store = make_store(tmp_path, ttl_seconds=1)
    _, _, summary = persist(store, [[1, "value"]])
    result_path = stored_path(tmp_path, summary)
    old_time = time.time() - 10
    os.utime(result_path, (old_time, old_time))

    with pytest.raises(FileNotFoundError, match="expired"):
        store.read_page(summary["result_id"])


def test_corrupt_result_is_rejected_by_row_reader(tmp_path):
    store = make_store(tmp_path)
    _, _, summary = persist(store, [[1, "value"]])
    stored_path(tmp_path, summary).write_text("not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="corrupt"):
        store.read_page(summary["result_id"])


def test_failed_atomic_replace_does_not_leave_partial_result(tmp_path):
    store = make_store(tmp_path)
    envelope = build_result(["id"], [[1]])
    payload = serialize_json(envelope).encode("utf-8")

    with (
        patch("os.replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        store.store(envelope, payload)

    assert list(tmp_path.iterdir()) == []
