"""本文件校验通用标识、整数、数值和日期，不依赖请求或响应对象。"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from stocksieve.shared.errors import DomainError


def invalid(field: str, message: str = '参数无效。') -> DomainError:
    return DomainError('INVALID_ARGUMENT', message, details={'field': field})


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
