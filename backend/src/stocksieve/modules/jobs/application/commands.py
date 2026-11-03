"""本文件定义同步与分析任务的应用输入契约。"""

import re
from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator

from stocksieve.modules.market.domain.models import Strategy
from stocksieve.shared.contracts import Input


class SyncInput(Input):
    kind: Literal['initialize', 'catalog', 'daily']
    source: str = Field(min_length=1, max_length=32)
    dataDate: date | None = None

    @field_validator('dataDate', mode='before')
    @classmethod
    def canonical_date(cls, value: Any) -> date | None:
        if value is None or isinstance(value, date) and not hasattr(value, 'hour'):
            return value
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('日期需为 YYYY-MM-DD')
        return date.fromisoformat(value)


class SyncRetryInput(Input):
    """重试只接受空对象，原任务的渠道、日期、范围和检查点不可由客户端改写。"""


class AnalysisInput(Input):
    kind: Literal['fundamental', 'chips', 'trade']
    snapshotId: str | None = Field(default=None, min_length=1)
    strategy: Strategy | None = None
