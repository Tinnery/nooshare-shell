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
            result[current] = "".join(chunks)

    for number, line in enumerate(text.split("\n"), start=1):
        value = line.strip()
        if value.startswith("#"):
            finish()
            tag = value[1:].strip()
            if not tag:
                raise JqParseError(f"empty tag at line {number}")
            if tag in result:
                raise JqParseError(f"duplicate tag section: {tag}")
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


def decode_segment(encoded: str) -> Segment:
    compact = "".join(encoded.split())
    if not compact or len(compact) % 4 or not _BASE64.fullmatch(compact):
        raise JqParseError("invalid Base64 segment")
    try:
        compressed = base64.b64decode(compact, validate=True)
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
    rows, columns = _parse_csv(csv_text)
    return Segment(rows, columns, len(raw))
