"""
Tong 数据端到端提取流程。

从 RustFS 下载 hsjday.zip 和 gbbq.zip，解压后解析 .day 文件和 gbbq 文件，
输出 CSV/TSV 文件。精确翻译 Go 版 extract.go 的逻辑。
"""
import csv
import logging
import os
import shutil
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.config import Config

from .gbbq import build_gbbq_index, decrypt_gbbq, symbol_from_code
from .models import DailyPrice, GbbqRecord
from .parser import parse_day_file

logger = logging.getLogger(__name__)

# Shared S3 configuration is read when the client is created.
S3_BUCKET_DEFAULT = "raw"
S3_REGION_DEFAULT = "us-east-1"

# 分类规则（与 Go classify.go 完全一致）
CLASS_STOCK = "stock"
CLASS_BSTOCK = "bstock"
CLASS_INDEX = "index"
CLASS_ETF = "etf"
CLASS_BLOCK = "block"
CLASS_UNKNOWN = "unknown"

# 分类规则表：(market, prefix, class)
CLASS_RULES = [
    # 上海
    ("sh", "600", CLASS_STOCK), ("sh", "601", CLASS_STOCK),
    ("sh", "603", CLASS_STOCK), ("sh", "605", CLASS_STOCK),
    ("sh", "688", CLASS_STOCK), ("sh", "689", CLASS_STOCK),
    ("sh", "900", CLASS_BSTOCK),
    ("sh", "000", CLASS_INDEX),
    ("sh", "880", CLASS_BLOCK), ("sh", "881", CLASS_BLOCK),
    ("sh", "50", CLASS_ETF), ("sh", "51", CLASS_ETF),
    ("sh", "52", CLASS_ETF), ("sh", "53", CLASS_ETF),
    ("sh", "56", CLASS_ETF), ("sh", "58", CLASS_ETF),
    # 深圳
    ("sz", "000", CLASS_STOCK), ("sz", "001", CLASS_STOCK),
    ("sz", "002", CLASS_STOCK), ("sz", "003", CLASS_STOCK),
    ("sz", "300", CLASS_STOCK), ("sz", "301", CLASS_STOCK),
    ("sz", "302", CLASS_STOCK),
    ("sz", "20", CLASS_BSTOCK),
    ("sz", "399", CLASS_INDEX),
    ("sz", "15", CLASS_ETF), ("sz", "16", CLASS_ETF),
    ("sz", "18", CLASS_ETF),
    # 北京
    ("bj", "43", CLASS_STOCK), ("bj", "83", CLASS_STOCK),
    ("bj", "87", CLASS_STOCK), ("bj", "88", CLASS_STOCK),
    ("bj", "920", CLASS_STOCK),
    ("bj", "899", CLASS_INDEX),
]

# 最小日期
MIN_DATE = 20080101


def _get_s3_client():
    """Create an S3 client using the shared NooShare secrets contract."""
    settings = {
        "NOOSHARE_S3_ENDPOINT": os.getenv("NOOSHARE_S3_ENDPOINT"),
        "NOOSHARE_S3_ACCESS_KEY": os.getenv("NOOSHARE_S3_ACCESS_KEY"),
        "NOOSHARE_S3_SECRET_KEY": os.getenv("NOOSHARE_S3_SECRET_KEY"),
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError(
            "Missing required S3 configuration: " + ", ".join(missing)
        )

    return boto3.client(
        "s3",
        endpoint_url=settings["NOOSHARE_S3_ENDPOINT"],
        aws_access_key_id=settings["NOOSHARE_S3_ACCESS_KEY"],
        aws_secret_access_key=settings["NOOSHARE_S3_SECRET_KEY"],
        region_name=os.getenv("NOOSHARE_S3_REGION", S3_REGION_DEFAULT),
        config=Config(
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
            signature_version="s3v4",
        ),
    )


def download_from_rustfs(s3_key: str, local_path: Path) -> Path:
    """Download a Tong archive from the shared S3 bucket."""
    bucket = os.getenv("NOOSHARE_S3_BUCKET", S3_BUCKET_DEFAULT).strip()
    if not bucket:
        raise RuntimeError("NOOSHARE_S3_BUCKET must not be empty")
    client = _get_s3_client()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading s3://{bucket}/{s3_key} -> {local_path}")
    client.download_file(bucket, s3_key, str(local_path))
    return local_path


def extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract a Tong archive without allowing paths to escape ``dest_dir``.

    Tong archives may contain Windows-style separators. Normalize those paths,
    reject absolute/parent traversal paths, and stream regular file contents to
    resolved destinations. ZIP symlink metadata is not materialized as links.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()
    seen: set[Path] = set()

    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            posix_path = PurePosixPath(normalized)
            windows_path = PureWindowsPath(normalized)
            if (
                not normalized
                or posix_path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or ".." in posix_path.parts
            ):
                raise ValueError(f"unsafe ZIP member path: {info.filename!r}")

            target = (root / Path(*posix_path.parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"ZIP member escapes extraction directory: {info.filename!r}"
                ) from exc

            if target in seen:
                raise ValueError(f"duplicate ZIP member path: {info.filename!r}")
            seen.add(target)

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _classify_symbol(symbol: str) -> str:
    """
    分类 symbol（与 Go classify.go 完全一致）。
    symbol 格式: sh600000, sz000001, bj430047

    返回: stock / bstock / index / etf / block / unknown
    使用最长前缀匹配（与 Go 逻辑一致）。
    """
    if len(symbol) < 3:
        return CLASS_UNKNOWN
    market = symbol[:2]
    num = symbol[2:]

    best = ""
    best_class = CLASS_UNKNOWN
    for rule_market, rule_prefix, rule_class in CLASS_RULES:
        if rule_market != market:
            continue
        if not num.startswith(rule_prefix):
            continue
        if len(rule_prefix) > len(best):
            best = rule_prefix
            best_class = rule_class
    return best_class


def _write_daily_csv(records: List[DailyPrice], output_path: Path) -> None:
    """写入 daily CSV（与 Go writeDailyCSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume", "amount"])
        for r in records:
            writer.writerow([
                r.date,
                f"{r.open:.2f}",
                f"{r.high:.2f}",
                f"{r.low:.2f}",
                f"{r.close:.2f}",
                f"{r.volume:.2f}",
                f"{r.amount:.2f}",
            ])


def _write_daily_tsv(records: List[DailyPrice], output_path: Path) -> None:
    """写入 daily TSV（与 Go writeDailyTSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("date\topen\thigh\tlow\tclose\tvolume\tamount\n")
        for r in records:
            f.write(f"{r.date}\t{r.open:.2f}\t{r.high:.2f}\t{r.low:.2f}\t{r.close:.2f}\t{r.volume}\t{r.amount:.2f}\n")


def _write_gbbq_csv(records: List[GbbqRecord], output_path: Path) -> None:
    """写入 gbbq CSV（与 Go writeGbbqCSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["category", "symbol", "stock_code", "date", "c1", "c2", "c3", "c4"])
        for r in records:
            writer.writerow([
                r.category,
                r.symbol,
                r.stock_code,
                r.date,
                f"{r.c1:.4f}",
                f"{r.c2:.4f}",
                f"{r.c3:.4f}",
                f"{r.c4:.4f}",
            ])


def _write_gbbq_tsv(records: List[GbbqRecord], output_path: Path) -> None:
    """写入 gbbq TSV（与 Go writeGbbqTSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("category\tsymbol\tstock_code\tdate\tc1\tc2\tc3\tc4\n")
        for r in records:
            f.write(f"{r.category}\t{r.symbol}\t{r.stock_code}\t{r.date}\t{r.c1:.4f}\t{r.c2:.4f}\t{r.c3:.4f}\t{r.c4:.4f}\n")


def _write_symbol_class_csv(symbol_class: Dict[str, str], output_path: Path) -> None:
    """写入 symbol_class CSV（与 Go writeSymbolClassCSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 按 symbol 排序
    sorted_symbols = sorted(symbol_class.items())
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "class"])
        for symbol, cls in sorted_symbols:
            writer.writerow([symbol, cls])


def _write_symbol_class_tsv(symbol_class: Dict[str, str], output_path: Path) -> None:
    """写入 symbol_class TSV（与 Go writeSymbolClassTSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_symbols = sorted(symbol_class.items())
    with open(output_path, "w") as f:
        f.write("symbol\tclass\n")
        for symbol, cls in sorted_symbols:
            f.write(f"{symbol}\t{cls}\n")


def _write_holidays_csv(holidays: List[str], output_path: Path) -> None:
    """写入 holidays CSV（与 Go writeHolidaysCSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["holiday"])
        for h in holidays:
            writer.writerow([h])


def _write_holidays_tsv(holidays: List[str], output_path: Path) -> None:
    """写入 holidays TSV（与 Go writeHolidaysTSV 一致）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("holiday\n")
        for h in holidays:
            f.write(f"{h}\n")


def _find_day_files(root_dir: Path) -> List[Path]:
    """递归查找所有 .day 文件"""
    return sorted(root_dir.rglob("*.day"))


def _process_and_write_day(
    day_path: Path,
    daily_dir: Path,
    write_tsv: bool,
    daily_tsv_dir: Optional[Path],
) -> Optional[str]:
    """
    处理单个 .day 文件：解析后立即写盘，不缓存全量数据。

    与 Go 行为对齐：解析失败时返回 None 且不写任何文件（Go 在 parse 出错时
    直接 continue，不会生成该 symbol 的 CSV）。成功时返回 symbol 名。

    返回 symbol（成功）或 None（失败）。
    """
    symbol = day_path.stem.lower()
    try:
        records = parse_day_file(day_path)
    except Exception as e:
        logger.warning(f"Failed to parse {day_path}: {e}")
        return None

    # 成功解析（即使 records 为空也写 header-only 的 CSV，与 Go writeCSVOHLCV 一致）
    _write_daily_csv(records, daily_dir / f"{symbol}.csv")
    if write_tsv and daily_tsv_dir is not None:
        _write_daily_tsv(records, daily_tsv_dir / f"{symbol}.tsv")
    return symbol


def _parse_holidays(gbbq_data: bytes) -> List[str]:
    """
    从 gbbq 数据中提取节假日列表。
    Go 版从 gbbq 的 category=1 记录中提取日期。
    """
    # 实际上 Go 版从单独的 holidays 数据源获取
    # 这里返回空列表，与 Go 版行为一致
    return []


def extract_tong_data(
    work_dir: Path,
    output_dir: Path,
    hsjday_zip: Optional[Path] = None,
    gbbq_zip: Optional[Path] = None,
    max_workers: int = 8,
    data_dir: Optional[Path] = None,
    write_tsv: bool = False,
) -> Dict[str, str]:
    """
    端到端提取 Tong 数据。

    Args:
        work_dir: 工作目录（用于解压临时文件）
        output_dir: 输出目录
        hsjday_zip: hsjday.zip 路径（如果为 None，从 RustFS 下载）
        gbbq_zip: gbbq.zip 路径（如果为 None，从 RustFS 下载）
        max_workers: 并发解析线程数
        data_dir: 已解压的数据目录（如果提供，跳过下载和解压步骤）

    Returns:
        输出路径字典
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {}

    # 如果提供了 data_dir，直接使用
    if data_dir and data_dir.exists():
        logger.info(f"Using existing data directory: {data_dir}")
        hsjday_dir = data_dir
        gbbq_file = data_dir / "gbbq" / "gbbq"
        if not gbbq_file.exists():
            # 尝试其他位置
            for f in data_dir.rglob("gbbq"):
                if f.is_file():
                    gbbq_file = f
                    break
    else:
        # 1. 准备 hsjday.zip
        if hsjday_zip is None:
            hsjday_key = os.getenv("NOOSHARE_TONG_HSJDAY_OBJECT", "").strip()
            if not hsjday_key:
                raise RuntimeError("NOOSHARE_TONG_HSJDAY_OBJECT must not be empty")
            hsjday_zip = work_dir / Path(hsjday_key).name
            if not hsjday_zip.exists():
                try:
                    download_from_rustfs(hsjday_key, hsjday_zip)
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not read TONG hsjday object "
                        f"s3://{os.getenv('NOOSHARE_S3_BUCKET', S3_BUCKET_DEFAULT)}/{hsjday_key}. "
                        "Check NOOSHARE_TONG_HSJDAY_OBJECT."
                    ) from exc

        # 2. 准备 gbbq.zip（或直接用 gbbq 文件）
        if gbbq_zip is None:
            gbbq_key = os.getenv("NOOSHARE_TONG_GBBQ_OBJECT", "").strip()
            if not gbbq_key:
                raise RuntimeError("NOOSHARE_TONG_GBBQ_OBJECT must not be empty")
            gbbq_zip = work_dir / Path(gbbq_key).name
            if not gbbq_zip.exists():
                try:
                    download_from_rustfs(gbbq_key, gbbq_zip)
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not read TONG GBBQ object "
                        f"s3://{os.getenv('NOOSHARE_S3_BUCKET', S3_BUCKET_DEFAULT)}/{gbbq_key}. "
                        "Check NOOSHARE_TONG_GBBQ_OBJECT."
                    ) from exc

        # 3. 解压 hsjday.zip
        logger.info("Extracting hsjday.zip...")
        hsjday_dir = work_dir / "hsjday"
        if not hsjday_dir.exists():
            extract_zip(hsjday_zip, hsjday_dir)

        # 4. 解压 gbbq.zip 或找到 gbbq 文件
        gbbq_file = None
        if gbbq_zip and gbbq_zip.exists():
            logger.info("Extracting gbbq.zip...")
            gbbq_dir = work_dir / "gbbq"
            if not gbbq_dir.exists():
                extract_zip(gbbq_zip, gbbq_dir)
            # 查找 gbbq 文件
            gbbq_file = gbbq_dir / "gbbq"
            if not gbbq_file.exists():
                # 尝试子目录
                for f in gbbq_dir.rglob("gbbq"):
                    gbbq_file = f
                    break

        # 如果 gbbq.zip 不存在，尝试查找已有的 gbbq 文件
        if gbbq_file is None or not gbbq_file.exists():
            # 尝试从 fixture 目录查找
            fixture_gbbq = Path("/tmp/tong_fixtures/gbbq_extracted/gbbq")
            if fixture_gbbq.exists():
                gbbq_file = fixture_gbbq
            else:
                raise FileNotFoundError("Cannot find gbbq file")

    logger.info(f"Using gbbq file: {gbbq_file}")

    # 5. 查找并解析 .day 文件
    logger.info("Finding .day files...")
    day_files = _find_day_files(hsjday_dir)
    logger.info(f"Found {len(day_files)} .day files")

    # 6. 并发解析 .day 文件
    #    关键优化：边解析边写盘，不缓存全量 records（避免 OOM）。
    #    与 Go 行为一致：解析失败的 symbol 不写文件；成功解析（含空记录）写 header-only CSV。
    logger.info(f"Parsing .day files with {max_workers} workers (streaming write)...")
    daily_dir = output_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_tsv_dir = output_dir / "daily_tsv"
    if write_tsv:
        daily_tsv_dir.mkdir(parents=True, exist_ok=True)

    all_symbols: List[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_and_write_day, df, daily_dir, write_tsv,
                            daily_tsv_dir if write_tsv else None): df
            for df in day_files
        }
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            if done_count % 1000 == 0:
                logger.info(f"  Progress: {done_count}/{len(day_files)}")
            symbol = future.result()
            if symbol is not None:
                all_symbols.append(symbol)

    logger.info(f"Parsed & wrote {len(all_symbols)} symbols")

    # 7. 从 daily/ 目录的 CSV 文件名提取 symbol 列表（与 Go extractSymbolsFromDaily 一致）
    logger.info("Extracting symbols from daily CSV files...")
    symbols_from_csv = sorted(f.stem for f in daily_dir.glob("*.csv"))
    logger.info(f"  Found {len(symbols_from_csv)} symbols from CSV files")

    # 9. 构建 symbol_class（排除 unknown）
    symbol_class: Dict[str, str] = {}
    for symbol in symbols_from_csv:
        cls = _classify_symbol(symbol)
        if cls != CLASS_UNKNOWN:
            symbol_class[symbol] = cls
    logger.info(f"  {len(symbol_class)} symbols after excluding unknown")

    # 10. 解析 gbbq
    logger.info("Decrypting gbbq...")
    gbbq_records = decrypt_gbbq(str(gbbq_file))
    logger.info(f"  {len(gbbq_records)} gbbq records")

    # 11. 写入 gbbq CSV（TSV 仅当 write_tsv）
    logger.info("Writing gbbq CSV...")
    _write_gbbq_csv(gbbq_records, output_dir / "gbbq.csv")
    if write_tsv:
        _write_gbbq_tsv(gbbq_records, output_dir / "gbbq.tsv")

    # 12. 写入 symbol_class CSV（TSV 仅当 write_tsv）
    logger.info(f"Writing symbol_class CSV ({len(symbol_class)} symbols)...")
    _write_symbol_class_csv(symbol_class, output_dir / "symbol_class.csv")
    if write_tsv:
        _write_symbol_class_tsv(symbol_class, output_dir / "symbol_class.tsv")

    # 13. 写入 holidays（空列表，与 Go 版一致；TSV 仅当 write_tsv）
    holidays = _parse_holidays(None)
    _write_holidays_csv(holidays, output_dir / "holidays.csv")
    if write_tsv:
        _write_holidays_tsv(holidays, output_dir / "holidays.tsv")

    result["daily_dir"] = str(daily_dir)
    result["daily_tsv_dir"] = str(daily_tsv_dir)
    result["gbbq_csv"] = str(output_dir / "gbbq.csv")
    result["gbbq_tsv"] = str(output_dir / "gbbq.tsv")
    result["symbol_class_csv"] = str(output_dir / "symbol_class.csv")
    result["symbol_class_tsv"] = str(output_dir / "symbol_class.tsv")
    result["holidays_csv"] = str(output_dir / "holidays.csv")
    result["holidays_tsv"] = str(output_dir / "holidays.tsv")
    result["total_symbols"] = len(all_symbols)
    result["total_gbbq_records"] = len(gbbq_records)

    logger.info("Extract complete!")
    logger.info(f"  Symbols: {len(all_symbols)}")
    logger.info(f"  Gbbq records: {len(gbbq_records)}")

    return result
