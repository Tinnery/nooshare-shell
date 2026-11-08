"""Run the current Tong domain pipeline against S3 and ephemeral PostgreSQL."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
from psycopg2 import sql

from domains.tong.flow import run_full_pipeline
from domains.tong.adapters.postgres import postgres_config


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nooshare-tong-ci-") as temporary:
        root = Path(temporary)
        result = run_full_pipeline(
            work_dir=root / "work",
            output_dir=root / "output",
            max_workers=8,
            write_to_pg=True,
            pg_config=postgres_config(),
            replace_snapshot=True,
        )

    expected_counts = result.get("postgresql")
    expected_tables = {
        "daily",
        "daily_hfq",
        "daily_qfq",
        "gbbq",
        "symbol_class",
    }
    if not isinstance(expected_counts, dict) or set(expected_counts) != expected_tables:
        raise AssertionError(
            f"Tong published unexpected table set: {sorted(expected_counts or {})}"
        )

    verified_counts = {}
    with psycopg2.connect(**postgres_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'tong' ORDER BY table_name"
            )
            actual_tables = {row[0] for row in cursor.fetchall()}
            if actual_tables != expected_tables:
                raise AssertionError(
                    f"Tong PostgreSQL tables differ: "
                    f"expected={sorted(expected_tables)}, "
                    f"actual={sorted(actual_tables)}"
                )

            for table, expected_count in sorted(expected_counts.items()):
                query = sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier("tong"),
                    sql.Identifier(table),
                )
                cursor.execute(query)
                actual_count = cursor.fetchone()[0]
                if actual_count != expected_count:
                    raise AssertionError(
                        f"tong.{table} row count mismatch: "
                        f"expected={expected_count}, actual={actual_count}"
                    )
                verified_counts[table] = actual_count

    print("Tong domain integration passed")
    for table, count in verified_counts.items():
        print(f"  tong.{table}: {count:,} rows")


if __name__ == "__main__":
    main()
