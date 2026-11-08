"""
复权计算模块。
精确翻译 Go ticker-tong/tickers/tong/adjust_calc.go 和 adjust.go。

后复权（HFQ）：以最早日期为基准，价格向上调整
前复权（FFQ/QFQ）：以最新日期为基准，价格向下调整
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Set, Optional

from .models import DailyPrice, GbbqRecord


@dataclass
class AdjustedDailyPrice:
    symbol: str
    date: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    pre_close: float
    factor: float


@dataclass
class XDXRInfo:
    dividend: float = 0.0
    rights_price: float = 0.0
    bonus_shares: float = 0.0
    rights_shares: float = 0.0


@dataclass
class HFQResult:
    factors: List[float]
    pre_closes: List[float]
    ex_rights_dates: Set[int]


@dataclass
class FFQResult:
    factors: List[float]
    pre_closes: List[float]
    ex_rights_dates: Set[int]


def calc_pre_base_factor(first_date: int, gbbq_records: List[GbbqRecord]) -> float:
    """
    计算首日之前的累积因子。
    只考虑送转股（C3, C4），不考虑现金分红（C1）。
    """
    factor = 1.0
    
    # 收集所有首日之前的事件
    events = []
    for r in gbbq_records:
        if r.category == 1 and r.date < first_date:
            events.append({
                'date': r.date,
                'c1': r.c1,
                'c3': r.c3,
                'c4': r.c4,
            })
    
    # 按日期排序
    events.sort(key=lambda x: x['date'])
    
    # 累积送转股因子
    for e in events:
        denom = 10.0
        numer = 10.0 + e['c3'] + e['c4']
        if denom > 0 and numer > 0:
            factor *= numer / denom
    
    return factor


def calc_hfq_factor(daily_records: List[DailyPrice], gbbq_records: List[GbbqRecord]) -> Optional[HFQResult]:
    """
    计算后复权因子。
    """
    if not daily_records:
        return None
    
    # 按日期排序
    daily_records.sort(key=lambda x: x.date)
    
    n = len(daily_records)
    
    # 建立日期到索引的映射
    date_to_idx = {d.date: i for i, d in enumerate(daily_records)}
    
    xdxr_map: Dict[int, XDXRInfo] = {}
    ex_rights_dates: Set[int] = set()
    
    first_date = daily_records[0].date
    
    # 计算首日之前的累积因子
    pre_base_factor = calc_pre_base_factor(first_date, gbbq_records)
    
    # 处理除权除息事件
    for r in gbbq_records:
        if r.category == 1:
            gbbq_date = r.date
            
            # 找到第一个 >= gbbq_date 的交易日
            effective_date = None
            for d in daily_records:
                if d.date >= gbbq_date:
                    effective_date = d.date
                    break
            
            if effective_date is not None:
                if effective_date not in xdxr_map:
                    xdxr_map[effective_date] = XDXRInfo()
                
                info = xdxr_map[effective_date]
                info.dividend += r.c1
                info.bonus_shares += r.c3
                info.rights_shares += r.c4
                info.rights_price = r.c2
                ex_rights_dates.add(effective_date)
    
    # 计算 pre_closes
    pre_closes = [0.0] * n
    for i in range(n):
        if i == 0:
            pre_closes[i] = daily_records[i].close
            continue
        
        prev_close = daily_records[i - 1].close
        date = daily_records[i].date
        
        if date in xdxr_map:
            xdxr = xdxr_map[date]
            denom = 10.0 + xdxr.bonus_shares + xdxr.rights_shares
            if denom == 0:
                pre_closes[i] = prev_close
            else:
                pre_closes[i] = (prev_close * 10.0 - xdxr.dividend + xdxr.rights_shares * xdxr.rights_price) / denom
        else:
            pre_closes[i] = prev_close
    
    # 计算因子
    factors = [0.0] * n
    factor = pre_base_factor
    
    for i in range(n):
        if i > 0 and pre_closes[i] > 0 and daily_records[i - 1].close > 0:
            ratio = daily_records[i - 1].close / pre_closes[i]
            if ratio > 0 and abs(ratio - 1.0) > 0.0001:
                factor *= ratio
        factors[i] = factor
    
    return HFQResult(factors=factors, pre_closes=pre_closes, ex_rights_dates=ex_rights_dates)


def calc_ffq_factor(daily_records: List[DailyPrice], gbbq_records: List[GbbqRecord]) -> Optional[FFQResult]:
    """
    计算前复权因子。
    """
    if not daily_records:
        return None
    
    # 按日期排序
    daily_records.sort(key=lambda x: x.date)
    
    n = len(daily_records)
    
    xdxr_map: Dict[int, XDXRInfo] = {}
    ex_rights_dates: Set[int] = set()
    
    # 处理除权除息事件
    for r in gbbq_records:
        if r.category == 1:
            gbbq_date = r.date
            
            # 找到第一个 >= gbbq_date 的交易日
            effective_date = None
            for d in daily_records:
                if d.date >= gbbq_date:
                    effective_date = d.date
                    break
            
            if effective_date is not None:
                if effective_date not in xdxr_map:
                    xdxr_map[effective_date] = XDXRInfo()
                
                info = xdxr_map[effective_date]
                info.dividend += r.c1
                info.bonus_shares += r.c3
                info.rights_shares += r.c4
                info.rights_price = r.c2
                ex_rights_dates.add(effective_date)
    
    # 计算 pre_closes
    pre_closes = [0.0] * n
    for i in range(n):
        if i == 0:
            pre_closes[i] = daily_records[i].close
            continue
        
        prev_close = daily_records[i - 1].close
        date = daily_records[i].date
        
        if date in xdxr_map:
            xdxr = xdxr_map[date]
            denom = 10.0 + xdxr.bonus_shares + xdxr.rights_shares
            if denom == 0:
                pre_closes[i] = prev_close
            else:
                pre_closes[i] = (prev_close * 10.0 - xdxr.dividend + xdxr.rights_shares * xdxr.rights_price) / denom
        else:
            pre_closes[i] = prev_close
    
    # 计算因子（从后往前）
    factors = [0.0] * n
    factor = 1.0
    
    for i in range(n - 1, -1, -1):
        factors[i] = factor
        if i > 0 and pre_closes[i] > 0 and daily_records[i - 1].close > 0:
            ratio = pre_closes[i] / daily_records[i - 1].close
            if ratio > 0 and abs(ratio - 1.0) > 0.0001:
                factor *= ratio
    
    return FFQResult(factors=factors, pre_closes=pre_closes, ex_rights_dates=ex_rights_dates)


def apply_hfq(records: List[DailyPrice], result: HFQResult) -> List[AdjustedDailyPrice]:
    """应用后复权因子。"""
    factors = result.factors
    pre_closes = result.pre_closes
    
    adj = []
    for i, r in enumerate(records):
        f = factors[i]
        pc = pre_closes[i]
        
        adj.append(AdjustedDailyPrice(
            symbol=r.symbol,
            date=r.date,
            open=round(r.open * f * 100) / 100,
            high=round(r.high * f * 100) / 100,
            low=round(r.low * f * 100) / 100,
            close=round(r.close * f * 100) / 100,
            volume=r.volume,
            amount=r.amount,
            pre_close=round(pc * f * 100) / 100,
            factor=round(f * 10000) / 10000,
        ))
    
    return adj


def apply_ffq(records: List[DailyPrice], result: FFQResult) -> List[AdjustedDailyPrice]:
    """应用前复权因子。"""
    factors = result.factors
    pre_closes = result.pre_closes
    
    adj = []
    for i, r in enumerate(records):
        f = factors[i]
        pc = pre_closes[i]
        
        adj.append(AdjustedDailyPrice(
            symbol=r.symbol,
            date=r.date,
            open=round(r.open * f * 100) / 100,
            high=round(r.high * f * 100) / 100,
            low=round(r.low * f * 100) / 100,
            close=round(r.close * f * 100) / 100,
            volume=r.volume,
            amount=r.amount,
            pre_close=round(pc * f * 100) / 100,
            factor=round(f * 10000) / 10000,
        ))
    
    return adj


def load_stock_symbols(csv_path: str) -> Set[str]:
    """从 symbol_class.csv 加载 stock 类型的 symbol。"""
    stock_set = set()
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)  # 跳过 header
        
        for row in reader:
            if len(row) >= 2 and row[1] == 'stock':
                stock_set.add(row[0])
    
    return stock_set


def write_adjusted_csv(csv_path: str, records: List[AdjustedDailyPrice], is_hfq: bool = True):
    """写入复权后的 CSV 文件。"""
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    
    factor_col = 'hfq_factor' if is_hfq else 'ffq_factor'
    header = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'preclose', factor_col]
    
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        
        for r in records:
            writer.writerow([
                r.date,
                f'{r.open:.2f}',
                f'{r.high:.2f}',
                f'{r.low:.2f}',
                f'{r.close:.2f}',
                f'{r.volume:.2f}',
                f'{r.amount:.2f}',
                f'{r.pre_close:.2f}',
                f'{r.factor:.4f}',
            ])


def write_adjusted_tsv(tsv_path: str, records: List[AdjustedDailyPrice], is_hfq: bool = True):
    """写入复权后的 TSV 文件。"""
    Path(tsv_path).parent.mkdir(parents=True, exist_ok=True)
    
    factor_col = 'hfq_factor' if is_hfq else 'ffq_factor'
    header = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'preclose', factor_col]
    
    with open(tsv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(header)
        
        for r in records:
            writer.writerow([
                r.date,
                f'{r.open:.2f}',
                f'{r.high:.2f}',
                f'{r.low:.2f}',
                f'{r.close:.2f}',
                f'{r.volume:.2f}',
                f'{r.amount:.2f}',
                f'{r.pre_close:.2f}',
                f'{r.factor:.4f}',
            ])


def load_day_records_from_csv(csv_path: str) -> List[DailyPrice]:
    """从 CSV 加载日线数据。"""
    records = []
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        
        if header is None:
            return records
        
        for row in reader:
            if len(row) < 7:
                continue
            
            date = int(row[0])
            open_price = float(row[1])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            volume = float(row[5])
            amount = float(row[6])
            
            # 从文件名提取 symbol
            symbol = Path(csv_path).stem
            
            records.append(DailyPrice(
                symbol=symbol,
                date=date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
            ))
    
    return records
