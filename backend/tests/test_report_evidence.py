"""本文件通过真实HTTP和隔离PostgreSQL验证事实核对、跨展示一致性及TTM计算。"""

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
from aiohttp import web
from stocksieve.interfaces.http.api import API
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.infrastructure.agent import LangGraphRuntime
from stocksieve.modules.analysis.infrastructure.reader import DocumentReader
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker

from .analysis_fixtures import FixtureAnalysisRuntime, generate
from .conftest import HTTPServer


def execute(context: Any, runtime: FixtureAnalysisRuntime) -> dict[str, Any]:
    context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    status, accepted, _ = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key='fact-verification-test')
    assert status == 202
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, runtime))
    assert asyncio.run(worker.run_once('analysis'))
    return context.request('GET', '/api/v1/analyses/' + accepted['data']['id'])[1]['data']


def test_shared_facts_fill_cards_and_metrics_without_replacing_model_decision(context: Any) -> None:
    class DecisionRuntime(FixtureAnalysisRuntime):
        async def execute(self, tools: Any) -> Any:
            report, usage = await super().execute(tools)
            report['overview']['decision'] = {'action': 'consider', 'label': '满足条件再评估', 'reason': '已读到经营数据，仍需等待观察条件。'}
            return report, usage

    context.published()
    report = generate(context, runtime=DecisionRuntime())['report']
    assert report['schemaVersion'] == 4 and len(report['sections']) == 6
    assert report['overview']['decision']['label'] == '满足条件再评估'
    assert '减持实施结果尚未完整核验' in report['criticalNotes']
    assert report['shareholders']['latestCount'] == 88254
    assert report['shareholders']['previousCount'] is None
    assert report['shareholders']['asOf'] == '2026-09-30'
    assert any(item['label'] == '营业收入' and item['value'] == '12.00 亿元' for item in report['metrics'])
    assert not any(item['value'] == '待核验' for item in report['metrics'])
    assert sum(item['label'] == '技术情景盈亏比' for item in report['metrics']) == 1
    assert all('盈亏平衡' not in item['label'] for item in report['metrics'])
    reference = next(item for item in report['sources'] if item['url'] == report['shareholders']['sourceUrl'])
    assert all(item['sourceId'] == reference['id'] for item in report['verifiedFacts'])


@pytest.mark.parametrize('invalid', ['value', 'unit', 'period', 'yoy', 'quarter_column', 'scope', 'length'])
def test_wrong_fact_or_excessive_text_cannot_be_published(context: Any, invalid: str) -> None:
    class InvalidFact(FixtureAnalysisRuntime):
        async def execute(self, tools: Any) -> Any:
            report, usage = await super().execute(tools)
            fact = report['facts'][1]
            if invalid == 'value':
                fact['value'] += 1
            elif invalid == 'unit':
                fact['unit'] = '万元'
            elif invalid == 'period':
                fact['periodEnd'] = '2025-09-30'
            elif invalid == 'yoy':
                fact['yoy'] = 999
            elif invalid == 'scope':
                report['facts'][0]['basis'] = 'A'
            elif invalid == 'length':
                report['sections'][0]['text'] = '长' * 241
            else:
                text = '单位：元\n本报告期 本报告期比上年同期增减 年初至报告期末 年初至报告期末比上年同期增减\n营业收入400.00 5.00% 1,200.00 -10.00%'
                tools.save('columns', 'research', '受控证券2026年三季度报告', {'text': text, 'pages': [{'page': 1, 'text': text}]}, url='https://www.sse.com.cn/fixture/columns')
                fact.update(sourceKey='columns', value=400, evidence='营业收入400.00 5.00% 1,200.00 -10.00%')
            return report, usage

    context.published()
    task = execute(context, InvalidFact())
    assert task['status'] == 'failed' and task['report'] is None
    assert task['error']['code'] == 'ANALYSIS_INVALID_REPORT'


def test_verified_periods_calculate_ttm_and_shareholder_changes(context: Any) -> None:
    class Periods(FixtureAnalysisRuntime):
        async def execute(self, tools: Any) -> Any:
            report, usage = await super().execute(tools)
            for key, title, end, revenue, profit in [('annual', '2025年年度报告', '2025-12-31', 1500000000, 200000000),
                                                     ('previous', '2025年三季度报告', '2025-09-30', 1000000000, 100000000)]:
                text = f'单位：元\n营业收入{revenue}\n归属于上市公司股东的净利润{profit}\n报告期末普通股股东总数100,000户'
                tools.save(key, 'research', '受控证券' + title, {'text': text, 'pages': [{'page': 1, 'text': text}]}, url='https://www.sse.com.cn/fixture/' + key)
                for metric, value, quote in [('revenue', revenue, f'营业收入{revenue}'), ('net_profit', profit, f'归属于上市公司股东的净利润{profit}')]:
                    report['facts'].append({'metric': metric, 'value': value, 'unit': '元', 'basis': 'consolidated',
                        'periodEnd': end, 'sourceKey': key, 'page': 1, 'evidence': quote})
                if key == 'annual':
                    report['facts'].append({'metric': 'shareholder_count', 'value': 100000, 'unit': '户', 'basis': 'all',
                        'periodEnd': end, 'sourceKey': key, 'page': 1, 'evidence': '报告期末普通股股东总数100,000户'})
            return report, usage

    context.published()
    report = generate(context, runtime=Periods())['report']
    assert report['valuation']['companyPe'] == 40
    assert next(item['value'] for item in report['metrics'] if item['label'] == '最近12个月营业收入') == '17.00 亿元'
    assert report['shareholders']['previousCount'] == 100000
    assert report['shareholders']['changePercent'] == -11.75
    assert not any('TTM' in item for item in report['coverageNotes'])


def test_source_wait_still_generates_from_available_data(context: Any) -> None:
    from datetime import timedelta

    class WaitingSource:
        async def search(self, query: str, exchange: str | None = None) -> Any:
            raise SourceDeferred(context.clock.now() + timedelta(hours=1), '公告渠道正在冷却。')

        async def document(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError('本用例不能下载资料')

    class MarketOnly(FixtureAnalysisRuntime):
        called = False

        async def execute(self, tools: Any) -> Any:
            self.called = True
            assert not tools.facts['researchCoverage']['publicDocumentsAvailable']
            assert tools.facts['snapshot']['stock']['code']
            keys = ['snapshot', 'general_trade']
            return {'overview': {'decision': {'action': 'wait', 'label': '观察价格条件', 'reason': '本次依据已有行情形成判断。'},
                    'holding': {'label': '按行情条件观察', 'reason': '公司经营和事件资料尚未补齐。'}, 'opportunities': [], 'risks': ['公告资料缺口仍需关注。']},
                'summary': '基于已有收盘行情观察走势和价位条件。',
                'sections': [{'id': key, 'title': key, 'headline': '已有行情观察', 'text': '依据已提供行情分析；没有公告时不补造公司经营事实。', 'sourceKeys': keys}
                    for key in ('company', 'advantages', 'risks', 'holding', 'entry', 'sell')],
                'sourceKeys': keys, 'facts': [], 'missingInformation': ['公司公告尚未取得。']}, {'inputTokens': 10, 'outputTokens': 10}

    context.published()
    runtime = MarketOnly()
    context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    accepted = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key='wait-before-model')[1]['data']
    source = WaitingSource()
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, runtime, source, source, DocumentReader()))
    assert asyncio.run(worker.run_once('analysis'))
    task = context.request('GET', '/api/v1/analyses/' + accepted['id'])[1]['data']
    assert task['status'] == 'succeeded', task.get('error')
    assert runtime.called
    assert task['report']['reportScope'] == 'available_data'
    assert task['report']['researchStatus'] == 'unavailable'
    assert task['report']['sections'][0]['title'] == '已知公司资料'
    assert task['report']['sources'] and task['report']['criticalNotes']
    assert not task['report']['verifiedFacts']


def test_derived_data_is_returned_to_model_for_consistent_final_text(context: Any) -> None:
    """TTM和户数变化算出后必须进入定稿上下文，不能只更新卡片而保留旧的缺失说法。"""
    context.published()
    draft: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []

    async def model(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        payload = draft
        if len(calls) == 2:
            text = json.dumps(body, ensure_ascii=False)
            assert '后端已核对' in text and 'peTtm' in text and '40.0' in text
            payload = {key: draft[key] for key in ('overview', 'summary', 'sections', 'missingInformation')}
            payload['summary'] = '定稿已使用后端派生指标。'
            payload['missingInformation'] = ['行业估值尚未取得。']
        else:
            assert len(calls) == 1 and body.get('tools')
        return web.json_response({'id': 'derived-fixture', 'object': 'chat.completion', 'model': 'fixture', 'created': 1793332800,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(payload, ensure_ascii=False)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}})

    class SeededRuntime(LangGraphRuntime):
        async def execute(self, tools: Any) -> Any:
            response, _ = await FixtureAnalysisRuntime().execute(tools)
            for key, title, end, revenue, profit in [('annual', '2025年年度报告', '2025-12-31', 1500000000, 200000000),
                                                     ('previous', '2025年三季度报告', '2025-09-30', 1000000000, 100000000)]:
                text = f'单位：元\n营业收入{revenue}\n归属于上市公司股东的净利润{profit}'
                tools.save(key, 'research', title, {'text': text, 'pages': [{'page': 1, 'text': text}]}, url='https://www.sse.com.cn/fixture/' + key)
                for metric, value, quote in [('revenue', revenue, f'营业收入{revenue}'), ('net_profit', profit, f'归属于上市公司股东的净利润{profit}')]:
                    response['facts'].append({'metric': metric, 'value': value, 'unit': '元', 'basis': 'consolidated',
                        'periodEnd': end, 'sourceKey': key, 'page': 1, 'evidence': quote})
            response['summary'] = '初稿尚未取得最近12个月结果。'
            draft.update(response)
            return await super().execute(tools)

    app = web.Application()
    app.router.add_post('/v1/chat/completions', model)
    app.freeze()
    server = HTTPServer(app)
    try:
        settings = replace(context.api.settings, ai_base_url=server.base_url + '/v1', ai_api_key='fixture', ai_model='fixture')
        runtime = SeededRuntime(settings)
        api = API(context.api.db, settings, context.api.registry, runtime, context.clock.now)
        context.api.runtime = runtime
        stock = context.source.universe_stocks[0].stock_id
        accepted = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key='derived-review-test')[1]['data']
        worker = Worker(api.tasks, Sync(api.tasks, api.registry), Analysis(api.tasks, runtime))
        assert asyncio.run(worker.run_once('analysis'))
        task = context.request('GET', '/api/v1/analyses/' + accepted['id'])[1]['data']
        assert task['status'] == 'succeeded', task.get('error')
        assert len(calls) == 2
        assert task['report']['valuation']['companyPe'] == 40
        assert task['report']['summary'] == '定稿已使用后端派生指标。'
    finally:
        server.close()
