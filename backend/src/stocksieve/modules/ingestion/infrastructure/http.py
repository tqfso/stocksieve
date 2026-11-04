"""本文件实现公开HTTP读取、供应商归一化、缓存及受控请求。"""

import asyncio
import base64
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from stocksieve.modules.ingestion.application.ports import RatePolicy, SourceDeferred
from stocksieve.shared.errors import DomainError


def supplier(url: str, fallback: str) -> str:
    """同一供应商的域名别名及子域名共用频控，不按接口功能拆分预算。"""
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname or ''
    for domain, name in [('sina.com.cn', 'sina'), ('sina.cn', 'sina'), ('sse.com.cn', 'sse'),
                         ('szse.cn', 'szse'), ('bse.cn', 'bse'), ('cninfo.com.cn', 'cninfo'),
                         ('baidu.com', 'baidu'), ('baidu.cn', 'baidu')]:
        if host == domain or host.endswith('.' + domain):
            return name
    return fallback


class SourceHTTP:
    """公开读取统一计费限频，无自动重试、代理轮换或隐含重定向。"""

    def __init__(self, gate: RatePolicy, cache_dir: Path, client: httpx.AsyncClient | None = None) -> None:
        self.gate, self.cache_dir = gate, cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                                                headers={'User-Agent': 'StockSieve/0.1'})

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

    async def reserve(self, vendor: str) -> None:
        """短间隔最多异步等待两轮；每轮重新核对共享账本，预算与真实冷却仍交回任务排队。"""
        for attempt in range(3):
            try:
                self.gate.reserve(vendor)
                return
            except SourceDeferred as wait:
                delay = wait.until.timestamp() - time.time()
                if attempt == 2 or wait.reason != 'SOURCE_REQUEST_INTERVAL' or delay > 1:
                    raise
                await asyncio.sleep(max(.001, delay))

    async def get_bytes(self, vendor: str, url: str, params: dict[str, Any] | None = None, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        """在流读取过程中限制响应大小，网页读取与行情请求复用同一限频入口。"""
        return await self.request_bytes('GET', vendor, url, params=params, max_bytes=max_bytes)

    async def request_bytes(self, method: str, vendor: str, url: str, *, params: dict[str, Any] | None = None,
                            data: dict[str, Any] | None = None, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        """POST仅供固定公告适配器使用，不能由模型构造任意请求。"""
        vendor = supplier(url, vendor)
        await self.reserve(vendor)
        try:
            async with self.client.stream(method, url, params=params, json=data, follow_redirects=False) as response:
                self.check_status(vendor, response)
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > max_bytes:
                        raise DomainError('DATA_NOT_AVAILABLE', '本次文档超过大小上限，未读取；其他渠道请求仍遵守原有频控。', 503)
                    content.extend(chunk)
                return bytes(content)
        except httpx.HTTPError as error:
            self.gate.block(vendor, 3600)
            raise DomainError('SOURCE_TIMEOUT', '渠道访问失败，已暂停请求。', 503) from error

    async def document(self, vendor: str, url: str, cache_seconds: int = 86400, stale_seconds: int = 0) -> tuple[bytes, datetime]:
        """复用公开原文并保留实际获取时间，不能把缓存读取时间伪装成新抓取。"""
        return await self.cached_document('GET', vendor, url, None, cache_seconds, stale_seconds)

    async def post_document(self, vendor: str, url: str, data: dict[str, Any], cache_seconds: int = 3600,
                            stale_seconds: int = 0) -> tuple[bytes, datetime]:
        """公告POST查询按固定参数缓存，同证券相同查询不会重复访问上游。"""
        return await self.cached_document('POST', vendor, url, data, cache_seconds, stale_seconds)

    async def cached_document(self, method: str, vendor: str, url: str, data: dict[str, Any] | None,
                              cache_seconds: int, stale_seconds: int) -> tuple[bytes, datetime]:
        identity = 'document:' + url if method == 'GET' else 'post-document:' + url + json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        key = hashlib.sha256(identity.encode()).hexdigest()
        path = self.cache_dir / (key + '.json')
        cached = None
        if path.exists() and max(cache_seconds, stale_seconds) > 0:
            try:
                saved = json.loads(path.read_text())
                age = time.time() - saved['at']
                if 0 <= age <= max(cache_seconds, stale_seconds):
                    cached = base64.b64decode(saved['body'], validate=True), datetime.fromtimestamp(saved['at'], UTC)
                    if age <= cache_seconds:
                        return cached
            except (ValueError, KeyError, OSError):
                pass
        try:
            content = await self.request_bytes(method, vendor, url, data=data, max_bytes=16 * 1024 * 1024)
        except (SourceDeferred, DomainError):
            # 只在调用方显式允许时使用有限旧缓存，原获取时间和渠道冷却保持不变。
            if cached is not None and stale_seconds > 0:
                return cached
            raise
        instant = time.time()
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps({'at': instant, 'body': base64.b64encode(content).decode()}))
        temporary.replace(path)
        return content, datetime.fromtimestamp(instant, UTC)

    async def close(self) -> None:
        await self.client.aclose()
