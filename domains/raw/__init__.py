"""Raw bucket data extraction orchestrator.

Handles files from the 'raw' bucket:
- jmeta: JQ format (base64+gzip+CSV), processed by jq flow
- tdmeta.zip: Tong gbbq data, processed by tong gbbq parser
- tdohlcv-day.zip: Tong .day files, processed by tong day parser
"""

from .extractor import extract_raw_data, RawExtractor

__all__ = ["extract_raw_data", "RawExtractor"]
