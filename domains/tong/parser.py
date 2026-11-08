"""Tong .day 二进制文件解析器。"""

import struct
from pathlib import Path
from typing import List
from .models import DailyPrice


def parse_day_file(day_path: Path, symbol: str = None) -> List[DailyPrice]:
    """
    解析 Tong .day 二进制文件。
    
    格式：每条记录 32 字节，结构为 <IIIIIIIf
    - date: uint32 (YYYYMMDD)
    - open: uint32 (价格，除以 100 得到元)
    - high: uint32
    - low: uint32
    - close: uint32
    - amount: float32 (成交额)
    - volume: uint32 (成交量)
    - reserved: uint32 (保留字段)
    
    Args:
        day_path: .day 文件路径
        symbol: 股票代码，如果为 None 则从文件名提取
    
    Returns:
        DailyPrice 列表
    """
    if symbol is None:
        symbol = day_path.stem  # 去掉 .day 后缀
    
    data = day_path.read_bytes()
    record_size = 32
    
    if len(data) % record_size != 0:
        raise ValueError(f"文件 {day_path} 大小不是 {record_size} 的倍数")
    
    count = len(data) // record_size
    records = []
    
    # 使用 struct 批量解析，提高效率
    # 格式：date(I) open(I) high(I) low(I) close(I) amount(f) volume(I) reserved(I)
    fmt = '<IIIIIfII'
    
    for i in range(count):
        offset = i * record_size
        chunk = data[offset:offset + record_size]
        
        date, open_raw, high_raw, low_raw, close_raw, amount, volume, _ = struct.unpack(fmt, chunk)
        
        # 过滤掉 2008 年之前的数据
        if date < 20080101:
            continue
        
        records.append(DailyPrice(
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
    """递归查找所有 .day 文件。"""
    day_files = []
    for path in root_dir.rglob('*.day'):
        if path.is_file():
            day_files.append(path)
    return sorted(day_files)
