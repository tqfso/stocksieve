"""本文件验证真实分析服务的计算、来源校验、部分成功和等待建议，不调用付费模型。"""

import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from stocksieve import tables as t
from stocksieve.analysis import Analysis, AnalysisTools
from stocksieve.sync import Sync
from stocksieve.worker import Worker


class Runtime:
    def __init__(self, bad_source=False):
        self.bad_source = bad_source

    def identity(self):
        return {'provider': 'test', 'model': 'test', 'protocol': 'chat_completions'}

    async def execute(self, tools):
        keys = ['snapshot']
        if tools.facts.get('financial'):
            keys.append('financial')
        return {'overview': {'decision': {'action': 'consider', 'label': '可考虑', 'reason': '根据本次取得的资料形成分析。'},
                             'opportunities': ['已有部分资料可以继续跟踪。'], 'risks': ['后续情况仍可能发生变化。']},
                'summary': '依据冻结行情和实际取得的资料形成的测试结果。',
                'sections': [{'title': '依据', 'text': '本结果仅使用当前任务已取得的资料，不新增来源或数值。'}],
                'sourceKeys': ['invented'] if self.bad_source else keys, 'missingInformation': []}, {'inputTokens': 10}


def setup(context, kind, *, strategy=None, runtime=None, published=False):
    if not published:
        context.published()
    runtime = runtime or Runtime()
    context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    payload = {'kind': kind}
    if strategy:
        payload['strategy'] = strategy
    status, result, _ = context.request('POST', f'/api/v1/stocks/{stock}/analyses', payload, key='analysis-request')
    assert status == 202, result
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, runtime))
    return result['data']['id'], worker


def test_chip_report_contains_computed_distribution_and_preserved_history(context):
    job, worker = setup(context, 'chips')
    asyncio.run(worker.run_once('analysis'))
    status, response, _ = context.request('GET', f'/api/v1/analyses/{job}')
    assert status == 200, response
    result = response['data']
    assert result['status'] == 'succeeded' and result['stage'] == 'completed'
    report = result['report']
    assert abs(sum(item['ratio'] for item in report['chipDistribution']['bins']) - 100) < 1e-8
    assert report['sources'] and report['generatedAt'] >= report['materialsAsOfAt']
    assert 'report' not in context.request('GET', '/api/v1/analyses')[1]['data']['items'][0]


def test_invented_source_rejected_without_publishing_report(context):
    job, worker = setup(context, 'chips', runtime=Runtime(bad_source=True))
    asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', f'/api/v1/analyses/{job}')[1]['data']
    assert result['status'] == 'failed' and result['report'] is None
    assert result['error']['code'] == 'ANALYSIS_INVALID_REPORT'


def test_fundamental_unknown_reduction_forces_wait_with_real_financial_metrics(context):
    context.published()
    stock = context.source.universe_stocks[0].stock_id
    with context.api.db.transaction() as connection:
        for end, revenue, profit, published in [(date(2026, 9, 30), 120, 15, date(2026, 10, 29)), (date(2025, 12, 31), 150, 20, date(2026, 3, 30)), (date(2025, 9, 30), 100, 10, date(2025, 10, 29))]:
            from datetime import datetime, timezone
            connection.execute(t.financials.insert().values(stock_id=stock, period_start=date(end.year, 1, 1), period_end=end,
                statement_scope='consolidated', revenue_ytd=D(revenue), net_profit_parent_ytd=D(profit), extra_json={'currency': 'CNY'},
                source='测试财报', source_url='https://example.test/financial', published_at=datetime.combine(published, datetime.min.time(), timezone.utc),
                fetched_at=context.clock.now(), content_hash='a' * 64, raw_payload_json={'revenue': revenue, 'profit': profit}))
    job, worker = setup(context, 'fundamental', published=True)
    asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', f'/api/v1/analyses/{job}')[1]['data']
    assert result['status'] == 'succeeded', result
    report = result['report']
    assert report['dataQuality'] == 'partial'
    assert report['overview']['decision']['action'] == 'wait'
    assert '减持' in report['missingInformation'][0]
    assert len(report['metrics']) == 3
    assert any(source['url'] == 'https://example.test/financial' for source in report['sources'])


def test_missing_core_financials_fails_instead_of_inventing_data(context):
    job, worker = setup(context, 'fundamental')
    asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', f'/api/v1/analyses/{job}')[1]['data']
    assert result['status'] == 'failed' and result['error']['code'] == 'ANALYSIS_SOURCES_UNAVAILABLE'


def test_waiting_strategy_cannot_be_changed_into_immediate_buy(context):
    original = context.source.daily
    async def trend(stock, target, history):
        packet = await original(stock, target, history)
        records = list(packet.bars)
        for index in range(140, 160):
            records[index] = replace(records[index], open=D('12.6'), low=D('12.58'), high=D('12.62'), close=D('12.6'), amount=D(30_000_000) if index >= 155 else D(50_000_000))
        return replace(packet, bars=tuple(records))
    context.source.daily = trend
    job, worker = setup(context, 'trade', strategy='trend_wait')
    asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', f'/api/v1/analyses/{job}')[1]['data']
    assert result['status'] == 'succeeded', result
    plan = result['report']['overview']['pricePlan']
    assert plan['waiting'] is True
    assert result['report']['overview']['decision']['action'] == 'wait'
    assert plan['entryPrice'] == '12.80 元'


def test_public_reader_refuses_local_and_unapproved_urls_without_network(context):
    context.published()
    runtime = Runtime(); context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    job = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {'kind': 'chips'}, key='reader-boundary')[1]['data']['id']
    lease = context.api.tasks.claim('analysis', 'test-reader')
    assert lease.id == job
    tools = AnalysisTools(context.api.tasks, lease)
    for url in ('file:///etc/passwd', 'http://127.0.0.1/', 'https://www.sse.com.cn@127.0.0.1/', 'https://example.test/', 'https://www.sse.com.cn:bad/'):
        result = asyncio.run(tools.read_public(url))
        assert result['status'] == 'missing'


def test_reduction_proof_requires_original_text_and_separates_plan_from_actual(context):
    context.published()
    runtime = Runtime(); context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    context.request('POST', f'/api/v1/stocks/{stock}/analyses', {'kind': 'fundamental'}, key='reduction-proof')
    lease = context.api.tasks.claim('analysis', 'proof-reader')
    tools = AnalysisTools(context.api.tasks, lease)
    text = '证券代码600000，大股东甲公司拟减持计划不超过100万股。本次进展披露已减持20万股，计划尚未完成。'
    tools.save('announcement', 'research', '测试公告原文', {'text': text}, url='https://www.sse.com.cn/test')
    bad = {'sourceKey': 'announcement', 'shareholder': '甲公司', 'status': 'in_progress', 'evidence': text, 'actualText': '计划不超过100万股'}
    assert asyncio.run(tools.review_reductions([bad]))['status'] == 'missing'
    assert tools.reduction_verified is False
    good = {**bad, 'actualText': '已减持20万股', 'planText': '拟减持计划不超过100万股'}
    assert asyncio.run(tools.review_reductions([good]))['status'] == 'available'
    assert tools.reduction_verified is True
