"""Export a complete canonical SQL result envelope to standard output."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .result_store import ResultStore, ResultStoreConfig, validate_result_envelope
from .serialization import serialize_json

CANONICAL_RESULT_KEYS = frozenset({"columns", "rows", "row_count", "truncated"})


def validate_complete_result_envelope(envelope: Any) -> dict[str, Any]:
    """Validate that a value is a complete result rather than a summary or page."""
    validated = validate_result_envelope(envelope)
    if set(validated) != CANONICAL_RESULT_KEYS:
        raise ValueError("Result JSON is not a complete canonical result envelope")
    return validated


def load_result_file(path: Path) -> dict[str, Any]:
    """Load and validate a complete canonical result envelope from a JSON file."""

    def reject_nonstandard_constant(value: str) -> None:
        raise ValueError(f"Non-standard JSON constant is not allowed: {value}")

    try:
        envelope = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_nonstandard_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"Unable to read result JSON '{path}': {exc}") from exc
    return validate_complete_result_envelope(envelope)


def load_stored_result(
    result_id: str, result_dir: Path | None = None
) -> dict[str, Any]:
    """Load a stored result by ID using configured safety and expiry settings."""
    config = ResultStoreConfig.from_env(output_dir_override=result_dir)
    if config.output_dir is None:
        raise ValueError(
            "--result-dir or MSSQL_RESULT_OUTPUT_DIR is required with --result-id"
        )
    if not config.output_dir.is_dir():
        raise ValueError(f"Result directory does not exist: {config.output_dir}")
    store = ResultStore(config)
    return validate_complete_result_envelope(store.load(result_id))


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for canonical result export."""
    parser = argparse.ArgumentParser(
        description="Export a complete canonical SQL result as compact JSON"
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--result-id")
    source_group.add_argument("--results-json", type=Path)
    parser.add_argument(
        "--result-dir",
        type=Path,
        help="Override MSSQL_RESULT_OUTPUT_DIR when --result-id is used",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load one canonical result and write compact JSON to standard output."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.results_json is not None:
        if args.result_dir is not None:
            parser.error("--result-dir requires --result-id")
        envelope = load_result_file(args.results_json)
    else:
        envelope = load_stored_result(args.result_id, args.result_dir)

    print(serialize_json(envelope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
