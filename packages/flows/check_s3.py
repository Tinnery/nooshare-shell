#!/usr/bin/env python3
"""Check S3 bucket contents."""
from flows.s3 import S3Adapter, S3Config

config = S3Config.from_env()
adapter = S3Adapter(config)

print(f"Checking bucket: {config.bucket}")
print(f"Endpoint: {config.endpoint}")
print()

try:
    objects = adapter.list_objects()
    print(f"Found {len(objects)} objects:")
    for obj in objects[:20]:  # Show first 20
        print(f"  - {obj}")
    if len(objects) > 20:
        print(f"  ... and {len(objects) - 20} more")
except Exception as e:
    print(f"Error: {e}")
