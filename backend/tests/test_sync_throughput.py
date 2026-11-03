"""本文件验证短间隔连续取数、会话复用与真实股票进度，确保提速不绕过预算和冷却。"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.providers.sina import SinaSource
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.shared.errors import DomainError


def test_continuous_requests_keep_shared_spacing_and_count_without_task_restarts(tmp_path: Path, monkeypatch: Any) -> None:
    import stocksieve.modules.ingestion.infrastructure.http as module
    instant = [datetime(2026, 11, 2, tzinfo=UTC).timestamp()]
    monkeypatch.setattr(module.time, 'time', lambda: instant[0])
    waits = []
    requests = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        instant[0] += seconds

    monkeypatch.setattr(module.asyncio, 'sleep', sleep)

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(instant[0])
        return httpx.Response(200, json={'ok': True})

    policy = SourcePolicy(tmp_path / 'rate', .2, 3)

    async def run() -> None:
        http = SourceHTTP(policy, tmp_path / 'cache', httpx.AsyncClient(transport=httpx.MockTransport(response)))
        try:
            for index in range(3):
                await http.get_json('sina', f'https://finance.sina.com.cn/test/{index}')
            with pytest.raises(SourceDeferred) as error:
                await http.get_json('sina', 'https://finance.sina.com.cn/test/over-budget')
            assert error.value.reason == 'SOURCE_ACCESS_WAIT'
        finally:
            await http.close()

    asyncio.run(run())
    assert len(requests) == 3 and len(waits) == 2
    assert all(b - a >= .199 for a, b in pairwise(requests))
    ledger = json.loads((tmp_path / 'rate/state.json').read_text())
    assert ledger['daily']['2026-11-02']['sina'] == 3


def test_even_short_real_cooldown_is_not_treated_as_request_spacing(tmp_path: Path, monkeypatch: Any) -> None:
    import stocksieve.modules.ingestion.infrastructure.http as module
    policy = SourcePolicy(tmp_path / 'rate', .2)
    policy.block('sina', .1)
    sleeper = AsyncMock()
    monkeypatch.setattr(module.asyncio, 'sleep', sleeper)

    async def run() -> None:
        http = SourceHTTP(policy, tmp_path / 'cache', httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('冷却期不能发请求'))))
        try:
            with pytest.raises(SourceDeferred) as error:
                await http.document('sina', 'https://finance.sina.com.cn/test')
            assert error.value.reason == 'SOURCE_ACCESS_WAIT'
            sleeper.assert_not_awaited()
        finally:
            await http.close()

    asyncio.run(run())


def test_http_429_stops_continuous_updates_without_retrying(tmp_path: Path, monkeypatch: Any) -> None:
    import stocksieve.modules.ingestion.infrastructure.http as module
    calls = []

    def response(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(429, headers={'Retry-After': '172800'})

    sleeper = AsyncMock()
    monkeypatch.setattr(module.asyncio, 'sleep', sleeper)

    async def run() -> None:
        http = SourceHTTP(SourcePolicy(tmp_path / 'rate', .2), tmp_path / 'cache', httpx.AsyncClient(transport=httpx.MockTransport(response)))
        try:
            with pytest.raises(DomainError):
                await http.document('sina', 'https://finance.sina.com.cn/test')
            with pytest.raises(SourceDeferred):
                await http.document('sina', 'https://finance.sina.com.cn/test')
            assert len(calls) == 1
            sleeper.assert_not_awaited()
        finally:
            await http.close()

    asyncio.run(run())


def test_sina_reuses_and_closes_http_session(tmp_path: Path, monkeypatch: Any) -> None:
    import stocksieve.modules.ingestion.infrastructure.providers.sina as module
    created = []

    class HTTP:
        def __init__(self, *args: Any) -> None:
            self.client = type('Client', (), {'headers': {}})()
            self.closed = False
            created.append(self)

        async def document(self, *args: Any) -> tuple[bytes, datetime]:
            assert not self.closed
            return b'{}', datetime.now(UTC)

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(module, 'SourceHTTP', HTTP)
    source = SinaSource(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache')

    async def run() -> None:
        await source.document('https://finance.sina.com.cn/a')
        await source.document('https://finance.sina.com.cn/b')
        assert len(created) == 1
        await source.close()
        assert created[0].closed and source._http is None

    asyncio.run(run())


def test_task_closes_source_session_when_deferred_and_completed(context: Any) -> None:
    context.submit('initialize'); context.run()
    close = AsyncMock()
    original = context.source.daily
    paused = False

    async def daily(*args: Any) -> Any:
        nonlocal paused
        if not paused:
            paused = True
            raise SourceDeferred(context.clock.now() + timedelta(seconds=1), '等待')
        return await original(*args)

    context.source.close, context.source.daily = close, daily
    context.submit('daily'); context.run()
    assert close.await_count == 1
    context.clock.advance(2); context.run()
    assert close.await_count == 2


def test_public_progress_uses_stock_count_and_reaches_100_only_after_publish(context: Any) -> None:
    context.submit('initialize'); context.run()
    original_daily, original_publish = context.source.daily, context.worker.sync.publish
    calls = 0
    publish_paused = False

    async def daily(*args: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SourceDeferred(context.clock.now() + timedelta(seconds=1), '等待')
        return await original_daily(*args)

    def publish(lease: Any) -> None:
        nonlocal publish_paused
        if not publish_paused:
            publish_paused = True
            raise SourceDeferred(context.clock.now() + timedelta(seconds=1), '待发布')
        original_publish(lease)

    context.source.daily, context.worker.sync.publish = daily, publish
    job = context.submit('daily'); context.run()
    partial = context.api.tasks.detail(job, 'sync')
    assert partial['stockProgress'] == {'processedCount': 1, 'totalCount': 5}
    assert partial['progress'] == 20
    context.clock.advance(2); context.run()
    pending = context.api.tasks.detail(job, 'sync')
    assert pending['stockProgress']['processedCount'] == 5 and pending['progress'] == 99.9
    context.clock.advance(2); context.run()
    done = context.api.tasks.detail(job, 'sync')
    assert done['status'] == 'succeeded' and done['progress'] == 100


def test_configurable_faster_spacing_has_a_lower_bound(context: Any, tmp_path: Path) -> None:
    assert replace(context.api.settings, request_interval=.2).request_interval == .2
    with pytest.raises(ValueError):
        replace(context.api.settings, request_interval=.1)
    with pytest.raises(ValueError):
        SourcePolicy(tmp_path / 'rate', .1)
