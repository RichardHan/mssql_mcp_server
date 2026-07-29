"""Measure context-size savings from structured and stored SQL results."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mssql_mcp_server.result_export import load_result_file
from mssql_mcp_server.serialization import serialize_json, serialize_legacy


def _measurement(text: str, bytes_per_token: float) -> dict[str, int]:
    byte_count = len(text.encode("utf-8"))
    return {
        "utf8_bytes": byte_count,
        "estimated_tokens": math.ceil(byte_count / bytes_per_token),
    }


def measure_context(
    envelope: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    bytes_per_token: float = 4.0,
) -> dict[str, Any]:
    """Compare legacy, inline JSON, and optional stored-summary sizes."""
    if bytes_per_token <= 0:
        raise ValueError("bytes_per_token must be greater than zero")
    legacy_text = serialize_legacy(envelope["columns"], envelope["rows"])
    json_text = serialize_json(envelope)
    metrics: dict[str, Any] = {
        "token_estimate": {
            "method": "utf8_bytes_divided_by_bytes_per_token",
            "bytes_per_token": bytes_per_token,
        },
        "legacy_inline": _measurement(legacy_text, bytes_per_token),
        "json_inline": _measurement(json_text, bytes_per_token),
    }
    if summary is not None:
        summary_text = serialize_json(summary)
        summary_metrics = _measurement(summary_text, bytes_per_token)
        metrics["stored_summary"] = summary_metrics
        for baseline in ("legacy_inline", "json_inline"):
            baseline_bytes = metrics[baseline]["utf8_bytes"]
            saved_bytes = baseline_bytes - summary_metrics["utf8_bytes"]
            metrics[f"savings_vs_{baseline}"] = {
                "utf8_bytes": saved_bytes,
                "estimated_tokens": metrics[baseline]["estimated_tokens"]
                - summary_metrics["estimated_tokens"],
                "percent": (
                    round(saved_bytes / baseline_bytes * 100, 2)
                    if baseline_bytes
                    else 0.0
                ),
            }
    return metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure before/after SQL result context size"
    )
    parser.add_argument("--results-json", required=True, type=Path)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--bytes-per-token", type=float, default=4.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    envelope = load_result_file(args.results_json)
    summary = None
    if args.summary_json is not None:
        summary = json.loads(args.summary_json.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ValueError("Summary JSON must contain an object")
    print(
        json.dumps(
            measure_context(
                envelope,
                summary=summary,
                bytes_per_token=args.bytes_per_token,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
