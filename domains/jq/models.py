from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Segment:
    rows: tuple[dict[str, str], ...]
    columns: tuple[str, ...]
    decompressed_bytes: int


@dataclass(frozen=True, slots=True)
class InputReceipt:
    path: str
    size_bytes: int
    sha256: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SegmentReceipt:
    source: str
    tag: str
    encoded_chars: int
    rows: int
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableBatch:
    tag: str
    table: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    sources: tuple[str, ...]
    input_rows: int
    dropped_rows: int = 0


@dataclass(frozen=True, slots=True)
class JqEtlResult:
    input_files: tuple[InputReceipt, ...]
    segments: tuple[SegmentReceipt, ...]
    tables: tuple[TableBatch, ...]

    @property
    def table_row_counts(self) -> dict[str, int]:
        return {batch.table: len(batch.rows) for batch in self.tables}

    @property
    def total_output_rows(self) -> int:
        return sum(len(batch.rows) for batch in self.tables)
