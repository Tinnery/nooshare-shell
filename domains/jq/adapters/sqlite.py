"""SQLite adapter for publishing and verifying complete JQ snapshots.

SQLite uses a dedicated file and a jq__ table prefix because it does not
implement PostgreSQL schemas. The logical table names and publication behavior
remain the same as the PostgreSQL adapter.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from ..models import JqEtlResult, TableBatch
from ..transform import REPORT_PERIOD_SPEC, TAG_SPECS
from ..validate import validate_result


TABLE_PREFIX = "jq__"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_TABLE_SPECS = {
    spec.table: spec
    for spec in (*TAG_SPECS.values(), REPORT_PERIOD_SPEC)
}


class JqSQLitePublishError(RuntimeError):
    """Raised when a JQ snapshot cannot be safely published to SQLite."""


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise JqSQLitePublishError(f"unsafe SQLite identifier: {value!r}")
    return f'"{value}"'


def _table_name(logical_name: str) -> str:
    return _identifier(f"{TABLE_PREFIX}{logical_name}")


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
            raise JqSQLitePublishError(
                f"{batch.table}.{column} contains unsupported value types: {names}"
            )
    return result


def _validate_snapshot(dataset: JqEtlResult) -> dict[str, dict[str, str]]:
    validate_result(dataset)
    batches = {batch.table: batch for batch in dataset.tables}
    expected_tables = set(_TABLE_SPECS)
    actual_tables = set(batches)
    if actual_tables != expected_tables:
        raise JqSQLitePublishError(
            "incomplete or unexpected JQ snapshot: "
            f"missing={sorted(expected_tables - actual_tables)}, "
            f"unexpected={sorted(actual_tables - expected_tables)}"
        )

    types_by_table: dict[str, dict[str, str]] = {}
    for table, batch in batches.items():
        spec = _TABLE_SPECS[table]
        if batch.columns != spec.columns:
            raise JqSQLitePublishError(
                f"{table} columns differ from the ETL contract: "
                f"{batch.columns!r} != {spec.columns!r}"
            )
        if batch.primary_key != spec.primary_key:
            raise JqSQLitePublishError(
                f"{table} primary key differs from the ETL contract: "
                f"{batch.primary_key!r} != {spec.primary_key!r}"
            )
        types_by_table[table] = _column_types(batch)
    return types_by_table


def _validate_table_namespace(connection: sqlite3.Connection) -> None:
    expected = {f"{TABLE_PREFIX}{table}" for table in _TABLE_SPECS}
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name GLOB 'jq__*'"
        )
    }
    unexpected = existing - expected
    if unexpected:
        raise JqSQLitePublishError(
            f"unexpected tables in the JQ SQLite namespace: {sorted(unexpected)}"
        )


def _create_and_validate_tables(
    connection: sqlite3.Connection,
    dataset: JqEtlResult,
    types_by_table: Mapping[str, Mapping[str, str]],
) -> None:
    for batch in dataset.tables:
        logical_table = batch.table
        table = _table_name(logical_table)
        definitions: list[str] = []
        for column in batch.columns:
            column_type = types_by_table[logical_table][column]
            sql_type = "INTEGER" if column_type == "boolean" else "TEXT"
            not_null = " NOT NULL" if column in batch.primary_key else ""
            check = (
                f" CHECK ({_identifier(column)} IS NULL OR "
                f"{_identifier(column)} IN (0, 1))"
                if column_type == "boolean"
                else ""
            )
            definitions.append(
                f"{_identifier(column)} {sql_type}{not_null}{check}"
            )

        primary_key = ", ".join(
            _identifier(key) for key in batch.primary_key
        )
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"({', '.join(definitions)}, PRIMARY KEY ({primary_key})) STRICT"
        )

        actual = connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
        expected_columns = tuple(
            (
                column,
                "INTEGER" if types_by_table[logical_table][column] == "boolean" else "TEXT",
                1 if column in batch.primary_key else 0,
                batch.primary_key.index(column) + 1 if column in batch.primary_key else 0,
            )
            for column in batch.columns
        )
        actual_columns = tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in actual
        )
        if actual_columns != expected_columns:
            raise JqSQLitePublishError(
                f"existing table {TABLE_PREFIX}{logical_table} has an incompatible "
                f"schema: {actual_columns!r}; expected {expected_columns!r}"
            )

        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (f"{TABLE_PREFIX}{logical_table}",),
        ).fetchone()
        if not definition or not definition[0].rstrip().upper().endswith("STRICT"):
            raise JqSQLitePublishError(
                f"existing table {TABLE_PREFIX}{logical_table} is not STRICT"
            )


def _sqlite_value(value: Any, value_type: str, field: str) -> Any:
    if value is None:
        return None
    if value_type == "boolean" and type(value) is bool:
        return int(value)
    if value_type == "string" and isinstance(value, str):
        return value
    raise JqSQLitePublishError(
        f"{field} must contain {value_type} or None, found {type(value).__name__}"
    )


def replace_snapshot(
    connection: sqlite3.Connection,
    dataset: JqEtlResult,
) -> dict[str, int]:
    """Atomically replace all ten logical JQ tables in one SQLite transaction."""
    types_by_table = _validate_snapshot(dataset)
    if connection.in_transaction:
        raise JqSQLitePublishError(
            "replace_snapshot requires a connection without an active transaction"
        )

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = FULL")
    loaded: dict[str, int] = {}
    try:
        connection.execute("BEGIN IMMEDIATE")
        _validate_table_namespace(connection)
        _create_and_validate_tables(connection, dataset, types_by_table)

        for batch in dataset.tables:
            connection.execute(f"DELETE FROM {_table_name(batch.table)}")

        for batch in dataset.tables:
            columns = ", ".join(_identifier(column) for column in batch.columns)
            placeholders = ", ".join("?" for _ in batch.columns)
            statement = (
                f"INSERT INTO {_table_name(batch.table)} ({columns}) "
                f"VALUES ({placeholders})"
            )
            rows = [
                tuple(
                    _sqlite_value(
                        row[column],
                        types_by_table[batch.table][column],
                        f"{batch.table}.{column}",
                    )
                    for column in batch.columns
                )
                for row in batch.rows
            ]
            connection.executemany(statement, rows)
            loaded[batch.table] = len(rows)

        connection.commit()
    except BaseException:
        connection.rollback()
        raise

    return loaded


def connect_sqlite(database_path: str | Path) -> sqlite3.Connection:
    """Open an explicitly configured SQLite database."""
    raw_path = str(database_path).strip()
    if not raw_path:
        raise ValueError("SQLite database path must be configured explicitly")
    if raw_path == ":memory:":
        path = raw_path
    else:
        target = Path(raw_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        path = str(target)

    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def publish_snapshot(
    database_path: str | Path,
    dataset: JqEtlResult,
) -> dict[str, int]:
    """Open, publish and close a SQLite database in one operation."""
    connection = connect_sqlite(database_path)
    try:
        return replace_snapshot(connection, dataset)
    finally:
        connection.close()
