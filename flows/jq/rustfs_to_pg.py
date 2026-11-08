"""Download JQ data from RustFS, decode, and write to PostgreSQL.

This module implements the complete flow:
1. Download JQ format data from RustFS (S3-compatible)
2. Decode base64 + gzip + CSV segments
3. Write decoded tables to PostgreSQL

Usage:
    from flows.jq import run_rustfs_to_pg
    
    run_rustfs_to_pg(
        endpoint_url="...",
        access_key_id="...",
        secret_access_key="...",
        bucket="tong-data",
        object_key="meta1",
        database="nooshare_test"
    )
"""

import hashlib
import io
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .models import InputReceipt, JqEtlResult, SegmentReceipt, TableBatch
from .parser import decode_segment, split_tags
from .transform import derive_report_period, shape_segment, TAG_SPECS
from .adapters.rustfs import get_s3_client
from .adapters.postgres import connect_postgres


def _csv_escape(value: Optional[str]) -> str:
    """Escape a value for CSV COPY protocol."""
    if value is None:
        return ""
    s = str(value)
    if "," in s or '"' in s or "\n" in s:
        return '"' + s.replace('"', '""') + '"'
    return s


def run_jq_etl(paths: list[Path]) -> JqEtlResult:
    """Execute the JQ ETL pipeline on the given input files.
    
    Args:
        paths: List of paths to JQ format files
        
    Returns:
        JqEtlResult containing all decoded tables
    """
    input_files = []
    segments = []
    all_batches = []
    
    for path in paths:
        path = Path(path)
        content = path.read_text(encoding="utf-8")
        size_bytes = path.stat().st_size
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        
        # Parse #tag sections
        tag_sections = split_tags(content)
        tags = tuple(tag_sections.keys())
        
        input_files.append(InputReceipt(
            path=str(path),
            size_bytes=size_bytes,
            sha256=sha256,
            tags=tags,
        ))
        
        # Decode each segment
        for tag, encoded in tag_sections.items():
            if tag not in TAG_SPECS:
                print(f"  ⚠ 跳过未知 tag: {tag}")
                continue
            
            segment = decode_segment(encoded)
            segments.append(SegmentReceipt(
                source=str(path),
                tag=tag,
                encoded_chars=len(encoded),
                rows=len(segment.rows),
                columns=segment.columns,
            ))
            
            # Transform to table batch
            batch = shape_segment(tag, segment.rows, str(path))
            all_batches.append(batch)
    
    # Derive report_period from pub_dates + stocks
    pub_dates_batch = next((b for b in all_batches if b.table == "pub_date"), None)
    stocks_batch = next((b for b in all_batches if b.table == "stocks"), None)
    
    if pub_dates_batch and stocks_batch:
        report_period_batch = derive_report_period(pub_dates_batch, stocks_batch)
        all_batches.append(report_period_batch)
    
    return JqEtlResult(
        input_files=tuple(input_files),
        segments=tuple(segments),
        tables=tuple(all_batches),
    )


def run_rustfs_to_pg(
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    object_key: str,
    database: str,
    schema: str = "jq",
    verbose: bool = True,
) -> dict[str, int]:
    """Execute the complete RustFS → PostgreSQL flow.
    
    Args:
        endpoint_url: RustFS/S3 endpoint URL
        access_key_id: S3 access key
        secret_access_key: S3 secret key
        bucket: S3 bucket name
        object_key: Object key to download (e.g., "meta1")
        database: PostgreSQL database name
        schema: Target schema (default: "jq")
        verbose: Print progress messages
        
    Returns:
        Dictionary mapping table names to row counts
        
    Raises:
        Exception: If download, decode, or write fails
    """
    if verbose:
        print("=== JQ RustFS → PostgreSQL Flow ===\n")
        print(f"1. 连接 RustFS ({endpoint_url})...")
    
    # 1. Connect to RustFS
    s3 = get_s3_client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    
    # 2. Download object
    if verbose:
        print(f"2. 从 bucket={bucket} 下载 {object_key}...")
    
    with tempfile.TemporaryDirectory(prefix="jq-download-") as tmpdir:
        root = Path(tmpdir)
        local_file = root / object_key
        
        s3.download_file(bucket, object_key, str(local_file))
        file_size = local_file.stat().st_size
        
        if verbose:
            print(f"   下载完成: {local_file.name} ({file_size:,} bytes)")
        
        # 3. Decode JQ data
        if verbose:
            print("\n3. 解码 JQ 数据...")
        
        result = run_jq_etl([local_file])
        
        if verbose:
            print(f"   输入文件: {len(result.input_files)}")
            print(f"   数据段: {len(result.segments)}")
            print(f"   输出表: {len(result.tables)}")
            print(f"   总行数: {result.total_output_rows:,}")
            
            for table, count in result.table_row_counts.items():
                print(f"   - {table}: {count:,} 行")
        
        # 4. Write to PostgreSQL
        if verbose:
            print(f"\n4. 写入 PostgreSQL (database={database}, schema={schema})...")
        
        # Set environment variable for connect_postgres
        old_db = os.environ.get("NOOSHARE_PG_DATABASE")
        os.environ["NOOSHARE_PG_DATABASE"] = database
        
        try:
            conn = connect_postgres()
            
            try:
                # Create schema
                with conn.cursor() as cur:
                    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                conn.commit()
                
                loaded = {}
                for batch in result.tables:
                    table_name = batch.table
                    columns = batch.columns
                    qualified = f"{schema}.{table_name}"
                    
                    # Drop and recreate table (clean slate)
                    col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
                    with conn.cursor() as cur:
                        cur.execute(f"DROP TABLE IF EXISTS {qualified}")
                        cur.execute(f"CREATE TABLE {qualified} ({col_defs})")
                    conn.commit()
                    
                    # Use COPY protocol for bulk insert
                    cols = ", ".join(f'"{c}"' for c in columns)
                    copy_sql = f"COPY {qualified} ({cols}) FROM STDIN WITH (FORMAT CSV)"
                    
                    # Build CSV data in memory
                    csv_buffer = io.StringIO()
                    rows_written = 0
                    for row in batch.rows:
                        csv_line = ",".join(_csv_escape(v) for v in row)
                        csv_buffer.write(csv_line + "\n")
                        rows_written += 1
                    csv_buffer.seek(0)
                    
                    with conn.cursor() as cur:
                        cur.copy_expert(copy_sql, csv_buffer)
                    conn.commit()
                    
                    loaded[table_name] = rows_written
                    if verbose:
                        print(f"   ✓ {table_name}: {rows_written:,} 行")
                
                if verbose:
                    print(f"\n   成功写入 {len(loaded)} 个表")
                
                return loaded
            
            finally:
                conn.close()
        
        finally:
            # Restore original environment
            if old_db is None:
                os.environ.pop("NOOSHARE_PG_DATABASE", None)
            else:
                os.environ["NOOSHARE_PG_DATABASE"] = old_db


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Download JQ data from RustFS and write to PostgreSQL"
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("NOOSHARE_S3_ENDPOINT"),
        help="RustFS endpoint URL (env: NOOSHARE_S3_ENDPOINT)",
    )
    parser.add_argument(
        "--access-key",
        default=os.environ.get("NOOSHARE_S3_ACCESS_KEY"),
        help="S3 access key (env: NOOSHARE_S3_ACCESS_KEY)",
    )
    parser.add_argument(
        "--secret-key",
        default=os.environ.get("NOOSHARE_S3_SECRET_KEY"),
        help="S3 secret key (env: NOOSHARE_S3_SECRET_KEY)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("NOOSHARE_S3_BUCKET", "tong-data"),
        help="S3 bucket name (env: NOOSHARE_S3_BUCKET, default: tong-data)",
    )
    parser.add_argument(
        "--object",
        default="meta1",
        help="Object key to download (default: meta1)",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("NOOSHARE_PG_DATABASE", "nooshare_test"),
        help="PostgreSQL database name (env: NOOSHARE_PG_DATABASE, default: nooshare_test)",
    )
    parser.add_argument("--schema", default="jq", help="Target schema")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    
    args = parser.parse_args()
    
    # Validate required credentials
    if not all([args.endpoint, args.access_key, args.secret_key]):
        print(
            "错误: 缺少必要的凭证。请设置环境变量或通过命令行参数提供：\n"
            "  NOOSHARE_S3_ENDPOINT / --endpoint\n"
            "  NOOSHARE_S3_ACCESS_KEY / --access-key\n"
            "  NOOSHARE_S3_SECRET_KEY / --secret-key",
            file=sys.stderr,
        )
        return 1
    
    try:
        loaded = run_rustfs_to_pg(
            endpoint_url=args.endpoint,
            access_key_id=args.access_key,
            secret_access_key=args.secret_key,
            bucket=args.bucket,
            object_key=args.object,
            database=args.database,
            schema=args.schema,
            verbose=not args.quiet,
        )
        
        if not args.quiet:
            print("\n=== 完成 ===")
        
        return 0
    
    except Exception as e:
        print(f"\n错误: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
