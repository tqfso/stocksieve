"""本文件生成按快照冻结的日、周、年 K 线和均线，分页不改变历史计算窗口。"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from .domain import Bar, adjusted_history
from .errors import MissingData

Period = Literal['day', 'week', 'year']


def period_key(day: date, period: Period) -> date:
    if period == 'week':
        return day - timedelta(days=day.weekday())
    if period == 'year':
        return date(day.year, 1, 1)
    return day


def complete_period(key: date, as_of: date, period: Period, calendar: dict[date, bool | None]) -> bool:
    """只有日历确认该周期后续不存在未收盘交易日，才标记周期完整。"""
    end = key if period == 'day' else key + timedelta(days=6) if period == 'week' else date(key.year, 12, 31)
    cursor = as_of + timedelta(days=1)
    while cursor <= end:
        if calendar.get(cursor) is not False:
            return False
        cursor += timedelta(days=1)
    return True


def optional_sum(values: list[Decimal | int | None]) -> float | int | None:
    if any(value is None for value in values):
        return None
    total = sum(value for value in values if value is not None)
    return int(total) if all(isinstance(value, int) for value in values) else float(total)


def candles(bars: list[Bar], calendar: dict[date, bool | None], as_of: date,
            period: Period = 'day', before: date | None = None, limit: int = 240, *, anchor_day: date | None = None) -> dict[str, Any]:
    """使用完整历史计算当前周期均线后分页；未知或缺失记录不补造为交易日。"""
    if period not in {'day', 'week', 'year'} or not 1 <= limit <= 1000:
        raise ValueError('K线参数无效')
    adjusted = adjusted_history([bar for bar in bars if bar.factor is not None], anchor_day or as_of)
    if any(calendar.get(bar.day) is not True for bar in adjusted):
        raise MissingData('行情日期未由交易日历确认')
    start = adjusted[0].day
    groups: dict[date, list[Bar]] = defaultdict(list)
    expected: dict[date, list[date]] = defaultdict(list)
    for bar in adjusted:
        groups[period_key(bar.day, period)].append(bar)
    # 1. 保留缺失周期的位置，避免均线在缺口后错误地向前多取一根。
    for day, is_open in sorted(calendar.items()):
        if start <= day <= as_of and is_open is not False:
            expected[period_key(day, period)].append(day)
    keys = sorted(expected)
    closes: list[Decimal | None] = []
    result = []
    for key in keys:
        records = groups.get(key, [])
        covered = records and {bar.day for bar in records} == set(expected[key])
        closes.append(records[-1].close if covered else None)
        if not records:
            continue
        first, last = records[0], records[-1]
        item: dict[str, Any] = {
            'time': last.day.isoformat(), 'open': float(first.open), 'high': float(max(bar.high for bar in records)),
            'low': float(min(bar.low for bar in records)), 'close': float(last.close),
            'volume': optional_sum([bar.volume for bar in records]), 'amount': optional_sum([bar.amount for bar in records]),
            'isComplete': bool(covered) and complete_period(key, as_of, period, calendar),
        }
        # 2. 当前周期独立计算均线，不能用日均线填充周线或年线。
        for length in (5, 10, 20, 60, 120):
            window = closes[-length:]
            item[f'ma{length}'] = float(sum(value for value in window if value is not None) / length) if len(window) == length and all(value is not None for value in window) else None
        result.append(item)
    # 3. 均线已经计算完成，随后才按游标取页。
    eligible = [item for item in result if before is None or item['time'] < before.isoformat()]
    selected = eligible[-limit:]
    has_more = len(eligible) > len(selected)
    return {'period': period, 'adjustment': 'qfq', 'items': selected, 'hasMore': has_more,
            'nextBefore': selected[0]['time'] if has_more and selected else None}
