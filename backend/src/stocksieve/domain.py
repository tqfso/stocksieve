"""本文件定义渠道无关的证券、原始日线、分类和预检数据模型。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from .errors import MissingData

Strategy = Literal['short', 'swing', 'long', 'trend_wait', 'base_wait']
BOARDS = {
    'SSE_MAIN': ('沪市主板', 'SSE'), 'SZSE_MAIN': ('深市主板', 'SZSE'),
    'CHINEXT': ('创业板', 'SZSE'), 'STAR': ('科创板', 'SSE'), 'BSE': ('北交所', 'BSE'),
}


def decimal_value(value: object) -> Decimal:
    """转换已取得的数值，拒绝 NaN、无穷值及布尔值。"""
    if isinstance(value, bool):
        raise ValueError('布尔值不是有效数值')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('数值必须有限')
    return result


@dataclass(frozen=True, slots=True)
class Stock:
    """证券身份由来源提供，板块未知时不能按代码前缀擅自补全。"""

    stock_id: str
    code: str
    name: str
    exchange: str
    board: str | None
    listed_on: date | None = None
    delisted_on: date | None = None

    def __post_init__(self) -> None:
        if len(self.code) != 6 or not self.code.isascii() or not self.code.isdigit():
            raise ValueError('股票代码必须为六位数字')
        if self.stock_id != f'{self.exchange}.{self.code}' or self.exchange not in {'SSE', 'SZSE', 'BSE'}:
            raise ValueError('股票身份与交易所不一致')
        if self.board is not None and (self.board not in BOARDS or BOARDS[self.board][1] != self.exchange):
            raise ValueError('上市板块与交易所不一致')
        if not self.name.strip():
            raise ValueError('股票名称不能为空')


@dataclass(frozen=True, slots=True)
class Bar:
    """原始成交量单位为股、金额为元；factor 为同口径累计复权因子。"""

    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    amount: Decimal | None
    volume: int | None = None
    turnover: Decimal | None = None
    total_market_cap: Decimal | None = None
    factor: Decimal | None = None

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if any(not value.is_finite() for value in values) or not (0 < self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError('OHLC 数据无效')
        for value in (self.amount, self.turnover, self.total_market_cap):
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError('成交或市值数据无效')
        if self.factor is not None and (not self.factor.is_finite() or self.factor <= 0):
            raise ValueError('复权因子必须为正数')
        if self.volume is not None and (isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0):
            raise ValueError('成交量必须为非负股数')


@dataclass(frozen=True, slots=True)
class StockState:
    """筛选所需的当日交易状态；未知标记不视为已确认正常。"""

    trade_status: str | None
    is_st: bool | None
    is_delisting: bool | None


def adjusted_history(bars: list[Bar], as_of: date) -> list[Bar]:
    """按计算日锚定前复权；先截断未来数据，成交量、金额和市值保持原值。"""
    selected = sorted((bar for bar in bars if bar.day <= as_of), key=lambda bar: bar.day)
    if not selected or selected[-1].day != as_of:
        raise MissingData('计算日行情缺失')
    if len({bar.day for bar in selected}) != len(selected):
        raise ValueError('同一版本存在重复行情日期')
    anchor = selected[-1].factor
    if anchor is None or any(bar.factor is None for bar in selected):
        raise MissingData('复权因子缺失')
    result = []
    for bar in selected:
        assert bar.factor is not None
        multiplier = bar.factor / anchor
        result.append(Bar(bar.day, bar.open * multiplier, bar.high * multiplier,
                          bar.low * multiplier, bar.close * multiplier, bar.amount,
                          bar.volume, bar.turnover, bar.total_market_cap, bar.factor))
    return result
