"""Dagster assets for nooshare-shell flows."""

import os
from dagster import asset, Output, MetadataValue, EnvVar
from flows.jq import run_rustfs_to_pg


@asset(
    name="jq_rustfs_to_pg",
    group_name="jq_etl",
    description="Download JQ data from RustFS (S3-compatible) and write to PostgreSQL",
)
def jq_rustfs_to_pg_asset(context) -> Output[dict]:
    """Dagster asset that executes the JQ RustFS → PostgreSQL flow.
    
    This asset:
    1. Downloads JQ format data from RustFS/S3
    2. Decodes base64 + gzip + CSV segments
    3. Writes decoded tables to PostgreSQL
    
    Environment variables required:
        NOOSHARE_S3_ENDPOINT: RustFS/S3 endpoint URL
        NOOSHARE_S3_ACCESS_KEY: S3 access key
        NOOSHARE_S3_SECRET_KEY: S3 secret key
        NOOSHARE_S3_BUCKET: S3 bucket name (default: tong-data)
        NOOSHARE_PG_DATABASE: PostgreSQL database name (default: nooshare_test)
    
    Returns:
        Dictionary mapping table names to row counts
    """
    # Read from environment variables
    endpoint_url = os.environ["NOOSHARE_S3_ENDPOINT"]
    access_key_id = os.environ["NOOSHARE_S3_ACCESS_KEY"]
    secret_access_key = os.environ["NOOSHARE_S3_SECRET_KEY"]
    bucket = os.environ.get("NOOSHARE_S3_BUCKET", "tong-data")
    object_key = "meta1"
    database = os.environ.get("NOOSHARE_PG_DATABASE", "nooshare_test")
    schema = "jq"
    
    context.log.info(f"Starting JQ ETL: {bucket}/{object_key} → {database}.{schema}")
    context.log.info(f"Endpoint: {endpoint_url}")
    
    # Execute the flow
    try:
        loaded = run_rustfs_to_pg(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket=bucket,
            object_key=object_key,
            database=database,
            schema=schema,
            verbose=True,
        )
        
        total_rows = sum(loaded.values())
        
        context.log.info(f"✓ Successfully loaded {total_rows:,} rows into {len(loaded)} tables")
        for table, count in loaded.items():
            context.log.info(f"  - {table}: {count:,} rows")
        
        # Return with metadata
        return Output(
            value=loaded,
            metadata={
                "tables_loaded": len(loaded),
                "total_rows": total_rows,
                "table_details": MetadataValue.md(
                    "\n".join(f"- **{table}**: {count:,} rows" for table, count in loaded.items())
                ),
                "database": database,
                "schema": schema,
            },
        )
    
    except Exception as e:
        context.log.error(f"✗ JQ ETL failed: {type(e).__name__}: {e}")
        raise
