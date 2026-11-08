from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .models import InputReceipt, JqEtlResult, SegmentReceipt, TableBatch
from .validate import validate_result


FORMAT_NAME = "nooshare-jq-csv-dataset"
FORMAT_VERSION = 1
NULL_MARKER = r"\N"
_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class JqCsvDatasetError(ValueError):
    """Raised when a JQ CSV dataset cannot be safely written or read."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _column_types(batch: TableBatch) -> dict[str, str]:
    result: dict[str, str] = {}
    for column in batch.columns:
        observed = {
            type(row[column])
            for row in batch.rows
            if row[column] is not None
        }
        if not observed or observed == {str}:
            result[column] = "string"
        elif observed == {bool}:
            result[column] = "boolean"
        else:
            names = ", ".join(sorted(item.__name__ for item in observed))
            raise JqCsvDatasetError(
                f"{batch.table}.{column} has unsupported or mixed value types: {names}"
            )
    return result


def _encode_cell(value: Any, value_type: str) -> str:
    if value is None:
        return NULL_MARKER
    if value_type == "boolean":
        if type(value) is not bool:
            raise JqCsvDatasetError("boolean CSV columns must contain bool or None")
        return "true" if value else "false"
    if value_type == "string":
        if not isinstance(value, str):
            raise JqCsvDatasetError("string CSV columns must contain str or None")
        # Reserve a single leading backslash for the NULL marker. Literal values
        # beginning with a backslash are escaped by doubling the first one.
        return "\\" + value if value.startswith("\\") else value
    raise JqCsvDatasetError(f"unsupported CSV column type: {value_type}")


def _decode_cell(value: str, value_type: str) -> Any:
    if value == NULL_MARKER:
        return None
    if value_type == "boolean":
        if value == "true":
            return True
        if value == "false":
            return False
        raise JqCsvDatasetError(f"invalid boolean CSV value: {value!r}")
    if value_type == "string":
        if value.startswith("\\"):
            if value.startswith("\\\\"):
                return value[1:]
            raise JqCsvDatasetError(f"invalid escaped CSV string: {value!r}")
        return value
    raise JqCsvDatasetError(f"unsupported CSV column type: {value_type}")


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise JqCsvDatasetError(f"manifest field {field!r} must be a string")
    return value


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise JqCsvDatasetError(f"manifest field {field!r} must be a string list")
    return tuple(value)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise JqCsvDatasetError(f"manifest field {field!r} must be a non-negative integer")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise JqCsvDatasetError(f"manifest field {field!r} must be an object")
    return value


def export_jq_csv_dataset(
    result: JqEtlResult,
    destination: str | Path,
) -> Path:
    """Write a validated JQ result as CSV tables plus a schema/provenance manifest.

    Export is atomic for a new destination. Repeating an export to an existing
    destination is idempotent only when the existing dataset reads back to the
    exact same JqEtlResult; conflicting output is never overwritten implicitly.
    """
    validate_result(result)
    destination = Path(destination)
    if not destination.name or destination == destination.parent:
        raise JqCsvDatasetError("destination must name a dataset directory")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() or destination.is_symlink():
        try:
            existing = read_jq_csv_dataset(destination)
        except (OSError, JqCsvDatasetError) as exc:
            raise JqCsvDatasetError(
                f"destination already exists and is not the same valid dataset: {destination}"
            ) from exc
        if existing == result:
            return destination
        raise JqCsvDatasetError(
            f"destination already contains a different dataset: {destination}"
        )

    stage = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.tmp-",
        dir=destination.parent,
    ))
    try:
        table_manifest: list[dict[str, Any]] = []
        for batch in result.tables:
            if not _TABLE_NAME.fullmatch(batch.table):
                raise JqCsvDatasetError(f"unsafe logical table name: {batch.table!r}")
            filename = f"{batch.table}.csv"
            path = stage / filename
            column_types = _column_types(batch)

            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(batch.columns)
                for row in batch.rows:
                    writer.writerow([
                        _encode_cell(row[column], column_types[column])
                        for column in batch.columns
                    ])

            table_manifest.append({
                "table": batch.table,
                "tag": batch.tag,
                "file": filename,
                "columns": list(batch.columns),
                "column_types": column_types,
                "primary_key": list(batch.primary_key),
                "sources": list(batch.sources),
                "input_rows": batch.input_rows,
                "dropped_rows": batch.dropped_rows,
                "row_count": len(batch.rows),
                "file_size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            })

        manifest = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "null_encoding": NULL_MARKER,
            "input_files": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "tags": list(item.tags),
                }
                for item in result.input_files
            ],
            "segments": [
                {
                    "source": item.source,
                    "tag": item.tag,
                    "encoded_chars": item.encoded_chars,
                    "rows": item.rows,
                    "columns": list(item.columns),
                }
                for item in result.segments
            ],
            "tables": table_manifest,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.rename(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    return destination


def read_jq_csv_dataset(source: str | Path) -> JqEtlResult:
    """Read and validate a CSV dataset written by export_jq_csv_dataset."""
    source = Path(source)
    try:
        if not source.is_dir():
            raise JqCsvDatasetError(f"CSV dataset directory does not exist: {source}")
        manifest_path = source / "manifest.json"
        manifest = _mapping(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            "root",
        )
        if manifest.get("format") != FORMAT_NAME:
            raise JqCsvDatasetError("unrecognized JQ CSV dataset format")
        if manifest.get("format_version") != FORMAT_VERSION:
            raise JqCsvDatasetError(
                f"unsupported JQ CSV dataset version: {manifest.get('format_version')!r}"
            )
        if manifest.get("null_encoding") != NULL_MARKER:
            raise JqCsvDatasetError("unsupported CSV NULL encoding")

        input_values = manifest.get("input_files")
        if not isinstance(input_values, list):
            raise JqCsvDatasetError("manifest input_files must be a list")
        inputs = []
        for item in input_values:
            item = _mapping(item, "input_files[]")
            digest = _string(item.get("sha256"), "input_files[].sha256")
            if not _SHA256.fullmatch(digest):
                raise JqCsvDatasetError("invalid input file SHA-256")
            inputs.append(InputReceipt(
                path=_string(item.get("path"), "input_files[].path"),
                size_bytes=_integer(item.get("size_bytes"), "input_files[].size_bytes"),
                sha256=digest,
                tags=_string_list(item.get("tags"), "input_files[].tags"),
            ))

        segment_values = manifest.get("segments")
        if not isinstance(segment_values, list):
            raise JqCsvDatasetError("manifest segments must be a list")
        segments = []
        for item in segment_values:
            item = _mapping(item, "segments[]")
            segments.append(SegmentReceipt(
                source=_string(item.get("source"), "segments[].source"),
                tag=_string(item.get("tag"), "segments[].tag"),
                encoded_chars=_integer(
                    item.get("encoded_chars"), "segments[].encoded_chars"
                ),
                rows=_integer(item.get("rows"), "segments[].rows"),
                columns=_string_list(item.get("columns"), "segments[].columns"),
            ))

        table_values = manifest.get("tables")
        if not isinstance(table_values, list) or not table_values:
            raise JqCsvDatasetError("manifest tables must be a non-empty list")

        batches: list[TableBatch] = []
        expected_files: set[str] = set()
        seen_tables: set[str] = set()
        for item in table_values:
            item = _mapping(item, "tables[]")
            table = _string(item.get("table"), "tables[].table")
            if not _TABLE_NAME.fullmatch(table) or table in seen_tables:
                raise JqCsvDatasetError(f"invalid or duplicate table name: {table!r}")
            seen_tables.add(table)

            filename = _string(item.get("file"), "tables[].file")
            if filename != f"{table}.csv":
                raise JqCsvDatasetError(f"unexpected CSV filename for table {table!r}")
            expected_files.add(filename)
            csv_path = source / filename
            if not csv_path.is_file():
                raise JqCsvDatasetError(f"missing CSV table file: {filename}")

            expected_size = _integer(
                item.get("file_size_bytes"), "tables[].file_size_bytes"
            )
            expected_digest = _string(item.get("sha256"), "tables[].sha256")
            if not _SHA256.fullmatch(expected_digest):
                raise JqCsvDatasetError(f"invalid SHA-256 for {filename}")
            if csv_path.stat().st_size != expected_size:
                raise JqCsvDatasetError(f"CSV byte size mismatch: {filename}")
            if _sha256_file(csv_path) != expected_digest:
                raise JqCsvDatasetError(f"CSV SHA-256 mismatch: {filename}")

            columns = _string_list(item.get("columns"), "tables[].columns")
            if not columns or len(set(columns)) != len(columns):
                raise JqCsvDatasetError(f"invalid columns for table {table!r}")
            column_type_values = _mapping(
                item.get("column_types"), "tables[].column_types"
            )
            if set(column_type_values) != set(columns):
                raise JqCsvDatasetError(
                    f"column type declarations do not match columns for {table!r}"
                )
            column_types = {
                column: _string(column_type_values[column], f"{table}.{column} type")
                for column in columns
            }
            if any(value not in {"string", "boolean"} for value in column_types.values()):
                raise JqCsvDatasetError(f"unsupported column type in table {table!r}")

            rows: list[dict[str, Any]] = []
            with csv_path.open("r", encoding="utf-8", newline="") as stream:
                reader = csv.reader(stream, strict=True)
                try:
                    header = next(reader)
                except StopIteration as exc:
                    raise JqCsvDatasetError(f"CSV is empty: {filename}") from exc
                if tuple(header) != columns:
                    raise JqCsvDatasetError(f"CSV header does not match manifest: {filename}")

                for line_number, values in enumerate(reader, start=2):
                    if len(values) != len(columns):
                        raise JqCsvDatasetError(
                            f"{filename} row {line_number} has {len(values)} fields; "
                            f"expected {len(columns)}"
                        )
                    rows.append({
                        column: _decode_cell(value, column_types[column])
                        for column, value in zip(columns, values, strict=True)
                    })

            expected_rows = _integer(item.get("row_count"), "tables[].row_count")
            if len(rows) != expected_rows:
                raise JqCsvDatasetError(
                    f"row count mismatch for {filename}: {len(rows)} != {expected_rows}"
                )

            batches.append(TableBatch(
                tag=_string(item.get("tag"), "tables[].tag"),
                table=table,
                columns=columns,
                primary_key=_string_list(item.get("primary_key"), "tables[].primary_key"),
                rows=tuple(rows),
                sources=_string_list(item.get("sources"), "tables[].sources"),
                input_rows=_integer(item.get("input_rows"), "tables[].input_rows"),
                dropped_rows=_integer(item.get("dropped_rows"), "tables[].dropped_rows"),
            ))

        actual_files = {item.name for item in source.glob("*.csv") if item.is_file()}
        if actual_files != expected_files:
            raise JqCsvDatasetError(
                "CSV file set does not match manifest "
                f"(expected {sorted(expected_files)}, found {sorted(actual_files)})"
            )

        result = JqEtlResult(tuple(inputs), tuple(segments), tuple(batches))
        validate_result(result)
        return result
    except JqCsvDatasetError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, csv.Error, KeyError, TypeError, ValueError) as exc:
        raise JqCsvDatasetError(f"invalid JQ CSV dataset at {source}: {exc}") from exc
