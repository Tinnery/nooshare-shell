from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .csv_io import export_jq_csv_dataset
from .models import InputReceipt, JqEtlResult, SegmentReceipt, TableBatch
from .parser import decode_segment, split_tags
from .transform import TAG_SPECS, derive_report_period, shape_segment
from .validate import validate_batch, validate_result


class JqFlowError(ValueError):
    pass


MAX_INPUT_BYTES = 50 * 1024 * 1024


def _same_snapshot(left: TableBatch, right: TableBatch) -> bool:
    if (
        left.columns != right.columns
        or left.primary_key != right.primary_key
        or left.input_rows != right.input_rows
        or left.dropped_rows != right.dropped_rows
    ):
        return False

    def keyed(batch: TableBatch) -> dict[tuple[object, ...], tuple[object, ...]]:
        return {
            tuple(row[field] for field in batch.primary_key):
            tuple(row[field] for field in batch.columns)
            for row in batch.rows
        }

    return keyed(left) == keyed(right)


def run_jq_etl(
    dataset_paths: Iterable[str | Path],
    *,
    output_dir: str | Path | None = None,
) -> JqEtlResult:
    """Build and validate one logical JQ snapshot, optionally exporting CSV files.

    Repeated tags across files are accepted only when the normalized snapshots
    are identical. Conflicting complete snapshots are rejected, never merged
    implicitly. When output_dir is supplied, the result is exported as a
    manifest-backed CSV dataset before it is returned.
    """
    paths = tuple(Path(path) for path in dataset_paths)
    if not paths:
        raise JqFlowError("at least one JQ dataset file is required")

    inputs, segment_receipts = [], []
    batches_by_tag: dict[str, list[TableBatch]] = {}

    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise JqFlowError(f"cannot read dataset {path}: {exc}") from exc
        if len(raw) > MAX_INPUT_BYTES:
            raise JqFlowError(f"dataset {path} exceeds the 50 MiB input limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JqFlowError(f"dataset {path} is not UTF-8") from exc

        segments = split_tags(text)
        if not segments:
            raise JqFlowError(f"dataset {path} contains no tag sections")
        unknown = sorted(set(segments) - set(TAG_SPECS))
        if unknown:
            raise JqFlowError(f"unsupported JQ tag(s) in {path}: {', '.join(unknown)}")

        inputs.append(InputReceipt(
            str(path), len(raw), hashlib.sha256(raw).hexdigest(), tuple(segments)
        ))

        for tag, encoded in segments.items():
            if not encoded:
                raise JqFlowError(f"dataset {path} contains an empty #{tag} segment")
            try:
                decoded = decode_segment(encoded)
                batch = shape_segment(tag, decoded.rows, str(path))
                validate_batch(batch)
            except ValueError as exc:
                raise JqFlowError(f"{path} #{tag}: {exc}") from exc

            segment_receipts.append(SegmentReceipt(
                str(path), tag, len(encoded), len(decoded.rows), decoded.columns
            ))
            batches_by_tag.setdefault(tag, []).append(batch)

    logical_batches = []
    for tag, candidates in batches_by_tag.items():
        first = candidates[0]
        if any(not _same_snapshot(first, candidate) for candidate in candidates[1:]):
            sources = ", ".join(candidate.sources[0] for candidate in candidates)
            raise JqFlowError(
                f"conflicting complete snapshots for tag #{tag} in {sources}; "
                "reconcile the source datasets explicitly"
            )
        logical_batches.append(replace(first, sources=tuple(c.sources[0] for c in candidates)))

    by_tag = {batch.tag: batch for batch in logical_batches}
    if "pub_dates" in by_tag:
        if "codes" not in by_tag:
            raise JqFlowError(
                "#pub_dates needs #codes in this self-contained flow; "
                "an external stock snapshot is not read implicitly"
            )
        derived = derive_report_period(by_tag["pub_dates"], by_tag["codes"])
        try:
            validate_batch(derived)
        except ValueError as exc:
            raise JqFlowError(f"derived report_period: {exc}") from exc
        logical_batches.append(derived)

    result = JqEtlResult(tuple(inputs), tuple(segment_receipts), tuple(logical_batches))
    validate_result(result)
    if output_dir is not None:
        export_jq_csv_dataset(result, output_dir)
    return result
