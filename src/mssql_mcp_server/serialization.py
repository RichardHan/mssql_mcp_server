"""Serialization helpers for SQL Server result sets."""

from __future__ import annotations

import base64
import csv
import json
import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from io import StringIO
from typing import Any
from uuid import UUID


class ResultFormat(str, Enum):
    """Supported wire formats for tabular query results."""

    LEGACY = "legacy"
    JSON = "json"
    CSV = "csv"

    @classmethod
    def parse(cls, value: str | None, *, default: str = "legacy") -> ResultFormat:
        candidate = (default if value is None else value).strip().lower()
        try:
            return cls(candidate)
        except ValueError as exc:
            supported = ", ".join(item.value for item in cls)
            raise ValueError(
                f"Unsupported result format '{candidate}'. Expected one of: {supported}"
            ) from exc


def normalize_value(value: Any) -> Any:
    """Convert database values into stable JSON-compatible values."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        encoded = base64.b64encode(bytes(value)).decode("ascii")
        return f"base64:{encoded}"
    if isinstance(value, Enum):
        return normalize_value(value.value)
    return str(value)


def build_result(
    columns: Sequence[str], rows: Iterable[Sequence[Any]], *, truncated: bool = False
) -> dict[str, Any]:
    """Build the compact column-array/row-array result envelope."""
    normalized_rows = [[normalize_value(value) for value in row] for row in rows]
    return {
        "columns": list(columns),
        "rows": normalized_rows,
        "row_count": len(normalized_rows),
        "truncated": truncated,
    }


def serialize_json(result: Any) -> str:
    """Serialize a result envelope as compact, lossless Unicode JSON."""
    return json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def serialize_csv(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Serialize rows as RFC 4180-style CSV using Python's csv module."""
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(
        ["" if value is None else normalize_value(value) for value in row]
        for row in rows
    )
    return output.getvalue().rstrip("\n")


def serialize_legacy(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Reproduce the original delimiter-joined output for compatibility."""
    result_rows = [",".join(map(str, row)) for row in rows]
    return "\n".join([",".join(columns), *result_rows])


def serialize_result(
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    result_format: ResultFormat,
) -> str:
    """Serialize a materialized result set in the requested wire format."""
    materialized_rows = list(rows)
    if result_format is ResultFormat.LEGACY:
        return serialize_legacy(columns, materialized_rows)
    return serialize_envelope(build_result(columns, materialized_rows), result_format)


def serialize_envelope(result: dict[str, Any], result_format: ResultFormat) -> str:
    """Serialize an existing result envelope in the requested wire format."""
    if result_format is ResultFormat.LEGACY:
        return serialize_legacy(result["columns"], result["rows"])
    if result_format is ResultFormat.CSV:
        return serialize_csv(result["columns"], result["rows"])
    return serialize_json(result)
