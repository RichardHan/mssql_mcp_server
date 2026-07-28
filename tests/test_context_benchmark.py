from pathlib import Path
from runpy import run_path

from mssql_mcp_server.serialization import build_result

measure_context = run_path(
    str(Path(__file__).parents[1] / "benchmarks" / "measure_result_context.py")
)["measure_context"]


def test_measure_context_reports_before_and_after_for_240_rows():
    envelope = build_result(
        ["id", "key_columns"],
        [[index, f"Region, Segment,{index}"] for index in range(240)],
    )
    summary = {
        "columns": envelope["columns"],
        "row_count": 240,
        "stored": True,
        "result_id": "0" * 32,
        "result_path": "/var/lib/mssql-results/0.json",
        "preview_rows": [],
        "truncated": False,
    }

    metrics = measure_context(envelope, summary=summary)

    assert metrics["legacy_inline"]["utf8_bytes"] > 5_000
    assert metrics["stored_summary"]["utf8_bytes"] < 300
    assert metrics["savings_vs_legacy_inline"]["percent"] > 90
    assert metrics["savings_vs_json_inline"]["estimated_tokens"] > 1_000


def test_measure_context_reports_large_definition_savings():
    envelope = build_result(["definition"], [["x," * 31_000]])
    summary = {
        "columns": ["definition"],
        "row_count": 1,
        "stored": True,
        "result_id": "1" * 32,
        "result_path": "/var/lib/mssql-results/1.json",
        "preview_rows": [],
        "truncated": False,
    }

    metrics = measure_context(envelope, summary=summary)

    assert metrics["json_inline"]["utf8_bytes"] > 62_000
    assert metrics["stored_summary"]["utf8_bytes"] < 300
    assert metrics["savings_vs_json_inline"]["percent"] > 99


def test_measure_context_rejects_invalid_token_ratio():
    envelope = build_result(["id"], [[1]])

    try:
        measure_context(envelope, bytes_per_token=0)
    except ValueError as exc:
        assert "greater than zero" in str(exc)
    else:
        raise AssertionError("Expected invalid token ratio to fail")
