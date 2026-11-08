"""Runtime orchestration for the two JQ service flows.

The core parser/transformer remains independent of service SDKs. This module
composes the RustFS and PostgreSQL adapters around the manifest-backed CSV
snapshot contract.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .adapters.postgres import connect_postgres, replace_snapshot
from .adapters.rustfs import BUCKET, download_prefix, get_s3_client, upload_files
from . import read_jq_csv_dataset, run_jq_etl
from .csv_io import export_jq_csv_dataset
from .models import JqEtlResult


def decode_rustfs_snapshot(*, s3_client: Any | None = None) -> JqEtlResult:
    """Decode RustFS raw objects and publish one manifest-backed CSV snapshot."""
    s3 = s3_client if s3_client is not None else get_s3_client()
    with tempfile.TemporaryDirectory(prefix="nooshare-jq-decode-") as tmpdir:
        root = Path(tmpdir)
        raw_files = download_prefix(s3, BUCKET, "raw/", root / "raw")
        if not raw_files:
            raise RuntimeError("RustFS raw/ prefix contains no JQ input files")

        result = run_jq_etl(raw_files)
        csv_dir = root / "csv"
        export_jq_csv_dataset(result, csv_dir)
        csv_files = sorted(csv_dir.glob("*.csv"))
        upload_files(s3, BUCKET, "csv/", csv_files)
        manifest = csv_dir / "manifest.json"
        s3.upload_file(str(manifest), BUCKET, "csv/manifest.json")
        return result


def publish_rustfs_snapshot(*, s3_client: Any | None = None) -> dict[str, int]:
    """Read a complete CSV snapshot from RustFS and replace PostgreSQL atomically."""
    s3 = s3_client if s3_client is not None else get_s3_client()
    with tempfile.TemporaryDirectory(prefix="nooshare-jq-publish-") as tmpdir:
        csv_dir = Path(tmpdir) / "csv"
        downloaded = download_prefix(s3, BUCKET, "csv/", csv_dir)
        if not downloaded:
            raise RuntimeError("RustFS csv/ prefix contains no files")
        dataset = read_jq_csv_dataset(csv_dir)
        connection = connect_postgres()
        try:
            return replace_snapshot(connection, dataset)
        finally:
            connection.close()
