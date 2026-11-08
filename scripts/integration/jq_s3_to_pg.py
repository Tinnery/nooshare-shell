"""Run the current JQ domain pipeline against a real S3 input and ephemeral PostgreSQL."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from botocore.exceptions import ClientError

from domains.jq import run_jq_etl
from domains.jq.adapters.postgres import connect_postgres, replace_snapshot
from domains.jq.adapters.rustfs import BUCKET, get_s3_client


def _resolve_object_key(s3, requested_key: str) -> str:
    """Resolve an exact key or a unique object with the requested basename."""
    paginator = s3.get_paginator("list_objects_v2")
    exact_match = False
    basename_matches: list[str] = []
    preview: list[str] = []

    for page in paginator.paginate(Bucket=BUCKET, PaginationConfig={"PageSize": 1000}):
        for item in page.get("Contents", []):
            key = item["Key"]
            if len(preview) < 50:
                preview.append(key)
            if key == requested_key:
                exact_match = True
            elif key.rsplit("/", 1)[-1] == requested_key:
                basename_matches.append(key)

    if exact_match:
        return requested_key
    if len(basename_matches) == 1:
        resolved = basename_matches[0]
        print(f"Resolved object basename {requested_key!r} to {resolved!r}")
        return resolved

    if len(basename_matches) > 1:
        raise RuntimeError(
            f"Multiple S3 objects have basename {requested_key!r}: "
            f"{sorted(basename_matches)}"
        )

    raise RuntimeError(
        f"S3 object {requested_key!r} was not found in bucket {BUCKET!r}. "
        f"First {len(preview)} available keys: {preview}"
    )


def main() -> None:
    requested_key = os.getenv("NOOSHARE_S3_OBJECT", "meta1").strip()
    if not requested_key:
        raise RuntimeError("NOOSHARE_S3_OBJECT must not be empty")

    s3 = get_s3_client()
    object_key = _resolve_object_key(s3, requested_key)
    print(f"Reading JQ input s3://{BUCKET}/{object_key}")

    with tempfile.TemporaryDirectory(prefix="nooshare-jq-ci-") as temporary:
        source = Path(temporary) / "jq-input"
        try:
            s3.download_file(BUCKET, object_key, str(source))
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise RuntimeError(
                f"Could not download s3://{BUCKET}/{object_key}: {error_code}"
            ) from exc

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
