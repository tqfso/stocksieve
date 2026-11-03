"""本文件验证新浪准备阶段在频控、缓存过期和进程重启后续跑，已完成批次不重放。"""

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.application.progress import progress_sink
from stocksieve.modules.ingestion.infrastructure.checkpoint import SourceCheckpoint
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.providers.sina import SinaSource
from stocksieve.modules.ingestion.infrastructure.providers.tencent import (
    TencentMetadata,
)
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.domain.models import Stock


def securities() -> tuple[Stock, ...]:
    """构造跨四板块、需要三个目录页和三个报价批次的证券集合。"""
    return tuple(Stock(f'{exchange}.{prefix}{number:03}', f'{prefix}{number:03}', f'测试{prefix}{number}', exchange, board)
        for prefix, exchange, board in [('600', 'SSE', 'SSE_MAIN'), ('000', 'SZSE', 'SZSE_MAIN'),
            ('300', 'SZSE', 'CHINEXT'), ('688', 'SSE', 'STAR')] for number in range(61))


def test_directory_resumes_after_cache_expiry_without_repeated_network_or_logs(tmp_path: Path, monkeypatch: Any) -> None:
    import stocksieve.modules.ingestion.infrastructure.http as http_module
    stocks = securities()
    rows = [{'symbol': ('sh' if stock.exchange == 'SSE' else 'sz') + stock.code, 'name': stock.name} for stock in stocks]
    now = [datetime(2026, 11, 2, tzinfo=UTC).timestamp()]
    monkeypatch.setattr(http_module.time, 'time', lambda: now[0])
    network: Counter[str] = Counter()
    messages: list[str] = []

    class Gate:
        available = False

        def reserve(self, vendor: str) -> None:
            if not self.available:
                raise SourceDeferred(datetime.fromtimestamp(now[0] + 1, UTC), '等待间隔')
            self.available = False

        def block(self, vendor: str, seconds: float) -> None:
            pytest.fail('正常分页不应触发冷却')

    gate = Gate()

    def respond(request: httpx.Request) -> httpx.Response:
        network[str(request.url)] += 1
        if 'getHQNodeStockCount' in request.url.path:
            return httpx.Response(200, json=str(len(rows)))
        start = (int(request.url.params['page']) - 1) * 80
        return httpx.Response(200, json=rows[start:start + 80])

    client = SourceHTTP(gate, tmp_path / 'http', httpx.AsyncClient(transport=httpx.MockTransport(respond)))

    async def document(url: str, seconds: int = 600) -> tuple[bytes, datetime]:
        return await client.document('sina', url, seconds)

    try:
        for _ in range(6):
            gate.available = True
            # 每次恢复都重建适配器，并让普通HTTP缓存过期，不能靠短期缓存掩盖断点缺失。
            now[0] += 3601
            source = SinaSource(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache')
            source.document = document
            with progress_sink(lambda message, *args: messages.append(message), operation_id='same-task'):
                try:
                    result = asyncio.run(source.directory())
                    break
                except SourceDeferred:
                    continue
        else:
            pytest.fail('每次恢复只允许一个新请求时，目录应在五次内完成')
        assert len(result) == len(stocks)
        assert len(network) == 5 and set(network.values()) == {1}
        assert [message.split('目录')[1].split('/')[0] for message in messages] == ['80', '160', '240', '244']
        # 新任务必须重新建立目录，不能把旧任务冻结的目录当成今天的最新响应。
        gate.available = True
        now[0] += 3601
        with progress_sink(lambda *args: None, operation_id='different-task'), pytest.raises(SourceDeferred):
            asyncio.run(source.directory())
        assert next(count for url, count in network.items() if 'getHQNodeStockCount' in url) == 2
    finally:
        asyncio.run(client.close())


def test_quote_batches_resume_from_saved_cursor_after_restart(tmp_path: Path) -> None:
    stocks = securities()
    completed: list[str] = []
    available = False

    async def document(url: str, seconds: int = 600) -> tuple[bytes, datetime]:
        nonlocal available
        if not available:
            raise SourceDeferred(datetime.now(UTC) + timedelta(seconds=1), '等待间隔')
        available = False
        completed.append(url)
        values = []
        for symbol in url.split('list=')[1].split(','):
            fields = [''] * 32
            fields[0], fields[1], fields[8], fields[30] = symbol, '10', '1000', '2026-11-02'
            values.append(f'var hq_str_{symbol}="' + ','.join(fields) + '";')
        return ''.join(values).encode('gb18030'), datetime.now(UTC)

    for _ in range(3):
        available = True
        source = SinaSource(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache')
        source.document = document
        with progress_sink(lambda *args: None, operation_id='quotes-task'):
            checkpoint = SourceCheckpoint(tmp_path / 'checkpoint')
            try:
                asyncio.run(source.prepare_quotes(stocks, checkpoint=checkpoint))
            except SourceDeferred:
                pass
    assert len(completed) == len(set(completed)) == 3
    assert checkpoint.data['quoteIndex'] == len(stocks)
    restored = SinaSource(source.policy, source.cache)
    assert len(restored._quotes) == len(stocks)


def test_metadata_batches_resume_even_when_http_cache_has_expired(tmp_path: Path) -> None:
    stocks = securities()
    completed: list[tuple[str, ...]] = []
    available = False

    async def fetch(symbols: list[str]) -> bool:
        nonlocal available
        if not available:
            raise SourceDeferred(datetime.now(UTC) + timedelta(days=1), '日预算等待')
        available = False
        completed.append(tuple(symbols))
        return True

    for _ in range(3):
        available = True
        metadata = TencentMetadata(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache')
        metadata.fetch = fetch
        with progress_sink(lambda *args: None, operation_id='metadata-task'):
            checkpoint = SourceCheckpoint(tmp_path / 'checkpoint')
            try:
                asyncio.run(metadata.prepare(stocks, checkpoint=checkpoint))
            except SourceDeferred:
                pass
    assert len(completed) == len(set(completed)) == 3
    assert checkpoint.data['metadataIndex'] == len(stocks)


def test_rate_wait_reuses_passed_precheck_without_repeating_logs(context: Any) -> None:
    context.submit('initialize'); context.run()
    original_check, original_daily = context.source.precheck, context.source.daily
    checks = 0
    paused = False

    async def precheck(target: Any) -> list[dict[str, Any]]:
        nonlocal checks
        checks += 1
        return await original_check(target)

    async def daily(stock: Any, target: Any, history: Any) -> Any:
        nonlocal paused
        if not paused:
            paused = True
            raise SourceDeferred(context.clock.now() + timedelta(seconds=1), '等待间隔')
        return await original_daily(stock, target, history)

    context.source.precheck, context.source.daily = precheck, daily
    job = context.submit('daily'); context.run()
    context.clock.advance(2); context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'succeeded'
    assert checks == 1
    logs = context.api.tasks.log_page(job, {'limit': '200'})['items']
    assert sum(item['message'] == '所有开市板块已就绪。' for item in logs) == 1


def test_stock_not_ready_invalidates_passed_precheck_before_retry(context: Any) -> None:
    context.submit('initialize'); context.run()
    original_check, original_daily = context.source.precheck, context.source.daily
    checks = 0
    paused = False

    async def precheck(target: Any) -> list[dict[str, Any]]:
        nonlocal checks
        checks += 1
        return await original_check(target)

    async def daily(stock: Any, target: Any, history: Any) -> Any:
        nonlocal paused
        if not paused:
            paused = True
            raise SourceDeferred(context.clock.now() + timedelta(minutes=10), '个股未就绪', reason='BOARD_DATA_NOT_READY')
        return await original_daily(stock, target, history)

    context.source.precheck, context.source.daily = precheck, daily
    job = context.submit('daily'); context.run()
    context.clock.advance(601); context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'succeeded'
    assert checks == 2
