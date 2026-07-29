"""Bounded, file-backed storage for oversized SQL result sets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .serialization import serialize_json

RESULT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
RESULT_FILE_PATTERN = re.compile(r"^[0-9a-f]{32}\.json$")
_STORE_LOCK = threading.RLock()


def validate_result_envelope(envelope: Any) -> dict[str, Any]:
    """Validate the canonical result envelope before downstream use."""
    required_keys = {"columns", "rows", "row_count", "truncated"}
    if not isinstance(envelope, dict) or not required_keys.issubset(envelope):
        raise ValueError("Result JSON is not a canonical result envelope")
    columns = envelope["columns"]
    rows = envelope["rows"]
    if not isinstance(columns, list) or not all(
        isinstance(column, str) for column in columns
    ):
        raise ValueError("Result columns must be an array of strings")
    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        raise ValueError("Result rows must be an array of arrays")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("Every result row must have the same width as columns")
    if (
        isinstance(envelope["row_count"], bool)
        or not isinstance(envelope["row_count"], int)
        or envelope["row_count"] != len(rows)
    ):
        raise ValueError("Result row_count does not match rows")
    if not isinstance(envelope["truncated"], bool):
        raise ValueError("Result truncated must be a boolean")  # noqa: TRY004
    return envelope


def _env_non_negative_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _validate_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")  # noqa: TRY004
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class ResultStoreConfig:
    """Configuration for automatic result persistence and bounded retrieval."""

    output_dir: Path | None = None
    max_inline_rows: int = 200
    max_inline_bytes: int = 32 * 1024
    preview_rows: int = 5
    preview_bytes: int = 4 * 1024
    default_page_rows: int = 100
    max_page_rows: int = 1000
    max_page_bytes: int = 32 * 1024
    max_page_source_bytes: int = 64 * 1024 * 1024
    max_chunk_bytes: int = 16 * 1024
    ttl_seconds: int = 24 * 60 * 60
    max_file_bytes: int = 1024 * 1024 * 1024
    max_store_bytes: int = 1024 * 1024 * 1024
    include_path: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.max_page_bytes, bool) or not isinstance(
            self.max_page_bytes, int
        ):
            raise ValueError(  # noqa: TRY004
                "MSSQL_RESULT_MAX_PAGE_BYTES must be an integer"
            )
        if self.max_page_bytes < 0 or 0 < self.max_page_bytes < 512:
            raise ValueError("MSSQL_RESULT_MAX_PAGE_BYTES must be 0 or at least 512")
        if isinstance(self.max_page_source_bytes, bool) or not isinstance(
            self.max_page_source_bytes, int
        ):
            raise ValueError(  # noqa: TRY004
                "MSSQL_RESULT_MAX_PAGE_SOURCE_BYTES must be an integer"
            )
        if self.max_page_source_bytes < 0:
            raise ValueError(
                "MSSQL_RESULT_MAX_PAGE_SOURCE_BYTES must be a non-negative integer"
            )

    @classmethod
    def from_env(cls, *, output_dir_override: Path | None = None) -> ResultStoreConfig:
        raw_output_dir = (
            str(output_dir_override)
            if output_dir_override is not None
            else os.getenv("MSSQL_RESULT_OUTPUT_DIR")
        )
        if not raw_output_dir:
            return cls()

        output_dir = Path(raw_output_dir).expanduser()
        if not output_dir.is_absolute():
            raise ValueError("MSSQL_RESULT_OUTPUT_DIR must be an absolute path")

        config = cls(
            output_dir=output_dir.resolve(strict=False),
            max_inline_rows=_env_non_negative_int("MSSQL_RESULT_MAX_INLINE_ROWS", 200),
            max_inline_bytes=_env_non_negative_int(
                "MSSQL_RESULT_MAX_INLINE_BYTES", 32 * 1024
            ),
            preview_rows=_env_non_negative_int("MSSQL_RESULT_PREVIEW_ROWS", 5),
            preview_bytes=_env_non_negative_int("MSSQL_RESULT_PREVIEW_BYTES", 4 * 1024),
            default_page_rows=_env_non_negative_int(
                "MSSQL_RESULT_DEFAULT_PAGE_ROWS", 100
            ),
            max_page_rows=_env_non_negative_int("MSSQL_RESULT_MAX_PAGE_ROWS", 1000),
            max_page_bytes=_env_non_negative_int(
                "MSSQL_RESULT_MAX_PAGE_BYTES", 32 * 1024
            ),
            max_page_source_bytes=_env_non_negative_int(
                "MSSQL_RESULT_MAX_PAGE_SOURCE_BYTES", 64 * 1024 * 1024
            ),
            max_chunk_bytes=_env_non_negative_int(
                "MSSQL_RESULT_MAX_CHUNK_BYTES", 16 * 1024
            ),
            ttl_seconds=_env_non_negative_int("MSSQL_RESULT_TTL_SECONDS", 24 * 60 * 60),
            max_file_bytes=_env_non_negative_int(
                "MSSQL_RESULT_MAX_FILE_BYTES", 1024 * 1024 * 1024
            ),
            max_store_bytes=_env_non_negative_int(
                "MSSQL_RESULT_MAX_STORE_BYTES", 1024 * 1024 * 1024
            ),
            include_path=_env_bool("MSSQL_RESULT_INCLUDE_PATH", False),
        )
        if config.max_page_rows < 1:
            raise ValueError("MSSQL_RESULT_MAX_PAGE_ROWS must be at least 1")
        if not 1 <= config.default_page_rows <= config.max_page_rows:
            raise ValueError(
                "MSSQL_RESULT_DEFAULT_PAGE_ROWS must be between 1 and "
                "MSSQL_RESULT_MAX_PAGE_ROWS"
            )
        if config.max_chunk_bytes < 1:
            raise ValueError("MSSQL_RESULT_MAX_CHUNK_BYTES must be at least 1")
        return config

    @property
    def enabled(self) -> bool:
        return self.output_dir is not None


class ResultStore:
    """Persist and retrieve canonical JSON result envelopes."""

    def __init__(self, config: ResultStoreConfig):
        self.config = config
        self.root: Path | None = None
        if config.output_dir is not None:
            config.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            root = config.output_dir.resolve(strict=True)
            if not root.is_dir():
                raise ValueError("MSSQL_RESULT_OUTPUT_DIR must be a directory")
            self.root = root

    @classmethod
    def from_env(cls, *, output_dir_override: Path | None = None) -> ResultStore:
        return cls(ResultStoreConfig.from_env(output_dir_override=output_dir_override))

    @property
    def enabled(self) -> bool:
        return self.root is not None

    def should_store(
        self, payload_bytes: bytes, row_count: int, *, force: bool
    ) -> bool:
        if force:
            if not self.enabled:
                raise ValueError(
                    "store_result requires MSSQL_RESULT_OUTPUT_DIR to be configured"
                )
            return True
        if not self.enabled:
            return False
        row_limit = self.config.max_inline_rows
        byte_limit = self.config.max_inline_bytes
        return (row_limit > 0 and row_count > row_limit) or (
            byte_limit > 0 and len(payload_bytes) > byte_limit
        )

    def store(self, envelope: dict[str, Any], payload_bytes: bytes) -> dict[str, Any]:
        root = self._require_root()
        if (
            self.config.max_file_bytes > 0
            and len(payload_bytes) > self.config.max_file_bytes
        ):
            raise ValueError("Serialized result exceeds MSSQL_RESULT_MAX_FILE_BYTES")
        if (
            self.config.max_store_bytes > 0
            and len(payload_bytes) > self.config.max_store_bytes
        ):
            raise ValueError(
                "Serialized result cannot fit within MSSQL_RESULT_MAX_STORE_BYTES"
            )

        result_id = uuid4().hex
        final_path = root / f"{result_id}.json"
        temp_path = root / f".{result_id}.{uuid4().hex}.tmp"

        with _STORE_LOCK:
            self._make_room(len(payload_bytes))
            try:
                with temp_path.open("xb") as result_file:
                    try:
                        os.chmod(temp_path, 0o600)
                    except OSError:
                        pass
                    result_file.write(payload_bytes)
                    result_file.flush()
                    os.fsync(result_file.fileno())
                os.replace(temp_path, final_path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()

        stat = final_path.stat()
        expires_at = (
            int(stat.st_mtime + self.config.ttl_seconds)
            if self.config.ttl_seconds > 0
            else None
        )
        summary = {
            "columns": envelope["columns"],
            "row_count": envelope["row_count"],
            "stored": True,
            "result_id": result_id,
            "byte_count": len(payload_bytes),
            "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "preview_rows": self._preview(envelope["rows"]),
            "truncated": envelope["truncated"],
            "expires_at": expires_at,
        }
        if self.config.include_path:
            summary["result_path"] = str(final_path)
        return summary

    def read_page(
        self, result_id: str, *, offset: Any = 0, limit: Any = None
    ) -> dict[str, Any]:
        limit = self.config.default_page_rows if limit is None else limit
        offset = _validate_int(offset, "offset", minimum=0, maximum=2**63 - 1)
        limit = _validate_int(
            limit, "limit", minimum=1, maximum=self.config.max_page_rows
        )
        source_bytes = self._result_path(result_id).stat().st_size
        max_page_source_bytes = self.config.max_page_source_bytes
        if max_page_source_bytes > 0 and source_bytes > max_page_source_bytes:
            return self._chunk_required_descriptor(
                result_id,
                offset,
                reason="result_exceeds_page_source_limit",
                source_bytes=source_bytes,
                max_page_source_bytes=max_page_source_bytes,
            )

        envelope = self._load_envelope(result_id)
        total_row_count = envelope["row_count"]
        if offset > total_row_count:
            raise ValueError("offset must not exceed total_row_count")
        available_rows = envelope["rows"][offset : offset + limit]

        page_rows: list[list[Any]] = []
        page_rows_bytes = 0
        columns_bytes = serialize_json(envelope["columns"]).encode("utf-8")
        result_id_bytes = serialize_json(result_id).encode("utf-8")
        max_page_bytes = self.config.max_page_bytes

        if (
            max_page_bytes > 0
            and self._page_serialized_size(
                envelope,
                offset=offset,
                returned_rows=0,
                rows_bytes=0,
                columns_bytes=columns_bytes,
                result_id_bytes=result_id_bytes,
            )
            > max_page_bytes
        ):
            return self._chunk_required_descriptor(
                result_id,
                offset,
                reason="columns_exceed_page_byte_limit",
                envelope=envelope,
            )

        for row in available_rows:
            row_bytes = len(serialize_json(row).encode("utf-8"))
            candidate_row_count = len(page_rows) + 1
            candidate_rows_bytes = page_rows_bytes + row_bytes
            if page_rows:
                candidate_rows_bytes += 1  # The comma between compact JSON rows.
            if (
                max_page_bytes > 0
                and self._page_serialized_size(
                    envelope,
                    offset=offset,
                    returned_rows=candidate_row_count,
                    rows_bytes=candidate_rows_bytes,
                    columns_bytes=columns_bytes,
                    result_id_bytes=result_id_bytes,
                )
                > max_page_bytes
            ):
                if not page_rows:
                    return self._chunk_required_descriptor(
                        result_id,
                        offset,
                        reason="row_exceeds_page_byte_limit",
                        envelope=envelope,
                        row_index=offset,
                    )
                break
            page_rows.append(row)
            page_rows_bytes = candidate_rows_bytes

        return self._page_envelope(envelope, result_id, offset, page_rows)

    def read_chunk(
        self, result_id: str, *, offset_bytes: Any = 0, max_bytes: Any = None
    ) -> dict[str, Any]:
        max_bytes = self.config.max_chunk_bytes if max_bytes is None else max_bytes
        offset_bytes = _validate_int(
            offset_bytes, "offset_bytes", minimum=0, maximum=2**63 - 1
        )
        max_bytes = _validate_int(
            max_bytes,
            "max_bytes",
            minimum=1,
            maximum=self.config.max_chunk_bytes,
        )
        with _STORE_LOCK:
            path = self._result_path(result_id)
            with path.open("rb", buffering=0) as result_file:
                total_bytes = os.fstat(result_file.fileno()).st_size
                if offset_bytes > total_bytes:
                    raise ValueError("offset_bytes must not exceed total_bytes")
                result_file.seek(offset_bytes)
                chunk = result_file.read(max_bytes)
        next_offset = offset_bytes + len(chunk)
        return {
            "result_id": result_id,
            "offset_bytes": offset_bytes,
            "returned_bytes": len(chunk),
            "next_offset": next_offset,
            "total_bytes": total_bytes,
            "eof": next_offset >= total_bytes,
            "data_base64": base64.b64encode(chunk).decode("ascii"),
        }

    def load(self, result_id: str) -> dict[str, Any]:
        """Load and validate a complete stored result for machine consumers."""
        return self._load_envelope(result_id)

    def _page_envelope(
        self,
        envelope: dict[str, Any],
        result_id: str,
        offset: int,
        rows: list[list[Any]],
    ) -> dict[str, Any]:
        next_offset = offset + len(rows)
        return {
            "columns": envelope["columns"],
            "rows": rows,
            "total_row_count": envelope["row_count"],
            "result_id": result_id,
            "offset": offset,
            "returned_rows": len(rows),
            "next_offset": next_offset,
            "has_more": next_offset < envelope["row_count"],
            "truncated": envelope["truncated"],
        }

    def _page_serialized_size(
        self,
        envelope: dict[str, Any],
        *,
        offset: int,
        returned_rows: int,
        rows_bytes: int,
        columns_bytes: bytes,
        result_id_bytes: bytes,
    ) -> int:
        """Return the exact compact UTF-8 size without re-serializing page rows."""
        total_row_count = envelope["row_count"]
        next_offset = offset + returned_rows
        has_more = next_offset < total_row_count
        truncated = envelope["truncated"]
        return sum(
            (
                len(b'{"columns":'),
                len(columns_bytes),
                len(b',"rows":['),
                rows_bytes,
                len(b'],"total_row_count":'),
                len(str(total_row_count)),
                len(b',"result_id":'),
                len(result_id_bytes),
                len(b',"offset":'),
                len(str(offset)),
                len(b',"returned_rows":'),
                len(str(returned_rows)),
                len(b',"next_offset":'),
                len(str(next_offset)),
                len(b',"has_more":'),
                len(b"true" if has_more else b"false"),
                len(b',"truncated":'),
                len(b"true" if truncated else b"false"),
                len(b"}"),
            )
        )

    def _chunk_required_descriptor(
        self,
        result_id: str,
        offset: int,
        *,
        reason: str,
        envelope: dict[str, Any] | None = None,
        row_index: int | None = None,
        source_bytes: int | None = None,
        max_page_source_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Return a terminal, byte-bounded pointer to chunk retrieval."""
        descriptor = {
            "result_id": result_id,
            "offset": offset,
            "returned_rows": 0,
            "chunk_required": True,
            "reason": reason,
        }
        if envelope is not None:
            descriptor["total_row_count"] = envelope["row_count"]
            descriptor["truncated"] = envelope["truncated"]
        if row_index is not None:
            descriptor["row_index"] = row_index
        if source_bytes is not None:
            descriptor["source_bytes"] = source_bytes
        if max_page_source_bytes is not None:
            descriptor["max_page_source_bytes"] = max_page_source_bytes

        max_page_bytes = self.config.max_page_bytes
        if (
            max_page_bytes > 0
            and len(serialize_json(descriptor).encode("utf-8")) > max_page_bytes
        ):
            raise RuntimeError(
                "MSSQL_RESULT_MAX_PAGE_BYTES is too small for a chunk descriptor"
            )
        return descriptor

    def _preview(self, rows: list[list[Any]]) -> list[list[Any]]:
        preview: list[list[Any]] = []
        for row in rows[: self.config.preview_rows]:
            candidate = [*preview, row]
            if (
                self.config.preview_bytes > 0
                and len(serialize_json(candidate).encode("utf-8"))
                > self.config.preview_bytes
            ):
                break
            preview.append(row)
        return preview

    def _load_envelope(self, result_id: str) -> dict[str, Any]:
        payload = self._read_payload(result_id)
        try:
            envelope = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Stored result '{result_id}' is corrupt") from exc
        try:
            return validate_result_envelope(envelope)
        except ValueError as exc:
            raise RuntimeError(f"Stored result '{result_id}' is corrupt") from exc

    def _read_payload(self, result_id: str) -> bytes:
        path = self._result_path(result_id)
        with _STORE_LOCK:
            return path.read_bytes()

    def _result_path(self, result_id: str) -> Path:
        root = self._require_root()
        if not isinstance(result_id, str) or not RESULT_ID_PATTERN.fullmatch(result_id):
            raise ValueError("result_id must be 32 lowercase hexadecimal characters")
        candidate = root / f"{result_id}.json"
        if candidate.is_symlink():
            raise ValueError("Stored result symlinks are not allowed")
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Unknown result_id: {result_id}") from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("Stored result path is outside the configured directory")
        if (
            self.config.ttl_seconds > 0
            and time.time() - resolved.stat().st_mtime > self.config.ttl_seconds
        ):
            raise FileNotFoundError(f"Result has expired: {result_id}")
        return resolved

    def _make_room(self, incoming_bytes: int) -> None:
        root = self._require_root()
        now = time.time()
        result_files = []
        for path in root.iterdir():
            if path.is_symlink() or not RESULT_FILE_PATTERN.fullmatch(path.name):
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if (
                self.config.ttl_seconds > 0
                and now - stat.st_mtime > self.config.ttl_seconds
            ):
                path.unlink(missing_ok=True)
                continue
            result_files.append((stat.st_mtime, stat.st_size, path))

        quota = self.config.max_store_bytes
        if quota <= 0:
            return
        current_bytes = sum(size for _, size, _ in result_files)
        for _, size, path in sorted(result_files):
            if current_bytes + incoming_bytes <= quota:
                break
            path.unlink(missing_ok=True)
            current_bytes -= size

    def _require_root(self) -> Path:
        if self.root is None:
            raise ValueError("MSSQL_RESULT_OUTPUT_DIR is not configured")
        return self.root
