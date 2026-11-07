"""PostgreSQL adapter for publishing validated JQ snapshots.

This module owns PostgreSQL DDL, schema/key compatibility checks, COPY
serialization and the atomic snapshot replacement transaction. It does not
own RustFS access or command-line execution.
"""

from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Mapping, Sequence

from ..models import JqEtlResult, TableBatch
from ..transform import REPORT_PERIOD_SPEC, TAG_SPECS

SCHEMA = "jq"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_TABLE_SPECS = {
    spec.table: spec
    for spec in (*TAG_SPECS.values(), REPORT_PERIOD_SPEC)
}


class JqPostgresPublishError(RuntimeError):
    """Raised when a JQ snapshot cannot be safely published."""


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise JqPostgresPublishError(f"unsafe PostgreSQL identifier: {value!r}")
    return f'"{value}"'


def _column_types(batch: TableBatch) -> dict[str, str]:
    """Infer the supported PostgreSQL types from validated logical values."""
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
            raise JqPostgresPublishError(
                f"{batch.table}.{column} contains unsupported value types: {names}"
            )
    return result


def _validate_snapshot_contract(dataset: JqEtlResult) -> dict[str, dict[str, str]]:
    """Require the complete known JQ snapshot and exact ETL key contracts."""
    batches = {batch.table: batch for batch in dataset.tables}
    expected_tables = set(_TABLE_SPECS)
    actual_tables = set(batches)
    if actual_tables != expected_tables:
        raise JqPostgresPublishError(
            "incomplete or unexpected JQ snapshot: "
            f"missing={sorted(expected_tables - actual_tables)}, "
            f"unexpected={sorted(actual_tables - expected_tables)}"
        )

    types_by_table: dict[str, dict[str, str]] = {}
    for table, batch in batches.items():
        spec = _TABLE_SPECS[table]
        if batch.columns != spec.columns:
            raise JqPostgresPublishError(
                f"{table} columns differ from the ETL contract: "
                f"{batch.columns!r} != {spec.columns!r}"
            )
        if batch.primary_key != spec.primary_key:
            raise JqPostgresPublishError(
                f"{table} primary key differs from the ETL contract: "
                f"{batch.primary_key!r} != {spec.primary_key!r}"
            )
        types_by_table[table] = _column_types(batch)
    return types_by_table


def _expected_primary_key(cursor, table: str) -> tuple[str, ...]:
    cursor.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
         AND kcu.table_name = tc.table_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = %s
          AND tc.table_name = %s
        ORDER BY kcu.ordinal_position
        """,
        (SCHEMA, table),
    )
    return tuple(row[0] for row in cursor.fetchall())


def _create_and_validate_tables(
    cursor,
    dataset: JqEtlResult,
    types_by_table: Mapping[str, Mapping[str, str]],
) -> None:
    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {_identifier(SCHEMA)}")
    for batch in dataset.tables:
        table = batch.table
        columns = []
        for column in batch.columns:
            sql_type = "BOOLEAN" if types_by_table[table][column] == "boolean" else "TEXT"
            columns.append(f"{_identifier(column)} {sql_type}")
        primary_key = ", ".join(_identifier(key) for key in batch.primary_key)
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {_identifier(SCHEMA)}.{_identifier(table)} "
            f"({', '.join(columns)}, PRIMARY KEY ({primary_key}))"
        )
        cursor.execute(ddl)

        cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (SCHEMA, table),
        )
        actual_columns = tuple(
            (name, data_type) for name, data_type in cursor.fetchall()
        )
        expected_columns = tuple(
            (
                column,
                "boolean" if types_by_table[table][column] == "boolean" else "text",
            )
            for column in batch.columns
        )
        if actual_columns != expected_columns:
            raise JqPostgresPublishError(
                f"existing table {SCHEMA}.{table} has an incompatible schema: "
                f"{actual_columns!r}; expected {expected_columns!r}"
            )

        actual_primary_key = _expected_primary_key(cursor, table)
        if actual_primary_key != batch.primary_key:
            raise JqPostgresPublishError(
                f"existing table {SCHEMA}.{table} has primary key "
                f"{actual_primary_key!r}; expected {batch.primary_key!r}"
            )


def _copy_table(
    cursor,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    column_types: Mapping[str, str],
) -> int:
    """COPY CSV with QUOTE_ALL, using an unquoted marker exclusively for NULL."""
    if not rows:
        return 0

    null_token = "__NOOSHARE_COPY_NULL__"
    while any(
        isinstance(value, str) and value == null_token
        for row in rows
        for value in row.values()
    ):
        null_token += "_"

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        values: list[str] = []
        for column in columns:
            value = row[column]
            if value is None:
                values.append(null_token)
            elif column_types[column] == "boolean":
                if type(value) is not bool:
                    raise JqPostgresPublishError(
                        f"{table}.{column} must contain bool or None"
                    )
                values.append("true" if value else "false")
            elif column_types[column] == "string":
                if not isinstance(value, str):
                    raise JqPostgresPublishError(
                        f"{table}.{column} must contain str or None"
                    )
                values.append(value)
            else:
                raise JqPostgresPublishError(
                    f"unsupported type for {table}.{column}: {column_types[column]!r}"
                )
        writer.writerow(values)

    payload = buffer.getvalue().replace(f'"{null_token}"', r"\N")
    buffer.close()

    quoted_columns = ", ".join(_identifier(column) for column in columns)
    copy_sql = (
        f"COPY {_identifier(SCHEMA)}.{_identifier(table)} ({quoted_columns}) "
        r"FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '\N', ENCODING 'UTF8')"
    )
    with io.StringIO(payload, newline="") as copy_stream:
        cursor.copy_expert(copy_sql, copy_stream)
    return len(rows)


def replace_snapshot(conn, dataset: JqEtlResult) -> dict[str, int]:
    """Replace all ten logical tables in one PostgreSQL transaction."""
    types_by_table = _validate_snapshot_contract(dataset)
    quoted_tables = ", ".join(
        f"{_identifier(SCHEMA)}.{_identifier(batch.table)}"
        for batch in dataset.tables
    )
    loaded: dict[str, int] = {}

    # psycopg2's connection context commits on success and rolls back on failure.
    with conn:
        with conn.cursor() as cursor:
            _create_and_validate_tables(cursor, dataset, types_by_table)
            cursor.execute(f"TRUNCATE TABLE {quoted_tables}")
            for batch in dataset.tables:
                loaded[batch.table] = _copy_table(
                    cursor,
                    batch.table,
                    batch.columns,
                    batch.rows,
                    types_by_table[batch.table],
                )

    return loaded


def postgres_config() -> dict[str, Any]:
    """Read connection settings from NooShare-specific or standard PG variables."""
    database = os.getenv("NOOSHARE_PG_DATABASE") or os.getenv("PGDATABASE")
    if not database:
        raise RuntimeError(
            "Set NOOSHARE_PG_DATABASE or PGDATABASE explicitly. "
            "No default database is selected for NooShare writes."
        )
    config: dict[str, Any] = {
        "dbname": database,
        "user": os.getenv("NOOSHARE_PG_USER", os.getenv("PGUSER", "travis")),
        "connect_timeout": int(os.getenv("NOOSHARE_PG_CONNECT_TIMEOUT", "10")),
        "application_name": "nooshare-jq-flow",
    }
    values = {
        "host": ("NOOSHARE_PG_HOST", "PGHOST"),
        "port": ("NOOSHARE_PG_PORT", "PGPORT"),
        "password": ("NOOSHARE_PG_PASSWORD", "PGPASSWORD"),
        "sslmode": ("NOOSHARE_PG_SSLMODE", "PGSSLMODE"),
    }
    for key, env_names in values.items():
        value = next((os.getenv(name) for name in env_names if os.getenv(name)), None)
        if value is not None:
            config[key] = int(value) if key == "port" else value
    return config


def connect_postgres():
    """Open a PostgreSQL connection using the NooShare/PG environment config."""
    import psycopg2

    return psycopg2.connect(**postgres_config())
