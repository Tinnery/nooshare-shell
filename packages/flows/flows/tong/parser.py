"""Tong .day binary file parser."""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class DailyRecord:
    """Daily price record from Tong .day file."""
    
    symbol: str
    date: int  # YYYYMMDD format
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float


def parse_day_file(day_path: Path, symbol: str = None) -> List[DailyRecord]:
    """Parse Tong .day binary file.
    
    Format: Each record is 32 bytes with structure <IIIIIfII
    - date: uint32 (YYYYMMDD)
    - open: uint32 (price / 100)
    - high: uint32
    - low: uint32
    - close: uint32
    - amount: float32 (turnover)
    - volume: uint32 (volume)
    - reserved: uint32
    
    Args:
        day_path: Path to .day file
        symbol: Stock symbol, extracted from filename if None
        
    Returns:
        List of DailyRecord
    """
    if symbol is None:
        symbol = day_path.stem
    
    data = day_path.read_bytes()
    record_size = 32
    
    if len(data) % record_size != 0:
        raise ValueError(f"File {day_path} size is not a multiple of {record_size}")
    
    count = len(data) // record_size
    records = []
    
    # Format: date(I) open(I) high(I) low(I) close(I) amount(f) volume(I) reserved(I)
    fmt = '<IIIIIfII'
    
    for i in range(count):
        offset = i * record_size
        chunk = data[offset:offset + record_size]
        
        date, open_raw, high_raw, low_raw, close_raw, amount, volume, _ = struct.unpack(fmt, chunk)
        
        # Filter data before 2008
        if date < 20080101:
            continue
        
        records.append(DailyRecord(
            symbol=symbol,
            date=date,
            open=open_raw / 100.0,
            high=high_raw / 100.0,
            low=low_raw / 100.0,
            close=close_raw / 100.0,
            volume=volume,
            amount=amount,
        ))
    
    return records


def find_day_files(root_dir: Path) -> List[Path]:
    """Recursively find all .day files.
    
    Args:
        root_dir: Root directory to search
        
    Returns:
        Sorted list of .day file paths
    """
    day_files = []
    for path in root_dir.rglob('*.day'):
        if path.is_file():
            day_files.append(path)
    return sorted(day_files)
