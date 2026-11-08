#!/usr/bin/env python3
"""CLI entry point for JQ RustFS → PostgreSQL flow.

Usage:
    # Set environment variables first
    export NOOSHARE_S3_ENDPOINT="..."
    export NOOSHARE_S3_ACCESS_KEY="..."
    export NOOSHARE_S3_SECRET_KEY="..."
    
    # Run the flow
    python scripts/jq/flow_rustfs_to_pg.py --bucket tong-data --object meta1 --database nooshare_test
    
    # Or override via CLI arguments
    python scripts/jq/flow_rustfs_to_pg.py \\
        --endpoint "..." \\
        --access-key "..." \\
        --secret-key "..." \\
        --bucket tong-data \\
        --object meta1 \\
        --database nooshare_test
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from flows.jq import run_rustfs_to_pg


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Download JQ data from RustFS and write to PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
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
        default="tong-data",
        help="S3 bucket name (default: tong-data)",
    )
    parser.add_argument(
        "--object",
        default="meta1",
        help="Object key to download (default: meta1)",
    )
    parser.add_argument(
        "--database",
        default="nooshare_test",
        help="PostgreSQL database name (default: nooshare_test)",
    )
    parser.add_argument(
        "--schema",
        default="jq",
        help="Target schema (default: jq)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )
    
    args = parser.parse_args()
    
    # Validate required credentials
    if not all([args.endpoint, args.access_key, args.secret_key]):
        print(
            "错误: 缺少必要的 S3 凭证。请设置环境变量或通过命令行参数提供：\n"
            "  NOOSHARE_S3_ENDPOINT / --endpoint\n"
            "  NOOSHARE_S3_ACCESS_KEY / --access-key\n"
            "  NOOSHARE_S3_SECRET_KEY / --secret-key",
            file=sys.stderr,
        )
        return 1
    
    print("=" * 60)
    print("JQ RustFS → PostgreSQL Flow")
    print("=" * 60)
    print(f"Endpoint:  {args.endpoint}")
    print(f"Bucket:    {args.bucket}")
    print(f"Object:    {args.object}")
    print(f"Database:  {args.database}")
    print(f"Schema:    {args.schema}")
    print("=" * 60)
    print()
    
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
        
        print()
        print("=" * 60)
        print("✓ 完成")
        print("=" * 60)
        print(f"共写入 {len(loaded)} 个表:")
        for table, rows in loaded.items():
            print(f"  - {table}: {rows:,} 行")
        
        return 0
    
    except Exception as e:
        print(f"\n✗ 错误: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
