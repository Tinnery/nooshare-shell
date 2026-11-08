"""Tong 数据模型定义。"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DailyPrice:
    """日线行情数据。"""
    symbol: str
    date: int  # YYYYMMDD 格式
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float

    def validate(self) -> Optional[str]:
        """验证数据有效性，返回错误信息或 None。"""
        if not self.symbol:
            return "symbol is empty"
        if self.date == 0:
            return "date is zero"
        if self.date < 20080101:
            return f"date {self.date} before min 20080101"
        if self.low > self.open:
            return f"low ({self.low:.2f}) > open ({self.open:.2f})"
        if self.low > self.close:
            return f"low ({self.low:.2f}) > close ({self.close:.2f})"
        if self.high < self.open:
            return f"high ({self.high:.2f}) < open ({self.open:.2f})"
        if self.high < self.close:
            return f"high ({self.high:.2f}) < close ({self.close:.2f})"
        return None


@dataclass
class GbbqRecord:
    """除权除息记录。"""
    category: int
    stock_code: str
    symbol: str
    date: int  # YYYYMMDD 格式
    c1: float  # 分红 (dividend)
    c2: float  # 配股价 (rights price)
    c3: float  # 送股 (bonus shares)
    c4: float  # 转增 (rights shares)
