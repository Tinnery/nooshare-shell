"""
Tong 完整流程入口。

串联 extract → adjust → PostgreSQL 写入。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from .adjust import (
    calc_hfq_factor,
    calc_ffq_factor,
    apply_hfq,
    apply_ffq,
    write_adjusted_csv,
)
from .adapters.postgres import postgres_config, run_tong_to_pg
from .extract import extract_tong_data
from .gbbq import build_gbbq_index, decrypt_gbbq

logger = logging.getLogger(__name__)

def run_tong_extract(
    work_dir: Path,
    output_dir: Path,
    hsjday_zip: Optional[Path] = None,
    gbbq_zip: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    max_workers: int = 8,
    write_tsv: bool = False,
) -> Dict:
    """
    运行 Tong 数据提取流程。

    从 RustFS 下载 hsjday.zip 和 gbbq.zip，解压后解析 .day 文件和 gbbq 文件，
    输出 CSV/TSV 文件。write_tsv=False 时只写 CSV（默认，下游 adjust/pg 仅用 CSV）。
    """
    return extract_tong_data(
        work_dir=work_dir,
        output_dir=output_dir,
        hsjday_zip=hsjday_zip,
        gbbq_zip=gbbq_zip,
        data_dir=data_dir,
        max_workers=max_workers,
        write_tsv=write_tsv,
    )


def run_tong_adjust(
    daily_dir: Path,
    gbbq_file: Path,
    output_dir: Path,
    stock_symbols: Optional[List[str]] = None,
) -> Dict:
    """
    运行复权计算流程。

    读取 daily CSV 和 gbbq 文件，计算后复权和前复权价格，输出 CSV。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 待复权的 symbol 列表：从 daily 目录的 CSV 文件名提取（覆盖所有有日线数据的 symbol）
    if stock_symbols is None:
        stock_symbols = [f.stem for f in sorted(daily_dir.glob("*.csv"))]
    logger.info(f"Loaded {len(stock_symbols)} symbols to adjust")

    # 2. 解密 gbbq
    gbbq_records = decrypt_gbbq(str(gbbq_file))
    logger.info(f"Decrypted {len(gbbq_records)} gbbq records")

    # 3. 构建 gbbq index
    gbbq_index = build_gbbq_index(gbbq_records)

    # 4. 计算复权
    hfq_dir = output_dir / "daily_hfq"
    qfq_dir = output_dir / "daily_qfq"
    hfq_dir.mkdir(parents=True, exist_ok=True)
    qfq_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    for symbol in stock_symbols:
        daily_path = daily_dir / f"{symbol}.csv"
        if not daily_path.exists():
            continue

        # 读取 daily 数据
        from .adjust import load_day_records_from_csv
        day_records = load_day_records_from_csv(str(daily_path))
        if not day_records:
            continue

        # 获取 gbbq 记录
        xdxr_infos = gbbq_index.get(symbol, [])

        # 计算后复权
        hfq_result = calc_hfq_factor(day_records, xdxr_infos)
        hfq_records = apply_hfq(day_records, hfq_result)
        write_adjusted_csv(str(hfq_dir / f"{symbol}.csv"), hfq_records, is_hfq=True)

        # 计算前复权
        ffq_result = calc_ffq_factor(day_records, xdxr_infos)
        ffq_records = apply_ffq(day_records, ffq_result)
        write_adjusted_csv(str(qfq_dir / f"{symbol}.csv"), ffq_records, is_hfq=False)

        success_count += 1

    logger.info(f"Adjusted {success_count} symbols")

    return {
        "hfq_dir": str(hfq_dir),
        "qfq_dir": str(qfq_dir),
        "total_symbols": success_count,
    }


def run_full_pipeline(
    work_dir: Path,
    output_dir: Path,
    hsjday_zip: Optional[Path] = None,
    gbbq_zip: Optional[Path] = None,
    max_workers: int = 8,
    write_to_pg: bool = False,
    pg_config: Optional[Dict] = None,
    replace_snapshot: bool = False,
) -> Dict:
    """
    运行完整 Tong 数据管道：extract → adjust → (可选) PostgreSQL。
    """
    work_dir = Path(work_dir)
    output_dir = Path(output_dir)

    # Step 1: Extract
    logger.info("=" * 60)
    logger.info("Step 1: Extract Tong data")
    logger.info("=" * 60)
    extract_result = run_tong_extract(
        work_dir=work_dir,
        output_dir=output_dir,
        hsjday_zip=hsjday_zip,
        gbbq_zip=gbbq_zip,
        max_workers=max_workers,
    )

    # Step 2: Adjust
    logger.info("=" * 60)
    logger.info("Step 2: Calculate adjusted prices")
    logger.info("=" * 60)
    daily_dir = output_dir / "daily"

    # 查找 gbbq 文件
    gbbq_file = None
    for candidate in [
        work_dir / "gbbq" / "gbbq",
        Path("/tmp/tong_fixtures/gbbq_extracted/gbbq"),
    ]:
        if candidate.exists():
            gbbq_file = candidate
            break
    if gbbq_file is None:
        raise FileNotFoundError("Cannot find gbbq file")

    adjust_result = run_tong_adjust(
        daily_dir=daily_dir,
        gbbq_file=gbbq_file,
        output_dir=output_dir,
    )

    result = {
        "extract": extract_result,
        "adjust": adjust_result,
    }

    # Step 3: PostgreSQL (可选)
    if write_to_pg:
        logger.info("=" * 60)
        logger.info("Step 3: Write to PostgreSQL")
        logger.info("=" * 60)
        pg_result = run_tong_to_pg(
            output_dir,
            pg_config,
            replace_snapshot=replace_snapshot,
        )
        result["postgresql"] = pg_result

    logger.info("=" * 60)
    logger.info("Pipeline complete!")
    logger.info(f"  Symbols: {extract_result.get('total_symbols', 0)}")
    logger.info(f"  Gbbq records: {extract_result.get('total_gbbq_records', 0)}")
    logger.info(f"  Adjusted symbols: {adjust_result.get('total_symbols', 0)}")
    if write_to_pg:
        pg = result.get("postgresql", {})
        logger.info(f"  PG daily: {pg.get('daily', 0):,} rows")
        logger.info(f"  PG daily_hfq: {pg.get('daily_hfq', 0):,} rows")
        logger.info(f"  PG daily_qfq: {pg.get('daily_qfq', 0):,} rows")
        logger.info(f"  PG gbbq: {pg.get('gbbq', 0):,} rows")
        logger.info(f"  PG symbol_class: {pg.get('symbol_class', 0):,} rows")

    return result
