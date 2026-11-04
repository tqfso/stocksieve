"""本文件通过真实项目HTTP与隔离PostgreSQL验证官方公告取证、备用来源及下载缓存。"""

import asyncio
import json
from dataclasses import replace
from io import BytesIO
from typing import Any

import httpx
import pytest
from reportlab.pdfgen import canvas
from stocksieve.modules.analysis.application.ports import SearchHit
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.infrastructure.documents import pdf_font
from stocksieve.modules.analysis.infrastructure.exchange_research import SzseSearch
from stocksieve.modules.analysis.infrastructure.reader import DocumentReader
from stocksieve.modules.analysis.infrastructure.research_registry import (
    ResearchRegistry,
)
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.jobs.application.worker import Worker

from .analysis_fixtures import FixtureAnalysisRuntime


@pytest.mark.parametrize('official_available', [True, False])
def test_official_source_is_sufficient_or_falls_back_without_resetting_limits(context: Any, official_available: bool) -> None:
    old = context.source.universe_stocks[1]
    stock = replace(old, stock_id='SZSE.002517', code='002517', name='恺英网络')
    context.source.universe_stocks = (context.source.universe_stocks[0], stock, *context.source.universe_stocks[2:])
    context.published()
    output = BytesIO()
    document = canvas.Canvas(output)
    pdf_font()
    document.setFont('StockSieveLatin', 10)
    document.drawString(40, 780, '002517')
    document.setFont(pdf_font(), 10)
    document.drawString(40, 760, '主要会计数据')
    document.drawString(40, 720, '报告期末普通股股东总数')
    document.setFont('StockSieveLatin', 10)
    document.drawString(180, 720, '123,456')
    document.save()
    pdf = output.getvalue()
    official_url = 'https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-10-29/fixture.PDF'
    fallback_url = 'https://static.cninfo.com.cn/finalpage/2026-10-29/fallback.PDF'
    requests: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.method == 'POST':
            assert json.loads(request.content)['stock'] == ['002517']
            if not official_available:
                return httpx.Response(403)
            return httpx.Response(200, json={'announceCount': 3, 'data': [
                {'secCode': ['002517'], 'title': '恺英网络：2026年三季度报告', 'publishTime': '2026-10-29 00:00:00', 'attachPath': '/disc/disk03/finalpage/2026-10-29/fixture.PDF'},
                {'secCode': ['000001'], 'title': '其他证券2026年三季度报告', 'attachPath': '/disc/disk03/finalpage/2026-10-29/other.PDF'},
                {'secCode': ['002517'], 'title': '恺英网络2026年三季度报告', 'attachPath': 'https://untrusted.invalid/fixture.pdf'}]})
        assert str(request.url) == (official_url if official_available else fallback_url)
        return httpx.Response(200, content=pdf)

    class Fallback:
        called = False

        async def search(self, query: str, exchange: str | None = None) -> list[SearchHit]:
            self.called = True
            assert not official_available, '官方资料足够时不能继续补源'
            return [SearchHit('恺英网络2026年三季度报告', fallback_url, '2026-10-29T00:00:00+08:00')]

    class ContractModel(FixtureAnalysisRuntime):
        async def execute(self, tools: Any) -> Any:
            response, usage = await super().execute(tools)
            expected = official_url if official_available else fallback_url
            key = next(key for key, item in tools.seen.items() if item['source_url'] == expected)
            response['facts'] = [{'metric': 'shareholder_count', 'value': 123456, 'unit': '户', 'basis': 'all',
                'periodEnd': '2026-09-30', 'sourceKey': key, 'page': 1, 'evidence': '报告期末普通股股东总数123,456'}]
            response['sourceKeys'] = [key, 'snapshot']
            for section in response['sections']:
                section['sourceKeys'] = [key, 'snapshot']
            return response, usage

    runtime = ContractModel()
    context.api.runtime = runtime
    accepted = context.request('POST', f'/api/v1/stocks/{stock.stock_id}/analyses', {}, key='official-source-test')[1]['data']
    fallback = Fallback()
    policy = SourcePolicy(context.api.settings.state_dir / 'source-test-rate', .2, 20000)

    async def run() -> None:
        http = SourceHTTP(policy, context.api.settings.state_dir / 'source-test-cache', httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        registry = ResearchRegistry()
        registry.register('szse', SzseSearch(http, context.clock.now), frozenset({'SZSE'}))
        registry.register('fallback', fallback, frozenset({'SZSE'}))
        try:
            worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, runtime, http, registry, DocumentReader()))
            assert await worker.run_once('analysis')
        finally:
            await http.close()

    asyncio.run(run())
    task = context.request('GET', '/api/v1/analyses/' + accepted['id'])[1]['data']
    assert task['status'] == 'succeeded', task.get('error')
    assert task['report']['shareholders']['latestCount'] == 123456
    assert task['report']['reportScope'] == 'company'
    assert fallback.called != official_available
    assert len(requests) == 2
    state = json.loads((context.api.settings.state_dir / 'source-test-rate/state.json').read_text())
    assert bool(state.get('cooldown', {}).get('szse')) != official_available
