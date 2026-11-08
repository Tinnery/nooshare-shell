"""PostgreSQL persistence adapter for Tong snapshots.

Owns the target schema, table layout, bulk loading and atomic snapshot replace.
The Tong Flow supplies a validated local dataset and explicitly opts into a
complete snapshot replacement.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def postgres_config() -> Dict:
    """Read an explicit PostgreSQL target from NooShare or libpq variables."""
    database = os.getenv("NOOSHARE_PG_DATABASE") or os.getenv("PGDATABASE")
    if not database:
        raise RuntimeError(
            "Set NOOSHARE_PG_DATABASE or PGDATABASE explicitly. "
            "No default database is selected for Tong writes."
        )

    config = {
        "dbname": database,
        "user": os.getenv("NOOSHARE_PG_USER", os.getenv("PGUSER", "travis")),
        "connect_timeout": int(os.getenv("NOOSHARE_PG_CONNECT_TIMEOUT", "10")),
        "application_name": "nooshare-tong-flow",
    }
    values = {
        "host": ("NOOSHARE_PG_HOST", "PGHOST"),
        "port": ("NOOSHARE_PG_PORT", "PGPORT"),
        "password": ("NOOSHARE_PG_PASSWORD", "PGPASSWORD"),
        "sslmode": ("NOOSHARE_PG_SSLMODE", "PGSSLMODE"),
    }
    for key, names in values.items():
        value = next((os.getenv(name) for name in names if os.getenv(name)), None)
        if value is not None:
            config[key] = int(value) if key == "port" else value
    return config

def create_pg_tables(conn) -> None:
    """创建 tong schema 下的所有表（如果不存在）。"""
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS tong")

        tables = {
            # 日线行情
            "daily": """
                CREATE TABLE IF NOT EXISTS tong.daily (
                    symbol TEXT NOT NULL,
                    date INTEGER NOT NULL,
                    open DOUBLE PRECISION NOT NULL,
                    high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL,
                    close DOUBLE PRECISION NOT NULL,
                    volume BIGINT NOT NULL,
                    amount DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (symbol, date)
                )
            """,
            # 后复权日线
            "daily_hfq": """
                CREATE TABLE IF NOT EXISTS tong.daily_hfq (
                    symbol TEXT NOT NULL,
                    date INTEGER NOT NULL,
                    open DOUBLE PRECISION NOT NULL,
                    high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL,
                    close DOUBLE PRECISION NOT NULL,
                    volume BIGINT NOT NULL,
                    amount DOUBLE PRECISION NOT NULL,
                    preclose DOUBLE PRECISION,
                    hfq_factor DOUBLE PRECISION,
                    PRIMARY KEY (symbol, date)
                )
            """,
            # 前复权日线
            "daily_qfq": """
                CREATE TABLE IF NOT EXISTS tong.daily_qfq (
                    symbol TEXT NOT NULL,
                    date INTEGER NOT NULL,
                    open DOUBLE PRECISION NOT NULL,
                    high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL,
                    close DOUBLE PRECISION NOT NULL,
                    volume BIGINT NOT NULL,
                    amount DOUBLE PRECISION NOT NULL,
                    preclose DOUBLE PRECISION,
                    ffq_factor DOUBLE PRECISION,
                    PRIMARY KEY (symbol, date)
                )
            """,
            # 除权除息
            # 注意：源 gbbq 数据中个别 (symbol, date, category) 存在两条 *不同* 记录
            # （例如 sz920222/20220610/cat5 一条为真实值、一条 c1/c2 为 0），Go 的 CSV
            # 会原样保留两条。为忠实保留全部记录，这里使用代理主键 id，
            # 不强制 (symbol, date, category) 唯一。
            "gbbq": """
                CREATE TABLE IF NOT EXISTS tong.gbbq (
                    id SERIAL PRIMARY KEY,
                    category INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    date INTEGER NOT NULL,
                    c1 DOUBLE PRECISION NOT NULL,
                    c2 DOUBLE PRECISION NOT NULL,
                    c3 DOUBLE PRECISION NOT NULL,
                    c4 DOUBLE PRECISION NOT NULL
                )
            """,
            # symbol 分类
            "symbol_class": """
                CREATE TABLE IF NOT EXISTS tong.symbol_class (
                    symbol TEXT NOT NULL,
                    class TEXT NOT NULL,
                    PRIMARY KEY (symbol)
                )
            """,
        }

        for table_name, ddl in tables.items():
            cur.execute(ddl)
            logger.info(f"  Table tong.{table_name} ready")



def load_pg_daily(conn, daily_dir: Path) -> int:
    """加载 daily 数据到 PostgreSQL。

    对 (symbol, date) 去重，规避极少数源 .day 文件内同一交易日重复出现两条
    完全相同记录的情况（如 sh000833），否则违反 PRIMARY KEY 导致整批 COPY 失败。
    重复行内容一致，去重无损。
    """
    import csv

    total = 0
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE tong.daily")

        for csv_file in sorted(daily_dir.glob("*.csv")):
            symbol = csv_file.stem
            rows = []
            seen = set()
            with open(csv_file, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = (symbol, int(row["date"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append((
                        symbol,
                        key[1],
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        int(float(row["volume"])),
                        float(row["amount"]),
                    ))

            if rows:
                # 使用 COPY 批量插入
                copy_sql = f"COPY tong.daily (symbol, date, open, high, low, close, volume, amount) FROM STDIN WITH (FORMAT csv)"
                csv_buffer = io.StringIO()
                for r in rows:
                    csv_buffer.write(f"{r[0]},{r[1]},{r[2]:.2f},{r[3]:.2f},{r[4]:.2f},{r[5]:.2f},{r[6]},{r[7]:.2f}\n")
                csv_buffer.seek(0)
                cur.copy_expert(copy_sql, csv_buffer)
                total += len(rows)

    return total


def load_pg_adjusted(conn, adjusted_dir: Path, table_name: str, factor_col: str) -> int:
    """加载复权数据到 PostgreSQL。

    同样按 (symbol, date) 去重，规避源数据重复交易日记录（见 load_pg_daily）。
    """
    import csv

    total = 0
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE tong.{table_name}")

        for csv_file in sorted(adjusted_dir.glob("*.csv")):
            symbol = csv_file.stem
            rows = []
            seen = set()
            with open(csv_file, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = (symbol, int(row["date"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    preclose = row.get("preclose", "")
                    factor = row.get(factor_col, "")
                    rows.append((
                        symbol,
                        key[1],
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        int(float(row["volume"])),
                        float(row["amount"]),
                        float(preclose) if preclose else None,
                        float(factor) if factor else None,
                    ))

            if rows:
                cols = f"symbol, date, open, high, low, close, volume, amount, preclose, {factor_col}"
                copy_sql = f"COPY tong.{table_name} ({cols}) FROM STDIN WITH (FORMAT csv)"
                csv_buffer = io.StringIO()
                for r in rows:
                    preclose_str = f"{r[8]:.2f}" if r[8] is not None else ""
                    factor_str = f"{r[9]:.4f}" if r[9] is not None else ""
                    csv_buffer.write(f"{r[0]},{r[1]},{r[2]:.2f},{r[3]:.2f},{r[4]:.2f},{r[5]:.2f},{r[6]},{r[7]:.2f},{preclose_str},{factor_str}\n")
                csv_buffer.seek(0)
                cur.copy_expert(copy_sql, csv_buffer)
                total += len(rows)

    return total


def load_pg_gbbq(conn, gbbq_csv: Path) -> int:
    """加载 gbbq 数据到 PostgreSQL。"""
    import csv

    total = 0
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE tong.gbbq")

        rows = []
        with open(gbbq_csv, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append((
                    int(row["category"]),
                    row["symbol"],
                    row["stock_code"],
                    int(row["date"]),
                    float(row["c1"]),
                    float(row["c2"]),
                    float(row["c3"]),
                    float(row["c4"]),
                ))

        if rows:
            copy_sql = "COPY tong.gbbq (category, symbol, stock_code, date, c1, c2, c3, c4) FROM STDIN WITH (FORMAT csv)"
            csv_buffer = io.StringIO()
            for r in rows:
                csv_buffer.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]:.4f},{r[5]:.4f},{r[6]:.4f},{r[7]:.4f}\n")
            csv_buffer.seek(0)
            cur.copy_expert(copy_sql, csv_buffer)
            total = len(rows)

    return total


def load_pg_symbol_class(conn, symbol_class_csv: Path) -> int:
    """加载 symbol_class 数据到 PostgreSQL。"""
    import csv

    total = 0
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE tong.symbol_class")

        rows = []
        with open(symbol_class_csv, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append((row["symbol"], row["class"]))

        if rows:
            copy_sql = "COPY tong.symbol_class (symbol, class) FROM STDIN WITH (FORMAT csv)"
            csv_buffer = io.StringIO()
            for r in rows:
                csv_buffer.write(f"{r[0]},{r[1]}\n")
            csv_buffer.seek(0)
            cur.copy_expert(copy_sql, csv_buffer)
            total = len(rows)

    return total


def _validate_tong_output(output_dir: Path) -> None:
    """Reject incomplete local datasets before opening or modifying a database."""
    required = {
        "daily": output_dir / "daily",
        "daily_hfq": output_dir / "daily_hfq",
        "daily_qfq": output_dir / "daily_qfq",
        "gbbq.csv": output_dir / "gbbq.csv",
        "symbol_class.csv": output_dir / "symbol_class.csv",
    }
    directory_names = {"daily", "daily_hfq", "daily_qfq"}
    missing = []
    for name, path in required.items():
        exists = path.is_dir() if name in directory_names else path.is_file()
        if not exists:
            missing.append(name)
    empty = [
        name for name in ("daily", "daily_hfq", "daily_qfq")
        if not list(required[name].glob("*.csv"))
    ]
    if missing or empty:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if empty:
            details.append(f"no CSV files={empty}")
        raise ValueError("incomplete Tong output directory: " + ", ".join(details))


def run_tong_to_pg(
    output_dir: Path,
    pg_config: Optional[Dict] = None,
    *,
    replace_snapshot: bool = False,
) -> Dict:
    """Atomically replace all Tong tables after an explicit full-snapshot opt-in.

    This publisher truncates the complete Tong tables. Callers must explicitly
    pass ``replace_snapshot=True`` and provide a PostgreSQL database name.
    All table replacements commit or roll back together.
    """
    if not replace_snapshot:
        raise ValueError(
            "Tong publication replaces every row in the tong schema. "
            "Pass replace_snapshot=True only for an authorized full snapshot update."
        )

    output_dir = Path(output_dir)
    _validate_tong_output(output_dir)

    import psycopg2

    target = postgres_config() if pg_config is None else dict(pg_config)
    if not target.get("dbname"):
        raise ValueError("pg_config must include an explicit dbname")

    daily_dir = output_dir / "daily"
    hfq_dir = output_dir / "daily_hfq"
    qfq_dir = output_dir / "daily_qfq"
    gbbq_csv = output_dir / "gbbq.csv"
    symbol_class_csv = output_dir / "symbol_class.csv"

    conn = psycopg2.connect(**target)
    try:
        # Psycopg's connection context covers all DDL, truncates and COPY operations.
        with conn:
            logger.info("Creating tables...")
            create_pg_tables(conn)

            logger.info("Loading daily data...")
            daily_count = load_pg_daily(conn, daily_dir)
            logger.info("  daily: %s rows", f"{daily_count:,}")

            logger.info("Loading daily_hfq data...")
            hfq_count = load_pg_adjusted(conn, hfq_dir, "daily_hfq", "hfq_factor")
            logger.info("  daily_hfq: %s rows", f"{hfq_count:,}")

            logger.info("Loading daily_qfq data...")
            qfq_count = load_pg_adjusted(conn, qfq_dir, "daily_qfq", "ffq_factor")
            logger.info("  daily_qfq: %s rows", f"{qfq_count:,}")

            logger.info("Loading gbbq data...")
            gbbq_count = load_pg_gbbq(conn, gbbq_csv)
            logger.info("  gbbq: %s rows", f"{gbbq_count:,}")

            logger.info("Loading symbol_class data...")
            sc_count = load_pg_symbol_class(conn, symbol_class_csv)
            logger.info("  symbol_class: %s rows", f"{sc_count:,}")

        return {
            "daily": daily_count,
            "daily_hfq": hfq_count,
            "daily_qfq": qfq_count,
            "gbbq": gbbq_count,
            "symbol_class": sc_count,
        }
    finally:
        conn.close()
