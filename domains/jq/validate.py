from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import JqEtlResult, TableBatch


DATE_COLUMNS = {"start_date", "end_date", "pub_date", "trade_date", "date"}
DECIMAL_COLUMNS = {"limit_ratio", "capitalization", "circulating_cap"}
VALID_EXCHANGES = {"sh", "sz", "bj", "unknown"}


class JqValidationError(ValueError):
    pass


def validate_batch(batch: TableBatch) -> None:
    if not batch.rows:
        raise JqValidationError(f"table {batch.table!r} is empty although tag {batch.tag!r} was supplied")

    seen: set[tuple[Any, ...]] = set()
    for number, row in enumerate(batch.rows, start=1):
        if tuple(row) != batch.columns:
            raise JqValidationError(f"{batch.table} row {number}: fields do not match output schema")
        key = tuple(row[field] for field in batch.primary_key)
        if any(value is None or value == "" for value in key):
            raise JqValidationError(f"{batch.table} row {number}: empty primary key")
        if key in seen:
            raise JqValidationError(f"{batch.table} row {number}: duplicate primary key {key!r}")
        seen.add(key)

        for field, value in row.items():
            if field in DATE_COLUMNS and value is not None:
                try:
                    date.fromisoformat(value)
                except (TypeError, ValueError) as exc:
                    raise JqValidationError(f"{batch.table} row {number}: invalid {field} {value!r}") from exc
            if field in DECIMAL_COLUMNS:
                try:
                    Decimal(str(value))
                except (InvalidOperation, ValueError) as exc:
                    raise JqValidationError(f"{batch.table} row {number}: invalid {field} {value!r}") from exc

        if batch.table == "stocks" and row["exchange"] not in VALID_EXCHANGES:
            raise JqValidationError(f"stocks row {number}: unsupported exchange {row['exchange']!r}")
        if batch.table == "trading_day" and not isinstance(row["is_trading_day"], bool):
            raise JqValidationError(f"trading_day row {number}: is_trading_day must be boolean")


def validate_result(result: JqEtlResult) -> None:
    names: set[str] = set()
    for batch in result.tables:
        if batch.table in names:
            raise JqValidationError(f"duplicate logical output table: {batch.table}")
        names.add(batch.table)
        validate_batch(batch)
