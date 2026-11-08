from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from domains.securities import infer_exchange

from .models import TableBatch


SENTINEL_END_DATES = {"2200-01-01", "9999-12-31"}
REPORT_YEAR_MIN, REPORT_YEAR_MAX = 2000, 2030


@dataclass(frozen=True, slots=True)
class TagSpec:
    table: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]


# Derived output schemas are kept next to the transformation that creates them.
REPORT_PERIOD_SPEC = TagSpec(
    "report_period",
    ("stock_code", "year", "quarter", "name", "date", "keyword"),
    ("stock_code", "year", "quarter"),
)

TAG_SPECS = {
    "codes": TagSpec("stocks", ("stock_code", "display_name", "exchange", "start_date", "end_date"), ("stock_code",)),
    "indices": TagSpec("indices", ("index_code", "display_name", "start_date", "end_date"), ("index_code",)),
    "funds": TagSpec("funds", ("fund_code", "display_name", "type", "start_date", "end_date"), ("fund_code",)),
    "dates": TagSpec("trading_day", ("trade_date", "is_trading_day"), ("trade_date",)),
    "pub_dates": TagSpec("pub_date", ("stock_code", "period", "pub_date"), ("stock_code", "period")),
    "limit_ratio": TagSpec("limit_ratio", ("stock_code", "start_date", "end_date", "limit_ratio"), ("stock_code", "start_date")),
    "st_periods": TagSpec("st_period", ("stock_code", "start_date", "end_date"), ("stock_code", "start_date")),
    "valuation": TagSpec("valuation", ("stock_code", "start_date", "end_date", "capitalization", "circulating_cap"), ("stock_code", "start_date")),
    "name_history": TagSpec("name_history", ("stock_code", "org_name", "new_name", "start_date", "pub_date"), ("stock_code", "start_date", "new_name")),
}


def _strip_suffix(code: str) -> str:
    return code.split(".", 1)[0]


def _clean(tag: str, source_rows: Sequence[Mapping[str, str]]) -> tuple[dict[str, str], ...]:
    result = []
    for original in source_rows:
        row = dict(original)
        if tag == "codes":
            if row.get("Unnamed: 0") is not None and not row.get("code"):
                row["code"] = row["Unnamed: 0"]
            row.pop("Unnamed: 0", None)
            code = row.get("code", "")
            if code:
                if "." in code:
                    suffix = code.split(".", 1)[1]
                    exchange = {"XSHG": "sh", "XSHE": "sz"}.get(suffix)
                    if exchange is None:
                        raise ValueError(f"unknown exchange suffix in code: {code}")
                    row["exchange"] = exchange
                row["code"] = _strip_suffix(code)
        elif tag == "dates":
            row.pop("Unnamed: 0", None)
        else:
            row.pop("Unnamed: 0", None)
            if tag == "pub_dates":
                if row.get("code"):
                    row["code"] = _strip_suffix(row["code"])
            else:
                if row.get("code"):
                    row["code"] = _strip_suffix(row["code"])
                for key, value in tuple(row.items()):
                    if key.endswith("_code") and value:
                        try:
                            number = Decimal(value)
                        except InvalidOperation:
                            continue
                        if number.is_finite():
                            row[key] = str(int(number))
        result.append(row)
    return tuple(result)


def _end_date(value: str | None) -> str | None:
    return None if not value or value in SENTINEL_END_DATES else value


def _shape(tag: str, row: Mapping[str, str]) -> dict[str, Any]:
    if tag == "codes":
        code = row.get("code", "")
        return {
            "stock_code": code,
            "display_name": row.get("display_name", ""),
            "exchange": row.get("exchange") or infer_exchange(code),
            "start_date": row.get("start_date") or None,
            "end_date": _end_date(row.get("end_date")),
        }
    if tag == "indices":
        return {"index_code": row.get("code", ""), "display_name": row.get("display_name", ""), "start_date": row.get("start_date") or None, "end_date": _end_date(row.get("end_date"))}
    if tag == "funds":
        return {"fund_code": row.get("code", ""), "display_name": row.get("display_name", ""), "type": row.get("type", ""), "start_date": row.get("start_date") or None, "end_date": _end_date(row.get("end_date"))}
    if tag == "dates":
        return {"trade_date": row.get("time", ""), "is_trading_day": True}
    if tag == "pub_dates":
        return {"stock_code": row.get("code", ""), "period": row.get("period", ""), "pub_date": row.get("pubDate") or None}
    if tag == "limit_ratio":
        return {"stock_code": row.get("code", ""), "start_date": row.get("start_date", ""), "end_date": _end_date(row.get("end_date")), "limit_ratio": row.get("limit_ratio") or "0"}
    if tag == "st_periods":
        return {"stock_code": row.get("code", ""), "start_date": row.get("start_date", ""), "end_date": _end_date(row.get("end_date"))}
    if tag == "valuation":
        return {"stock_code": row.get("code", ""), "start_date": row.get("start_date", ""), "end_date": _end_date(row.get("end_date")), "capitalization": row.get("capitalization") or "0", "circulating_cap": row.get("circulating_cap") or "0"}
    if tag == "name_history":
        return {"stock_code": row.get("code", ""), "org_name": row.get("org_name", ""), "new_name": row.get("new_name", ""), "start_date": row.get("start_date") or None, "pub_date": row.get("pub_date") or None}
    raise ValueError(f"unsupported JQ tag: {tag}")


def shape_segment(tag: str, rows: Sequence[Mapping[str, str]], source: str) -> TableBatch:
    spec = TAG_SPECS[tag]
    shaped, dropped = [], 0
    for row in _clean(tag, rows):
        item = _shape(tag, row)
        if any(not item.get(key) for key in spec.primary_key):
            dropped += 1
        else:
            shaped.append(item)
    return TableBatch(tag, spec.table, spec.columns, spec.primary_key, tuple(shaped), (source,), len(rows), dropped)


def derive_report_period(pub_dates: TableBatch, stocks: TableBatch) -> TableBatch:
    names = {str(row["stock_code"]): str(row["display_name"] or "") for row in stocks.rows}
    pattern = re.compile(r"^([0-9]{4})[qQ]([1-4])$")
    keywords = {"Q1": "一季报", "Q2": "半年度报告", "Q3": "三季报", "Q4": "年度报告"}
    rows = []
    for item in pub_dates.rows:
        match = pattern.fullmatch(str(item["period"]))
        if match is None or not REPORT_YEAR_MIN <= int(match.group(1)) <= REPORT_YEAR_MAX:
            continue
        stock_code = str(item["stock_code"])
        if stock_code not in names:
            continue
        quarter = "Q" + match.group(2)
        rows.append({
            "stock_code": stock_code,
            "year": match.group(1),
            "quarter": quarter,
            "name": names[stock_code],
            "date": item["pub_date"],
            "keyword": keywords[quarter],
        })
    return TableBatch(
        REPORT_PERIOD_SPEC.table,
        REPORT_PERIOD_SPEC.table,
        REPORT_PERIOD_SPEC.columns,
        REPORT_PERIOD_SPEC.primary_key,
        tuple(rows),
        ("derived:pub_dates+stocks",),
        len(pub_dates.rows),
        len(pub_dates.rows) - len(rows),
    )
