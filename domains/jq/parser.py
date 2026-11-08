from __future__ import annotations

import base64
import binascii
import csv
import gzip
import io
import re

from .models import Segment


class JqParseError(ValueError):
    pass


_BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_DELIMITERS = (",", "\t", "|", ";")


def split_tags(text: str) -> dict[str, str]:
    """Split #tag sections and concatenate trimmed Base64 payload lines."""
    result: dict[str, str] = {}
    current: str | None = None
    chunks: list[str] = []

    def finish() -> None:
        if current is not None:
            content = "".join(chunks)
            if current in result:
                # 允许重复 tag，合并数据（换行分隔）
                result[current] += "\n" + content
            else:
                result[current] = content

    for number, line in enumerate(text.split("\n"), start=1):
        value = line.strip()
        if value.startswith("#"):
            finish()
            tag = value[1:].strip()
            if not tag:
                raise JqParseError(f"empty tag at line {number}")
            current, chunks = tag, []
        elif value and current is not None:
            chunks.append(value)

    finish()
    return result


def _delimiter(text: str) -> str | None:
    header = text.splitlines()[0]
    if not any(item in header for item in _DELIMITERS):
        return None
    try:
        return csv.Sniffer().sniff(
            text[:8192], delimiters="".join(_DELIMITERS)
        ).delimiter
    except csv.Error:
        return max(_DELIMITERS, key=header.count)


def _parse_csv(text: str) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
    text = text.strip()
    if not text:
        raise JqParseError("decompressed CSV is empty")
    lines = text.splitlines()
    delimiter = _delimiter(text)

    if delimiter is None:
        header = lines[0]
        rows = tuple({header: line.strip()} for line in lines[1:] if line.strip())
        return rows, (header,)

    reader = csv.reader(
        io.StringIO(text, newline=""), delimiter=delimiter, strict=True
    )
    try:
        headers = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise JqParseError(f"invalid CSV header: {exc}") from exc
    if not headers or any(not item for item in headers):
        raise JqParseError("CSV contains an empty header")
    if len(set(headers)) != len(headers):
        raise JqParseError("CSV contains duplicate headers")

    rows: list[dict[str, str]] = []
    try:
        for line_number, values in enumerate(reader, start=2):
            if not values or all(value == "" for value in values):
                continue
            if len(values) > len(headers):
                raise JqParseError(
                    f"CSV row {line_number} has {len(values)} fields; "
                    f"expected {len(headers)}"
                )
            values.extend([""] * (len(headers) - len(values)))
            rows.append(dict(zip(headers, values, strict=True)))
    except csv.Error as exc:
        raise JqParseError(f"CSV parse error: {exc}") from exc
    return tuple(rows), tuple(headers)


def _split_base64_segments(compact: str) -> list[str]:
    """Split concatenated base64+gzip segments by looking for gzip magic number.
    
    Gzip files start with \\x1f\\x8b\\x08, which encodes to base64 starting with "H4sI".
    We use this marker to find segment boundaries.
    """
    # Find all occurrences of gzip magic in base64 form
    # H4sI is the base64 encoding of \\x1f\\x8b\\x08 (gzip magic + deflate method)
    positions = [0]
    pos = 0
    while True:
        idx = compact.find("H4sI", pos + 1) if pos > 0 else compact.find("H4sI", 1)
        if idx == -1:
            break
        # Align to 4-byte boundary (base64 works in 4-char blocks)
        aligned = (idx // 4) * 4
        if aligned > pos and aligned not in positions:
            positions.append(aligned)
        pos = idx
    
    if len(positions) == 1:
        # Single segment
        return [compact]
    
    # Split at boundaries
    segments = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(compact)
        segments.append(compact[start:end])
    
    return segments


def _dedup_rows(rows: tuple[dict[str, str], ...], columns: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    """Deduplicate rows by the first column (primary key), keeping the last occurrence."""
    if not columns:
        return rows
    key_col = columns[0]
    seen: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row.get(key_col, "")
        seen[key] = row
    return tuple(seen.values())


def decode_segment(encoded: str) -> Segment:
    compact = "".join(encoded.split())
    if not compact:
        raise JqParseError("empty Base64 segment")
    
    # Try single segment first (fast path)
    if len(compact) % 4 == 0 and _BASE64.fullmatch(compact):
        try:
            compressed = base64.b64decode(compact, validate=True)
            raw = gzip.decompress(compressed)
            csv_text = raw.decode("utf-8")
            rows, columns = _parse_csv(csv_text)
            return Segment(rows, columns, len(raw))
        except (binascii.Error, ValueError, OSError, EOFError, UnicodeDecodeError):
            # Might be multiple concatenated segments, try splitting
            pass
    
    # Split into multiple segments
    segments = _split_base64_segments(compact)
    
    all_rows = []
    columns = None
    total_bytes = 0
    
    for seg in segments:
        if len(seg) % 4 or not _BASE64.fullmatch(seg):
            raise JqParseError(f"invalid Base64 segment")
        
        try:
            compressed = base64.b64decode(seg, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise JqParseError(f"invalid Base64 segment: {exc}") from exc
        
        try:
            raw = gzip.decompress(compressed)
        except (OSError, EOFError) as exc:
            raise JqParseError("segment is not valid gzip data") from exc
        
        try:
            csv_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JqParseError("decompressed segment is not UTF-8") from exc
        
        rows, cols = _parse_csv(csv_text)
        
        if columns is None:
            columns = cols
        elif columns != cols:
            raise JqParseError("merged segments have different CSV schemas")
        
        all_rows.extend(rows)
        total_bytes += len(raw)
    
    # Deduplicate by primary key (first column) when merging multiple segments
    if len(segments) > 1 and columns:
        all_rows_deduped = _dedup_rows(tuple(all_rows), columns)
    else:
        all_rows_deduped = tuple(all_rows)
    
    return Segment(all_rows_deduped, columns, total_bytes)
