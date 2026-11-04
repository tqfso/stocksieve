"""本文件验证正式限频继承探测次数与冷却、跨进程锁和损坏状态保护。"""

import asyncio
import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.application.progress import progress_sink
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.shared.errors import DomainError


def test_formal_budget_preserves_used_counts_and_existing_cooldowns(tmp_path: Path) -> None:
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
    state = {'daily': {'2026-10-31': {'sina': 8, 'baidu': 9}}, 'next_at': now - 1, 'cooldown': {'baidu': now + 86400}}
    (tmp_path / 'state.json').write_text(json.dumps(state))
    policy = SourcePolicy(tmp_path, 10, 400)
    policy.reserve('sina', now)
    with pytest.raises(SourceDeferred):
        SourcePolicy(tmp_path, 10, 400).reserve('sina', now + 1)
    with pytest.raises(SourceDeferred):
        policy.reserve('baidu', now + 20)
    saved = json.loads((tmp_path / 'state.json').read_text())
    assert saved['daily']['2026-10-31'] == {'sina': 9, 'baidu': 9}
    assert saved['cooldown'] == state['cooldown']


def test_global_budget_and_corruption_never_silently_reset(tmp_path: Path) -> None:
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
    policy = SourcePolicy(tmp_path, 10, 2)
    policy.reserve('a', now); policy.reserve('b', now + 10)
    with pytest.raises(SourceDeferred):
        policy.reserve('c', now + 20)
    (tmp_path / 'state.json').write_text('invalid')
    with pytest.raises(DomainError):
        policy.reserve('a', now + 86400)


def test_one_second_shared_interval_and_daily_budget_reserved_for_regular_updates(tmp_path: Path) -> None:
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
    policy = SourcePolicy(tmp_path, 1, 8)
    with progress_sink(lambda *args: None, background=True):
        for offset in range(2):
            policy.reserve('sina', now + offset)
        with pytest.raises(SourceDeferred):
            policy.reserve('sina', now + 2)
    for offset in range(2, 7):
        policy.reserve('sina', now + offset)
    with pytest.raises(SourceDeferred):
        policy.reserve('baidu', now + 6.5)
    policy.reserve('baidu', now + 7)
    with pytest.raises(SourceDeferred):
        policy.reserve('sina', now + 8)
    saved = json.loads((tmp_path / 'state.json').read_text())
    assert sum(saved['daily']['2026-10-31'].values()) == 8


def test_recent_stale_cache_preserves_cooldown_and_original_fetch_time(tmp_path: Path) -> None:
    url = 'https://www.cninfo.com.cn/fixture'
    cache = tmp_path / 'cache'
    cache.mkdir()
    original = time.time() - 7200
    file = cache / (hashlib.sha256(('document:' + url).encode()).hexdigest() + '.json')
    file.write_text(json.dumps({'at': original, 'body': base64.b64encode(b'cached-index').decode()}))
    policy = SourcePolicy(tmp_path / 'rate', .2, 20000)
    policy.block('cninfo', 3600)
    before = json.loads((tmp_path / 'rate/state.json').read_text())

    def network(request: httpx.Request) -> httpx.Response:
        raise AssertionError('冷却期间不应发送网络请求')

    async def run() -> None:
        http = SourceHTTP(policy, cache, httpx.AsyncClient(transport=httpx.MockTransport(network)))
        try:
            body, stamp = await http.document('cninfo', url, cache_seconds=3600, stale_seconds=86400)
            assert body == b'cached-index' and stamp.timestamp() == pytest.approx(original, abs=1e-6, rel=0)
            with pytest.raises(SourceDeferred):
                await http.document('cninfo', url, cache_seconds=3600, stale_seconds=4000)
        finally:
            await http.close()

    asyncio.run(run())
    after = json.loads((tmp_path / 'rate/state.json').read_text())
    assert after['cooldown'] == before['cooldown']
    assert json.loads(file.read_text())['at'] == original


def test_local_size_limit_does_not_block_whole_supplier(tmp_path: Path) -> None:
    policy = SourcePolicy(tmp_path / 'rate', .2, 20000)

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'12345')))
        http = SourceHTTP(policy, tmp_path / 'cache', client)
        try:
            with pytest.raises(DomainError) as error:
                await http.get_bytes('cninfo', 'https://www.cninfo.com.cn/fixture', max_bytes=4)
            assert error.value.code == 'DATA_NOT_AVAILABLE'
        finally:
            await http.close()

    asyncio.run(run())
    saved = json.loads((tmp_path / 'rate/state.json').read_text())
    assert not saved.get('cooldown', {}).get('cninfo')


def test_post_queries_are_cached_by_body_and_preserve_fetch_time(tmp_path: Path) -> None:
    seen = []

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.method == 'POST'
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'code': seen[-1]['stock'][0]})

    async def run() -> None:
        http = SourceHTTP(SourcePolicy(tmp_path / 'rate', .2, 20000), tmp_path / 'cache',
                          httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        try:
            first, stamp = await http.post_document('szse', 'https://www.szse.cn/fixture', {'stock': ['002517']})
            again, cached = await http.post_document('szse', 'https://www.szse.cn/fixture', {'stock': ['002517']})
            second, _ = await http.post_document('szse', 'https://www.szse.cn/fixture', {'stock': ['300026']})
            assert first == again and stamp == cached
            assert json.loads(second)['code'] == '300026'
        finally:
            await http.close()

    asyncio.run(run())
    assert len(seen) == 2
