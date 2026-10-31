"""本文件提供数据渠道能力接口、注册表和共享频控；默认不启用尚未实测的免费渠道。"""

import hashlib
import base64
import json
import sqlite3
import time
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from .domain import BOARDS, Bar, Stock, StockState
from .errors import DomainError

SHANGHAI = ZoneInfo('Asia/Shanghai')


def supplier(url: str, fallback: str) -> str:
    """同一供应商的域名别名及子域名共用频控，不按接口功能拆分预算。"""
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname or ''
    for domain, name in [('sina.com.cn', 'sina'), ('sina.cn', 'sina'), ('eastmoney.com', 'eastmoney'),
                         ('qq.com', 'tencent'), ('gtimg.cn', 'tencent'), ('sse.com.cn', 'sse'),
                         ('szse.cn', 'szse'), ('bse.cn', 'bse'), ('cninfo.com.cn', 'cninfo')]:
        if host == domain or host.endswith('.' + domain):
            return name
    return fallback


@dataclass(frozen=True)
class CalendarDay:
    is_open: bool | None
    close_at: datetime | None = None


@dataclass(frozen=True)
class Initialization:
    stocks: tuple[Stock, ...]
    calendar: dict[str, dict[date, CalendarDay]]


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


class Registry:
    """注册只建立能力映射，启用渠道须以实测覆盖为依据，不能静默替换渠道。"""

    def __init__(self) -> None:
        self.sources: dict[str, Source] = {}
        self.pending: dict[str, str] = {'sina': 'Sina', 'baostock': 'Baostock', 'eastmoney': '东方财富'}

    def register(self, source: Source) -> None:
        import re
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', source.id) or not source.supported_kinds <= {'initialize', 'catalog', 'daily'}:
            raise ValueError('数据源标识或能力声明无效')
        if source.id in self.sources:
            raise ValueError('重复注册数据源')
        self.sources[source.id] = source
        self.pending.pop(source.id, None)

    def get(self, source_id: str, kind: str) -> Source:
        source = self.sources.get(source_id)
        if not source or kind not in source.supported_kinds or kind == 'daily' and source.boards != frozenset(BOARDS):
            raise DomainError('SOURCE_UNAVAILABLE', '该渠道未启用、未通过覆盖验证或不支持此任务。')
        return source

    def public(self) -> list[dict[str, Any]]:
        return [{'id': key, 'name': source.name, 'available': True, 'supportedSyncKinds': sorted(source.supported_kinds)} for key, source in self.sources.items()] + [
            {'id': key, 'name': name, 'available': False, 'supportedSyncKinds': []} for key, name in self.pending.items()]


class SourceDeferred(Exception):
    """频控等待由持久化任务重新调度，不能在持有执行租约时睡眠。"""

    def __init__(self, until: datetime, message: str) -> None:
        super().__init__(message)
        self.until = until


class RateGate:
    """使用本机事务文件协调不同进程对同一供应商的预算和冷却。"""

    def __init__(self, path: Path, interval: float = 10, daily_limit: int = 1000) -> None:
        if not math.isfinite(interval) or interval < 10 or daily_limit < 1:
            raise ValueError('频率或预算配置无效')
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.interval, self.daily_limit = path, interval, daily_limit
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS gates (vendor TEXT PRIMARY KEY, day TEXT, used INTEGER, next_at REAL, blocked_until REAL)')

    def reserve(self, vendor: str, now: float | None = None) -> None:
        instant = time.time() if now is None else now
        day = datetime.fromtimestamp(instant, SHANGHAI).date()
        with sqlite3.connect(self.path, timeout=5) as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT day, used, next_at, blocked_until FROM gates WHERE vendor=?', (vendor,)).fetchone()
            old_day, used, next_at, blocked = row or (day.isoformat(), 0, 0, 0)
            used = used if old_day == day.isoformat() else 0
            until = max(next_at, blocked)
            if used >= self.daily_limit:
                from datetime import timedelta
                midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), SHANGHAI)
                until = max(until, midnight.timestamp())
            if until > instant:
                try:
                    blocked_date = datetime.fromtimestamp(until, timezone.utc)
                except (OverflowError, OSError, ValueError):
                    blocked_date = datetime.max.replace(tzinfo=timezone.utc)
                raise SourceDeferred(blocked_date, '渠道正处于限频、预算等待或冷却。')
            connection.execute('INSERT INTO gates VALUES (?,?,?,?,?) ON CONFLICT(vendor) DO UPDATE SET day=excluded.day,used=excluded.used,next_at=excluded.next_at,blocked_until=excluded.blocked_until',
                               (vendor, day.isoformat(), used + 1, instant + self.interval, blocked))

    def block(self, vendor: str, seconds: float, now: float | None = None) -> None:
        instant = time.time() if now is None else now
        with sqlite3.connect(self.path, timeout=5) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('UPDATE gates SET blocked_until=MAX(blocked_until,?) WHERE vendor=?', (instant + seconds, vendor))


class SourceHTTP:
    """GET 请求统一计费限频，无自动重试、代理轮换或隐含重定向。"""

    def __init__(self, gate: RateGate, cache_dir: Path, client: httpx.AsyncClient | None = None) -> None:
        self.gate, self.cache_dir = gate, cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                                                headers={'User-Agent': 'StockSieve/0.1'})

    async def get_json(self, vendor: str, url: str, params: dict[str, Any] | None = None, *, cache_seconds: int = 600) -> Any:
        vendor = supplier(url, vendor)
        key = hashlib.sha256(json.dumps([vendor, url, params], sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / (key + '.json')
        if path.exists():
            try:
                cached = json.loads(path.read_text())
                if 0 <= time.time() - cached['at'] <= cache_seconds:
                    return cached['data']
            except (ValueError, KeyError, OSError):
                pass
        content = await self.get_bytes(vendor, url, params)
        try:
            def reject_constant(value: str) -> Any:
                raise ValueError('非有限数值')
            data = json.loads(content, parse_constant=reject_constant)
        except ValueError as error:
            self.gate.block(vendor, 86400)
            raise DomainError('DATA_NOT_AVAILABLE', '渠道返回了不可读取的数据，已停止请求。', 503) from error
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps({'at': time.time(), 'data': data}, ensure_ascii=False, allow_nan=False))
        temporary.replace(path)
        return data

    def check_status(self, vendor: str, response: httpx.Response) -> None:
        if response.status_code in {403, 418, 429}:
            delay = 86400.0
            retry = response.headers.get('Retry-After')
            if retry:
                try:
                    seconds = float(retry)
                    if math.isfinite(seconds):
                        delay = max(delay, seconds)
                except ValueError:
                    try:
                        delay = max(delay, parsedate_to_datetime(retry).timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            self.gate.block(vendor, delay)
            raise DomainError('SOURCE_RATE_LIMITED', '渠道限制访问，已进入冷却。', 503)
        if response.status_code != 200:
            self.gate.block(vendor, 3600)
            raise DomainError('SOURCE_UNAVAILABLE', '渠道未返回有效响应。', 503)

    async def get_bytes(self, vendor: str, url: str, params: dict[str, Any] | None = None) -> bytes:
        """在流读取过程中限制响应大小，网页读取与行情请求复用同一限频入口。"""
        vendor = supplier(url, vendor)
        self.gate.reserve(vendor)
        try:
            async with self.client.stream('GET', url, params=params, follow_redirects=False) as response:
                self.check_status(vendor, response)
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > 8 * 1024 * 1024:
                        self.gate.block(vendor, 86400)
                        raise DomainError('DATA_NOT_AVAILABLE', '渠道响应超过大小限制，已暂停访问。', 503)
                    content.extend(chunk)
                return bytes(content)
        except httpx.HTTPError as error:
            self.gate.block(vendor, 3600)
            raise DomainError('SOURCE_TIMEOUT', '渠道访问失败，已暂停请求。', 503) from error

    async def document(self, vendor: str, url: str, cache_seconds: int = 86400) -> tuple[bytes, datetime]:
        """复用公开原文并保留实际获取时间，不能把缓存读取时间伪装成新抓取。"""
        key = hashlib.sha256(('document:' + url).encode()).hexdigest()
        path = self.cache_dir / (key + '.json')
        if path.exists():
            try:
                saved = json.loads(path.read_text())
                if 0 <= time.time() - saved['at'] <= cache_seconds:
                    return base64.b64decode(saved['body'], validate=True), datetime.fromtimestamp(saved['at'], timezone.utc)
            except (ValueError, KeyError, OSError):
                pass
        content = await self.get_bytes(vendor, url)
        instant = time.time()
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps({'at': instant, 'body': base64.b64encode(content).decode()}))
        temporary.replace(path)
        return content, datetime.fromtimestamp(instant, timezone.utc)

    async def close(self) -> None:
        await self.client.aclose()
