"""本文件定义历史补齐和最新快照窗口上限，旧快照的数据保留不受窗口收缩影响。"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from stocksieve.shared.errors import MissingData


@dataclass(frozen=True)
class HistoryPolicy:
    """按交易日配置历史窗口；0表示最新快照不设上限，最少140天满足现有策略。"""

    initial_days: int = 150
    max_days: int = 0

    def __post_init__(self) -> None:
        if type(self.initial_days) is not int or self.initial_days < 140:
            raise ValueError('历史补齐天数必须是至少140个交易日的整数。')
        if type(self.max_days) is not int or self.max_days < 0 or self.max_days and self.max_days < self.initial_days:
            raise ValueError('历史上限需为0，或不小于补齐天数的整数交易日。')

    def calendar_start(self, target: date) -> date:
        """为交易日选择准备足够自然日历，最早覆盖到A股市场建立年份。"""
        days = max(730, max(self.initial_days, self.max_days) * 2 + 60)
        return max(date(1990, 1, 1), target - timedelta(days=min(days, target.toordinal() - 1)))

    def initial_dates(self, days: Iterable[date], target: date) -> list[date]:
        """只接受已确认开市且不晚于目标日的日期，不以工作日推算。"""
        selected = sorted({day for day in days if day <= target})[-self.initial_days:]
        if len(selected) < self.initial_days:
            raise MissingData(f'历史交易日不足配置要求的{self.initial_days}天。')
        return selected

    def cutoff(self, days: Iterable[date], target: date) -> date | None:
        """返回最新窗口下界；不修改任何已发布快照或执行物理删除。"""
        if not self.max_days:
            return None
        selected = sorted({day for day in days if day <= target})
        if not selected:
            raise MissingData('历史窗口缺少真实交易日历。')
        return selected[-self.max_days] if len(selected) >= self.max_days else selected[0]
