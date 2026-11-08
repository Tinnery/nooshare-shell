"""Run the current JQ domain pipeline against a real S3 input and ephemeral PostgreSQL."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from domains.jq import run_jq_etl
from domains.jq.adapters.postgres import connect_postgres, replace_snapshot
from domains.jq.adapters.rustfs import BUCKET, get_s3_client


def main() -> None:
    object_key = os.getenv("NOOSHARE_S3_OBJECT", "meta1").strip()
    if not object_key:
        raise RuntimeError("NOOSHARE_S3_OBJECT must not be empty")

    s3 = get_s3_client()
    print(f"Reading JQ input s3://{BUCKET}/{object_key}")

    with tempfile.TemporaryDirectory(prefix="nooshare-jq-ci-") as temporary:
        source = Path(temporary) / "jq-input"
        s3.download_file(BUCKET, object_key, str(source))

        result = run_jq_etl([source])
        if not result.tables:
            raise RuntimeError("JQ input produced no tables")

        connection = connect_postgres()
        try:
            expected_counts = replace_snapshot(connection, result)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'jq' ORDER BY table_name"
                )
                actual_tables = {row[0] for row in cursor.fetchall()}

                expected_tables = set(expected_counts)
                if actual_tables != expected_tables:
                    raise AssertionError(
                        f"PostgreSQL tables differ from JQ snapshot: "
                        f"expected={sorted(expected_tables)}, "
                        f"actual={sorted(actual_tables)}"
                    )

                verified_counts = {}
                for table, expected_count in sorted(expected_counts.items()):
                    cursor.execute(f'SELECT COUNT(*) FROM jq."{table}"')
                    actual_count = cursor.fetchone()[0]
                    if actual_count != expected_count:
                        raise AssertionError(
                            f"jq.{table} row count mismatch: "
                            f"expected={expected_count}, actual={actual_count}"
                        )
                    verified_counts[table] = actual_count

            # Replacing the same snapshot a second time verifies repeatability.
            repeated_counts = replace_snapshot(connection, result)
            if repeated_counts != expected_counts:
                raise AssertionError(
                    f"repeated JQ publication changed row counts: "
                    f"{repeated_counts!r} != {expected_counts!r}"
                )
        finally:
            connection.close()

    print(f"JQ domain integration passed: {len(verified_counts)} tables")
    for table, count in verified_counts.items():
        print(f"  jq.{table}: {count:,} rows")


if __name__ == "__main__":
    main()
