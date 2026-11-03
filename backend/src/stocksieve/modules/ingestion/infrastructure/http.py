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
    """GET 请求统一计费限频，无自动重试、代理轮换或隐含重定向。"""

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

    async def get_bytes(self, vendor: str, url: str, params: dict[str, Any] | None = None) -> bytes:
        """在流读取过程中限制响应大小，网页读取与行情请求复用同一限频入口。"""
        vendor = supplier(url, vendor)
        await self.reserve(vendor)
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
        if path.exists() and cache_seconds > 0:
            try:
                saved = json.loads(path.read_text())
                if 0 <= time.time() - saved['at'] <= cache_seconds:
                    return base64.b64decode(saved['body'], validate=True), datetime.fromtimestamp(saved['at'], UTC)
            except (ValueError, KeyError, OSError):
                pass
        content = await self.get_bytes(vendor, url)
        instant = time.time()
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps({'at': instant, 'body': base64.b64encode(content).decode()}))
        temporary.replace(path)
        return content, datetime.fromtimestamp(instant, UTC)

    async def close(self) -> None:
        await self.client.aclose()
