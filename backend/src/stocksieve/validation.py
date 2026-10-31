"""本文件校验请求体与查询参数，拒绝未知字段、非法日期、重复参数和非有限数值。"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from multidict import MultiDictProxy
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domain import Strategy
from .errors import DomainError


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class LoginInput(Input):
    username: str
    password: str = Field(min_length=1, max_length=32)


class UserInput(Input):
    username: str
    name: str
    password: str = Field(min_length=6, max_length=32)


class PasswordInput(Input):
    oldPassword: str = Field(min_length=1, max_length=32)
    newPassword: str = Field(min_length=6, max_length=32)


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


class AnalysisInput(Input):
    kind: Literal['fundamental', 'chips', 'trade']
    snapshotId: str | None = Field(default=None, min_length=1)
    strategy: Strategy | None = None


def invalid(field: str, message: str = '参数无效。') -> DomainError:
    return DomainError('INVALID_ARGUMENT', message, details={'field': field})


def known_query(query: MultiDictProxy[str], allowed: set[str]) -> dict[str, str]:
    for key in query:
        if key not in allowed or len(query.getall(key)) != 1:
            raise invalid(key, '存在未知或重复的查询参数。')
    return {key: value for key, value in query.items() if value != ''}


def integer(query: dict[str, str], key: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    raw = query.get(key, str(default))
    if len(raw) > 10 or not re.fullmatch(r'[0-9]+', raw):
        raise invalid(key, '请输入有效整数。')
    value = int(raw)
    if value < minimum or value > (maximum if maximum is not None else 2147483647):
        raise invalid(key, '整数参数超出允许范围。')
    return value


def number(query: dict[str, str], key: str, maximum: Decimal | None = None) -> Decimal | None:
    raw = query.get(key)
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise invalid(key) from error
    if not value.is_finite() or value < 0 or (maximum is not None and value > maximum):
        raise invalid(key, '数值参数超出允许范围。')
    return value


def date_value(raw: str | None, field: str = 'dataDate') -> date | None:
    if raw is None:
        return None
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
            raise ValueError('date')
        return date.fromisoformat(raw)
    except ValueError as error:
        raise invalid(field, '日期需为有效的 YYYY-MM-DD。') from error


def resource_id(value: str, code: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise DomainError(code, '资源不存在。', 404) from error
