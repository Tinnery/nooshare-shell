"""Run the current Tong domain pipeline against S3 and ephemeral PostgreSQL."""

from __future__ import annotations

import tempfile
from pathlib import Path

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

    counts = result.get("postgresql")
    if not isinstance(counts, dict) or not counts:
        raise AssertionError("Tong pipeline did not publish PostgreSQL tables")

    expected_tables = {
        "daily",
        "daily_hfq",
        "daily_qfq",
        "gbbq",
        "symbol_class",
    }
    if set(counts) != expected_tables:
        raise AssertionError(
            f"Tong published unexpected table set: {sorted(counts)}"
        )

    print("Tong domain integration passed")
    for table, count in sorted(counts.items()):
        print(f"  tong.{table}: {count:,} rows")


if __name__ == "__main__":
    main()
