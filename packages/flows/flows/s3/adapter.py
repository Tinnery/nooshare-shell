"""S3/RustFS adapter implementation."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


@dataclass
class S3Config:
    """S3/RustFS connection configuration."""
    
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    
    @classmethod
    def from_env(cls) -> "S3Config":
        """Load configuration from environment variables."""
        import os
        return cls(
            endpoint=os.getenv("FLOWS_S3_ENDPOINT", ""),
            access_key=os.getenv("FLOWS_S3_ACCESS_KEY", "readonly"),
            secret_key=os.getenv("FLOWS_S3_SECRET_KEY", "readonly2026"),
            bucket=os.getenv("FLOWS_S3_BUCKET", "tong-data"),
            region=os.getenv("FLOWS_S3_REGION", "us-east-1"),
        )


class S3Adapter:
    """Adapter for S3/RustFS operations."""
    
    def __init__(self, config: S3Config):
        self.config = config
        self._client = None
    
    def _get_client(self):
        """Get or create S3 client."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
                config=Config(
                    connect_timeout=5,
                    read_timeout=60,
                    retries={"max_attempts": 3, "mode": "standard"},
                    s3={"addressing_style": "path"},
                    signature_version="s3v4",
                ),
            )
        return self._client
    
    def download_file(self, s3_key: str, local_path: Path) -> Path:
        """Download a file from S3 to local path.
        
        Args:
            s3_key: S3 object key (e.g., "hsjday.zip")
            local_path: Local destination path
            
        Returns:
            Path to downloaded file
        """
        client = self._get_client()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Downloading s3://{self.config.bucket}/{s3_key} -> {local_path}")
        client.download_file(self.config.bucket, s3_key, str(local_path))
        
        return local_path
    
    def list_objects(self, prefix: str = "") -> list[str]:
        """List objects in bucket with optional prefix.
        
        Args:
            prefix: Object key prefix filter
            
        Returns:
            List of object keys
        """
        client = self._get_client()
        response = client.list_objects_v2(
            Bucket=self.config.bucket,
            Prefix=prefix,
        )
        
        return [obj["Key"] for obj in response.get("Contents", [])]
