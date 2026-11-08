"""Dagster configuration for nooshare-shell flows."""

import os
from dagster import Config


class JqEtlConfig(Config):
    """Configuration schema for JQ ETL flow."""
    
    endpoint_url: str = os.environ.get("NOOSHARE_S3_ENDPOINT", "")
    access_key_id: str = os.environ.get("NOOSHARE_S3_ACCESS_KEY", "")
    secret_access_key: str = os.environ.get("NOOSHARE_S3_SECRET_KEY", "")
    bucket: str = os.environ.get("NOOSHARE_RAW_BUCKET", "raw")
    object_key: str = "meta1"
    database: str = os.environ.get("NOOSHARE_PG_DATABASE", "nooshare_test")
    schema: str = "jq"
