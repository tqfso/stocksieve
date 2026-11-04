"""本文件定义渠道能力与数据契约，业务仅依赖这些端口。"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from stocksieve.modules.market.domain.history import HistoryPolicy
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
    excluded_reason: str | None = None


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


@runtime_checkable
class HistorySource(Protocol):
    """支持按任务固定历史配置及扩展真实日历的可选渠道能力。"""

    def for_history(self, policy: HistoryPolicy) -> Source: ...
    async def history_calendar(self, target: date) -> tuple[dict[str, dict[date, CalendarDay]], datetime]: ...


@runtime_checkable
class CalendarCacheSource(Protocol):
    """可复用已持久化真实日历的渠道能力，减少相同日历的重复取数。"""

    def use_calendar(self, calendar: dict[str, dict[date, CalendarDay]], fetched_at: datetime) -> None: ...


@runtime_checkable
class SharedCalendarSource(CalendarCacheSource, Protocol):
    """可采用已确认的交易所日历，价格和因子仍须来自自身渠道。"""

    accepts_shared_calendar: bool


@runtime_checkable
class SecuritiesAwareSource(Protocol):
    """基础资料更新可复用已经确认的证券身份和上市日期。"""

    def use_securities(self, stocks: tuple[Stock, ...]) -> None: ...


@runtime_checkable
class IncrementalSource(Protocol):
    """支持增量的渠道声明策略所需历史长度，较早历史通过快照链保留。"""

    incremental_history_days: int


@runtime_checkable
class SnapshotScopedSource(Protocol):
    """续跑时保持任务已冻结的字段来源，配置变化只影响新任务。"""

    def for_snapshot(self, provenance: dict[str, Any], *, force: bool = False) -> Source: ...


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
    """额度、冷却和长等待由任务重新调度；明确的短请求间隔可在异步HTTP边界等待。"""

    def __init__(self, until: datetime, message: str, *, reason: str = 'SOURCE_ACCESS_WAIT') -> None:
        super().__init__(message)
        self.until = until
        self.reason = reason


@runtime_checkable
class SourceSession(Protocol):
    """可持有任务内HTTP连接的渠道，执行结束或暂停时必须释放连接。"""

    async def close(self) -> None: ...


class RatePolicy(Protocol):
    def reserve(self, vendor: str, now: float | None = None) -> None: ...
    def block(self, vendor: str, seconds: float, now: float | None = None) -> None: ...


class PublicHTTPClient(Protocol):
    """公开资料读取端口，由网络适配器实现；应用服务不依赖具体HTTP客户端。"""

    async def document(self, vendor: str, url: str, cache_seconds: int = 86400, stale_seconds: int = 0) -> tuple[bytes, datetime]: ...
    async def close(self) -> None: ...


class AnnouncementHTTPClient(PublicHTTPClient, Protocol):
    """公告索引的受控POST读取，沿用同一缓存和供应商频控。"""

    async def post_document(self, vendor: str, url: str, data: dict[str, Any], cache_seconds: int = 3600,
                            stale_seconds: int = 0) -> tuple[bytes, datetime]: ...
