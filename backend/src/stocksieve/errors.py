"""本文件定义可直接映射为接口错误的业务异常，避免暴露内部堆栈。"""

from typing import Any


class DomainError(Exception):
    """业务错误携带稳定错误码、HTTP 状态和面向用户的说明。"""

    def __init__(self, code: str, message: str, status: int = 400,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


class MissingData(Exception):
    """必要历史、日历、复权或成交数据不足，当前计算不可成立。"""
