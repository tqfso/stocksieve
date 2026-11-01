"""本文件定义渠道能力与数据契约，业务仅依赖这些端口。"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from stocksieve.modules.market.domain.models import Bar, Stock, StockState
from stocksieve.shared.errors import DomainError


@dataclass(frozen=True)
class CalendarDay:
    is_open: bool | None
    close_at: datetime | None = None


@dataclass(frozen=True)
class Initialization:
    stocks: tuple[Stock, ...]
    calendar: dict[str, dict[date, CalendarDay]]
    calendar_fetched_at: datetime | None = None


@dataclass(frozen=True)
class DailyPacket:
    stock: Stock
    state: StockState
    bars: tuple[Bar, ...]


class Source(Protocol):
    """每个任务固定一个渠道；渠道必须声明可独立完成的任务和板块。"""

    id: str
    name: str
    supported_kinds: frozenset[str]
    boards: frozenset[str]

    async def initialize(self) -> Initialization: ...
    async def catalog(self, stocks: tuple[Stock, ...]) -> dict[str, Any]: ...
    async def precheck(self, target: date) -> list[dict[str, Any]]: ...
    async def universe(self, target: date, known: tuple[Stock, ...]) -> tuple[Stock, ...]: ...
    async def daily(self, stock: Stock, target: date, history: tuple[Bar, ...]) -> DailyPacket: ...


class CatalogOnlySource:
    """分类适配器的能力基类，其他任务在注册表和直接调用时均明确拒绝。"""

    supported_kinds = frozenset({'catalog'})

    async def initialize(self) -> Initialization:
        raise DomainError('SOURCE_UNAVAILABLE', '该渠道仅支持分类更新。')

    async def precheck(self, target: date) -> list[dict[str, Any]]:
        raise DomainError('SOURCE_UNAVAILABLE', '该渠道仅支持分类更新。')

    async def universe(self, target: date, known: tuple[Stock, ...]) -> tuple[Stock, ...]:
        raise DomainError('SOURCE_UNAVAILABLE', '该渠道仅支持分类更新。')

    async def daily(self, stock: Stock, target: date, history: tuple[Bar, ...]) -> DailyPacket:
        raise DomainError('SOURCE_UNAVAILABLE', '该渠道仅支持分类更新。')


class SourceDeferred(Exception):
    """频控等待由持久化任务重新调度，不能在持有执行租约时睡眠。"""

    def __init__(self, until: datetime, message: str) -> None:
        super().__init__(message)
        self.until = until


class RatePolicy(Protocol):
    def reserve(self, vendor: str, now: float | None = None) -> None: ...
    def block(self, vendor: str, seconds: float, now: float | None = None) -> None: ...


class PublicHTTPClient(Protocol):
    """公开资料读取端口，由网络适配器实现；应用服务不依赖具体HTTP客户端。"""

    async def document(self, vendor: str, url: str, cache_seconds: int = 86400) -> tuple[bytes, datetime]: ...
    async def get_json(self, vendor: str, url: str, params: dict[str, Any] | None = None, *, cache_seconds: int = 600) -> Any: ...
    async def close(self) -> None: ...
