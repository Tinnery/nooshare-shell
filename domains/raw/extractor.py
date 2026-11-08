"""Raw bucket data extraction orchestrator.

Maps raw bucket objects to domain processors:
- jmeta → jq flow (base64+gzip+CSV format)
- tdmeta.zip → tong gbbq flow (same as gbbq.zip)
- tdohlcv-day.zip → tong ohlcv flow (same as hsjday.zip)
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

from ..jq.flow import run_jq_etl, JqEtlResult
from ..tong.extract import extract_zip, extract_tong_data
from ..tong.gbbq import decrypt_gbbq

logger = logging.getLogger(__name__)

# Raw bucket configuration
RAW_BUCKET = os.getenv("NOOSHARE_RAW_BUCKET", "raw")

# Object key mapping
JMETA_KEY = "jmeta"
TDMETA_KEY = "tdmeta.zip"
TDOHLCV_KEY = "tdohlcv-day.zip"


@dataclass
class RawExtractResult:
    """Result from raw bucket extraction."""
    jq_result: Optional[JqEtlResult] = None
    tong_result: Optional[dict] = None
    work_dir: Optional[Path] = None
    output_dir: Optional[Path] = None
    errors: list[str] = field(default_factory=list)


class RawExtractor:
    """Extract and process data from the raw bucket."""

    def __init__(
        self,
        s3_endpoint: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        s3_region: str | None = None,
        raw_bucket: str | None = None,
    ):
        self.s3_endpoint = s3_endpoint or os.getenv("NOOSHARE_S3_ENDPOINT", "http://localhost:9000")
        self.s3_access_key = s3_access_key or os.getenv("NOOSHARE_S3_ACCESS_KEY", "")
        self.s3_secret_key = s3_secret_key or os.getenv("NOOSHARE_S3_SECRET_KEY", "")
        self.s3_region = s3_region or os.getenv("NOOSHARE_S3_REGION", "us-east-1")
        self.raw_bucket = raw_bucket or RAW_BUCKET
        self._s3_client = None

    @property
    def s3_client(self):
        """Lazy S3 client initialization."""
        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.s3_endpoint,
                aws_access_key_id=self.s3_access_key,
                aws_secret_access_key=self.s3_secret_key,
                region_name=self.s3_region,
                config=Config(
                    connect_timeout=5,
                    read_timeout=60,
                    retries={"max_attempts": 3, "mode": "standard"},
                    s3={"addressing_style": "path"},
                    signature_version="s3v4",
                ),
            )
        return self._s3_client

    def download_object(self, key: str, dest_path: Path) -> Path:
        """Download a single object from raw bucket."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Downloading s3://{self.raw_bucket}/{key} -> {dest_path}")
        self.s3_client.download_file(self.raw_bucket, key, str(dest_path))
        return dest_path

    def extract_all(
        self,
        work_dir: Path,
        output_dir: Path,
        process_jmeta: bool = True,
        process_tong: bool = True,
        max_workers: int = 8,
    ) -> RawExtractResult:
        """Extract all data from raw bucket.

        Args:
            work_dir: Working directory for downloads and extraction
            output_dir: Output directory for processed data
            process_jmeta: Whether to process jmeta (JQ data)
            process_tong: Whether to process tdmeta.zip and tdohlcv-day.zip (Tong data)
            max_workers: Concurrent workers for Tong parsing

        Returns:
            RawExtractResult with jq and tong results
        """
        work_dir = Path(work_dir)
        output_dir = Path(output_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        result = RawExtractResult(work_dir=work_dir, output_dir=output_dir)

        # Download and process jmeta (JQ format)
        if process_jmeta:
            try:
                jmeta_path = work_dir / "jmeta"
                if not jmeta_path.exists():
                    self.download_object(JMETA_KEY, jmeta_path)
                logger.info("Processing jmeta with JQ flow...")
                jq_output = output_dir / "jq"
                result.jq_result = run_jq_etl([jmeta_path], output_dir=jq_output)
                logger.info(f"JQ processing complete: {len(result.jq_result.tables)} batches")
            except Exception as e:
                logger.error(f"Failed to process jmeta: {e}")
                result.errors.append(f"jmeta: {e}")

        # Download and process Tong data
        if process_tong:
            try:
                tdmeta_path = work_dir / "tdmeta.zip"
                tdohlcv_path = work_dir / "tdohlcv-day.zip"

                # Download if not present
                if not tdmeta_path.exists():
                    self.download_object(TDMETA_KEY, tdmeta_path)
                if not tdohlcv_path.exists():
                    self.download_object(TDOHLCV_KEY, tdohlcv_path)

                logger.info("Processing Tong data...")
                tong_output = output_dir / "tong"

                # Use extract_tong_data with the downloaded files
                # tdohlcv-day.zip is equivalent to hsjday.zip
                # tdmeta.zip is equivalent to gbbq.zip
                result.tong_result = extract_tong_data(
                    work_dir=work_dir / "tong_work",
                    output_dir=tong_output,
                    hsjday_zip=tdohlcv_path,
                    gbbq_zip=tdmeta_path,
                    max_workers=max_workers,
                )
                logger.info(f"Tong processing complete: {result.tong_result.get('total_symbols', 0)} symbols")
            except Exception as e:
                logger.error(f"Failed to process Tong data: {e}")
                result.errors.append(f"tong: {e}")

        return result


def extract_raw_data(
    work_dir: Path | str,
    output_dir: Path | str,
    **kwargs,
) -> RawExtractResult:
    """Convenience function to extract all data from raw bucket.

    Args:
        work_dir: Working directory for downloads and extraction
        output_dir: Output directory for processed data
        **kwargs: Additional arguments passed to RawExtractor.extract_all

    Returns:
        RawExtractResult with jq and tong results
    """
    extractor = RawExtractor()
    return extractor.extract_all(Path(work_dir), Path(output_dir), **kwargs)
