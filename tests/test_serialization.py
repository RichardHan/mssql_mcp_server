import csv
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from io import StringIO
from uuid import UUID

import pytest

from mssql_mcp_server.serialization import (
    ResultFormat,
    build_result,
    normalize_value,
    serialize_result,
)


def test_json_preserves_delimiters_multiline_unicode_and_null():
    text = serialize_result(
        ["key_columns", "definition", "empty", "nullable"],
        [["Region, Segment", 'SELECT "quoted"\r\nFROM 表格\t欄', "", None]],
        ResultFormat.JSON,
    )

    parsed = json.loads(text)

    assert parsed == {
        "columns": ["key_columns", "definition", "empty", "nullable"],
        "rows": [["Region, Segment", 'SELECT "quoted"\r\nFROM 表格\t欄', "", None]],
        "row_count": 1,
        "truncated": False,
    }
    assert "表格" in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("123.4500"), "123.4500"),
        (date(2026, 7, 28), "2026-07-28"),
        (time(13, 14, 15, 123456), "13:14:15.123456"),
        (
            datetime(2026, 7, 28, 13, 14, 15, tzinfo=UTC),
            "2026-07-28T13:14:15+00:00",
        ),
        (
            UUID("12345678-1234-5678-1234-567812345678"),
            "12345678-1234-5678-1234-567812345678",
        ),
        (b"\x00\xff", "base64:AP8="),
        (memoryview(b"abc"), "base64:YWJj"),
    ],
)
def test_normalize_non_native_json_types(value, expected):
    assert normalize_value(value) == expected


@pytest.mark.parametrize("rows", [[], [[1]], [[1], [2], [3]]])
def test_result_envelope_counts_zero_one_and_many_rows(rows):
    result = build_result(["id"], rows)

    assert result["rows"] == rows
    assert result["row_count"] == len(rows)
    assert result["truncated"] is False


def test_native_numbers_and_booleans_remain_typed():
    result = build_result(["integer", "float", "boolean"], [[10, 1.25, True]])

    assert result["rows"] == [[10, 1.25, True]]


def test_non_finite_floats_use_stable_json_strings():
    text = serialize_result(
        ["value"], [[float("nan")], [float("inf")]], ResultFormat.JSON
    )

    assert json.loads(text)["rows"] == [["nan"], ["inf"]]


def test_csv_compatibility_mode_uses_legal_quoting():
    text = serialize_result(
        ["key_columns", "definition", "nullable"],
        [["Region, Segment", 'SELECT "x"\nFROM t', None]],
        ResultFormat.CSV,
    )

    parsed = list(csv.reader(StringIO(text)))

    assert parsed == [
        ["key_columns", "definition", "nullable"],
        ["Region, Segment", 'SELECT "x"\nFROM t', ""],
    ]


def test_legacy_mode_reproduces_original_unsafe_delimiter_output():
    text = serialize_result(
        ["key_columns", "nullable"],
        [["Region, Segment", None]],
        ResultFormat.LEGACY,
    )

    assert text == "key_columns,nullable\nRegion, Segment,None"

    binary_text = serialize_result(["binary"], [[b"abc"]], ResultFormat.LEGACY)
    assert binary_text == "binary\nb'abc'"


def test_result_format_parser_defaults_to_legacy_and_validates_values():
    assert ResultFormat.parse(None) is ResultFormat.LEGACY
    assert ResultFormat.parse(" CSV ") is ResultFormat.CSV

    with pytest.raises(ValueError, match="Unsupported result format"):
        ResultFormat.parse("yaml")

    with pytest.raises(ValueError, match="Unsupported result format"):
        ResultFormat.parse("")
