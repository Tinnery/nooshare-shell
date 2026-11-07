"""Daily data ETL flow: S3 -> Parse -> PostgreSQL."""

import csv
import logging
import shutil
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Optional

from ..pg import PGAdapter, PGConfig
from ..s3 import S3Adapter, S3Config
from ..tdx.parser import DailyRecord, find_day_files, parse_day_file

logger = logging.getLogger(__name__)


class DailyFlow:
    """ETL flow for daily price data."""
    
    def __init__(
        self,
        s3_config: Optional[S3Config] = None,
        pg_config: Optional[PGConfig] = None,
        work_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        max_workers: int = 8,
    ):
        """Initialize daily ETL flow.
        
        Args:
            s3_config: S3 connection config (loads from env if None)
            pg_config: PostgreSQL connection config (loads from env if None)
            work_dir: Working directory for temporary files
            output_dir: Output directory for CSV files
            max_workers: Number of parallel workers for parsing
        """
        self.s3_config = s3_config or S3Config.from_env()
        self.pg_config = pg_config or PGConfig.from_env()
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "flows_work"
        self.output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "flows_output"
        self.max_workers = max_workers
        
        self.s3_adapter = S3Adapter(self.s3_config)
        self.pg_adapter = PGAdapter(self.pg_config)
    
    def extract(self) -> Path:
        """Extract phase: Download and parse .day files from S3.
        
        Returns:
            Path to directory containing daily CSV files
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Download hsjday.zip
        hsjday_zip = self.work_dir / "hsjday.zip"
        if not hsjday_zip.exists():
            logger.info("Downloading hsjday.zip from S3...")
            self.s3_adapter.download_file("hsjday.zip", hsjday_zip)
        
        # Extract zip
        hsjday_dir = self.work_dir / "hsjday"
        if not hsjday_dir.exists():
            logger.info("Extracting hsjday.zip...")
            self._extract_zip(hsjday_zip, hsjday_dir)
        
        # Find and parse .day files
        logger.info("Finding .day files...")
        day_files = find_day_files(hsjday_dir)
        logger.info(f"Found {len(day_files)} .day files")
        
        # Parse .day files in parallel
        logger.info(f"Parsing .day files with {self.max_workers} workers...")
        daily_dir = self.output_dir / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        
        symbol_count = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._process_day_file, day_path, daily_dir): day_path
                for day_path in day_files
            }
            
            for future in as_completed(futures):
                if future.result():
                    symbol_count += 1
        
        logger.info(f"Parsed {symbol_count} symbols to {daily_dir}")
        return daily_dir
    
    def _process_day_file(self, day_path: Path, output_dir: Path) -> bool:
        """Process a single .day file and write CSV.
        
        Args:
            day_path: Path to .day file
            output_dir: Output directory for CSV
            
        Returns:
            True if successful, False otherwise
        """
        symbol = day_path.stem.lower()
        try:
            records = parse_day_file(day_path)
            self._write_daily_csv(records, output_dir / f"{symbol}.csv")
            return True
        except Exception as e:
            logger.warning(f"Failed to parse {day_path}: {e}")
            return False
    
    def _write_daily_csv(self, records: list[DailyRecord], output_path: Path) -> None:
        """Write daily records to CSV.
        
        Args:
            records: List of DailyRecord
            output_path: Output CSV path
        """
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
    
    def _extract_zip(self, zip_path: Path, dest_dir: Path) -> None:
        """Extract ZIP file safely.
        
        Args:
            zip_path: Path to ZIP file
            dest_dir: Destination directory
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, "r") as archive:
            for info in archive.infolist():
                # Skip directories
                if info.is_dir():
                    continue
                
                # Normalize path
                normalized = info.filename.replace("\\", "/")
                target = dest_dir / normalized
                
                # Security check
                try:
                    target.resolve().relative_to(dest_dir.resolve())
                except ValueError:
                    raise ValueError(f"Unsafe ZIP member path: {info.filename}")
                
                # Extract file
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    
    def load(self, daily_dir: Path) -> int:
        """Load phase: Write daily CSV data to PostgreSQL.
        
        Args:
            daily_dir: Directory containing daily CSV files
            
        Returns:
            Number of rows loaded
        """
        logger.info("Creating PostgreSQL table...")
        self.pg_adapter.create_daily_table()
        
        logger.info("Loading daily data to PostgreSQL...")
        row_count = self.pg_adapter.load_daily_data(daily_dir)
        
        return row_count
    
    def run(self) -> Dict:
        """Run complete ETL flow.
        
        Returns:
            Dictionary with flow results
        """
        logger.info("=" * 60)
        logger.info("Starting Daily ETL Flow")
        logger.info("=" * 60)
        
        try:
            # Extract phase
            logger.info("[1/2] Extract phase...")
            daily_dir = self.extract()
            
            # Load phase
            logger.info("[2/2] Load phase...")
            row_count = self.load(daily_dir)
            
            logger.info("=" * 60)
            logger.info("Daily ETL Flow completed successfully")
            logger.info(f"  Symbols: {len(list(daily_dir.glob('*.csv')))}")
            logger.info(f"  Rows loaded: {row_count:,}")
            logger.info("=" * 60)
            
            return {
                "status": "success",
                "daily_dir": str(daily_dir),
                "symbols": len(list(daily_dir.glob("*.csv"))),
                "rows_loaded": row_count,
            }
        
        finally:
            self.pg_adapter.close()


def run_daily_etl(
    s3_config: Optional[S3Config] = None,
    pg_config: Optional[PGConfig] = None,
    work_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    max_workers: int = 8,
) -> Dict:
    """Convenience function to run daily ETL flow.
    
    Args:
        s3_config: S3 connection config
        pg_config: PostgreSQL connection config
        work_dir: Working directory
        output_dir: Output directory
        max_workers: Number of parallel workers
        
    Returns:
        Flow results dictionary
    """
    flow = DailyFlow(
        s3_config=s3_config,
        pg_config=pg_config,
        work_dir=work_dir,
        output_dir=output_dir,
        max_workers=max_workers,
    )
    return flow.run()
