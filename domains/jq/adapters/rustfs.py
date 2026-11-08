"""S3-compatible client and object-prefix helpers used by JQ Flows."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

import boto3
from botocore.config import Config


BUCKET = os.getenv("NOOSHARE_S3_BUCKET", "raw").strip()
RUSTFS_REGION = os.getenv("NOOSHARE_S3_REGION", "us-east-1").strip()


def get_s3_client(
    *,
    endpoint_url: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    region_name: str | None = None,
):
    """Create an authenticated path-style client for the configured S3 endpoint."""
    settings = {
        "NOOSHARE_S3_ENDPOINT": endpoint_url or os.getenv("NOOSHARE_S3_ENDPOINT"),
        "NOOSHARE_S3_ACCESS_KEY": access_key_id or os.getenv("NOOSHARE_S3_ACCESS_KEY"),
        "NOOSHARE_S3_SECRET_KEY": secret_access_key or os.getenv("NOOSHARE_S3_SECRET_KEY"),
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError(
            "Missing required S3 configuration: " + ", ".join(missing)
        )
    if not BUCKET:
        raise RuntimeError("NOOSHARE_S3_BUCKET must not be empty")

    return boto3.client(
        "s3",
        endpoint_url=settings["NOOSHARE_S3_ENDPOINT"],
        aws_access_key_id=settings["NOOSHARE_S3_ACCESS_KEY"],
        aws_secret_access_key=settings["NOOSHARE_S3_SECRET_KEY"],
        region_name=region_name or RUSTFS_REGION or "us-east-1",
        config=Config(
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
            signature_version="s3v4",
        ),
    )


def download_prefix(s3, bucket: str, prefix: str, dest_dir: Path) -> list[Path]:
    """Download every object under prefix, preserving its relative key path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=prefix,
        PaginationConfig={"PageSize": 1_000},
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.startswith(prefix):
                raise ValueError(f"object key is outside requested prefix: {key!r}")
            relative_key = key[len(prefix):]
            relative = PurePosixPath(relative_key)
            if (
                not relative_key
                or relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ValueError(f"unsafe object key below prefix {prefix!r}: {key!r}")

            local_path = dest_dir.joinpath(*relative.parts)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(local_path))
            paths.append(local_path)
            print(f"  downloaded {key} ({obj['Size']:,} bytes)")
    return sorted(paths)


def upload_files(s3, bucket: str, prefix: str, files: list[Path]) -> None:
    """Upload local files under prefix using each file's basename as object key."""
    for file_path in files:
        key = f"{prefix}{file_path.name}"
        s3.upload_file(str(file_path), bucket, key)
        print(f"  uploaded {file_path.name} to s3://{bucket}/{key}")
