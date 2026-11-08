from __future__ import annotations

import base64
import csv
import gzip
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domains.jq import export_jq_csv_dataset, read_jq_csv_dataset, run_jq_etl
from domains.jq.adapters.sqlite import connect_sqlite, replace_snapshot
from domains.tong.gbbq import _load_hex_keys


_TAG_ROWS = {
    "codes": (
        ("code", "display_name", "start_date", "end_date"),
        ("600000.XSHG", "Example Bank", "2000-01-01", "2200-01-01"),
    ),
    "indices": (
        ("code", "display_name", "start_date", "end_date"),
        ("000001", "Example Index", "2000-01-01", "2200-01-01"),
    ),
    "funds": (
        ("code", "display_name", "type", "start_date", "end_date"),
        ("510050", "Example ETF", "etf", "2000-01-01", "2200-01-01"),
    ),
    "dates": (
        ("time",),
        ("2026-11-06",),
    ),
    "pub_dates": (
        ("code", "period", "pubDate"),
        ("600000", "2025Q1", "2025-04-30"),
    ),
    "limit_ratio": (
        ("code", "start_date", "end_date", "limit_ratio"),
        ("600000", "2020-01-01", "2200-01-01", "10"),
    ),
    "st_periods": (
        ("code", "start_date", "end_date"),
        ("600000", "2020-01-01", "2200-01-01"),
    ),
    "valuation": (
        ("code", "start_date", "end_date", "capitalization", "circulating_cap"),
        ("600000", "2020-01-01", "2200-01-01", "123.45", "67.89"),
    ),
    "name_history": (
        ("code", "org_name", "new_name", "start_date", "pub_date"),
        ("600000", "Example Bank", "Example Financial", "2020-01-01", "2020-01-02"),
    ),
}


def _fixture_text() -> str:
    sections = []
    for tag, (header, row) in _TAG_ROWS.items():
        buffer = tempfile.SpooledTemporaryFile(mode="w+", encoding="utf-8", newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(header)
        writer.writerow(row)
        buffer.seek(0)
        compressed = gzip.compress(buffer.read().encode("utf-8"), mtime=0)
        buffer.close()
        sections.append(f"#{tag}\n{base64.b64encode(compressed).decode('ascii')}")
    return "\n".join(sections) + "\n"


class DomainJqTests(unittest.TestCase):
    def test_jq_domain_parse_csv_manifest_and_sqlite_publish(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nooshare-jq-test-") as temporary:
            root = Path(temporary)
            source = root / "meta1"
            source.write_text(_fixture_text(), encoding="utf-8")

            result = run_jq_etl([source])
            self.assertEqual(len(result.tables), 10)
            self.assertEqual(result.table_row_counts["report_period"], 1)

            dataset_dir = root / "dataset"
            export_jq_csv_dataset(result, dataset_dir)
            restored = read_jq_csv_dataset(dataset_dir)
            self.assertEqual(restored, result)

            connection = connect_sqlite(":memory:")
            try:
                counts = replace_snapshot(connection, restored)
                repeated = replace_snapshot(connection, restored)
                self.assertEqual(counts, repeated)
                self.assertEqual(counts["stocks"], 1)
                self.assertEqual(counts["report_period"], 1)
                row = connection.execute(
                    'SELECT stock_code, year, quarter FROM "jq__report_period"'
                ).fetchone()
                self.assertEqual(tuple(row), ("600000", "2025", "Q1"))
            finally:
                connection.close()


class TongKeyTests(unittest.TestCase):
    def test_tong_key_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TONG_GZZX_KEY_HEX"):
                _load_hex_keys()

    def test_tong_key_must_be_valid_hex_and_exact_length(self) -> None:
        with patch.dict(os.environ, {"TONG_GZZX_KEY_HEX": "not-hex"}, clear=True):
            with self.assertRaisesRegex(ValueError, "valid hexadecimal"):
                _load_hex_keys()

        with patch.dict(
            os.environ,
            {"TONG_GZZX_KEY_HEX": "00" * 4175},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "4176 bytes"):
                _load_hex_keys()

    def test_tong_key_accepts_the_expected_decoded_length(self) -> None:
        with patch.dict(
            os.environ,
            {"TONG_GZZX_KEY_HEX": "00" * 4176},
            clear=True,
        ):
            self.assertEqual(len(_load_hex_keys()), 4176)


if __name__ == "__main__":
    unittest.main()
