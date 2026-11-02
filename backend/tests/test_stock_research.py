"""本文件验证全目录搜索、未入选个股模拟分析、通用规则和报告权限，不访问外部模型或网页。"""

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from typing import Any

from sqlalchemy import delete

from stocksieve.infrastructure.persistence import schema as t
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.domain.trade import general_trade
from stocksieve.modules.analysis.infrastructure.agent import create_runtime
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker
from stocksieve.modules.market.domain.models import Bar, StockState


class ForbiddenNetwork:
    """模拟分析期间任一外部资料调用均使测试失败。"""

    async def search(self, *args: Any) -> Any:
        raise AssertionError('不允许联网检索')

    async def document(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError('不允许读取网页')


def run_analysis(context: Any) -> None:
    runtime = create_runtime(context.api.settings)
    context.api.runtime = runtime
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry),
        Analysis(context.api.tasks, runtime, ForbiddenNetwork(), ForbiddenNetwork()))
    assert asyncio.run(worker.run_once('analysis'))


def test_directory_search_is_independent_of_signals_and_has_pending_states(context: Any) -> None:
    context.submit('initialize'); context.run()
    first = context.source.universe_stocks[0]
    path = '/api/v1/stocks?search=' + first.code
    result = context.request('GET', path)[1]['data']
    assert result['total'] == 1 and result['items'][0]['stockId'] == first.stock_id
    assert result['items'][0]['dataStatus'] == 'pending'
    context.submit('catalog'); context.run()
    context.submit('daily'); context.run()
    with context.api.db.transaction() as connection:
        connection.execute(delete(t.signals).where(t.signals.c.stock_id == first.stock_id))
    result = context.request('GET', path)[1]['data']
    assert result['items'][0]['dataStatus'] == 'ready'
    for search in (first.name, first.code[:3], first.source_code or first.stock_id):
        assert context.request('GET', '/api/v1/stocks?search=' + search)[1]['data']['total'] > 0
    assert context.request('GET', '/api/v1/stocks?search=%25')[1]['data']['total'] == 0
    assert context.request('GET', '/api/v1/stocks?search=不存在的股票')[1]['data']['total'] == 0
    assert context.request('GET', path, token=None)[0] == 401
    assert context.request('GET', '/api/v1/stocks?search=' + 'a' * 51)[0] == 400
    assert context.request('GET', '/api/v1/stocks?pageSize=101')[0] == 400


def test_unselected_stock_supports_all_mock_types_with_general_history_isolation(context: Any) -> None:
    snapshot = context.published()
    stock = context.source.universe_stocks[0].stock_id
    with context.api.db.transaction() as connection:
        connection.execute(delete(t.signals).where(t.signals.c.stock_id == stock))
    context.api.runtime = create_runtime(context.api.settings)
    context.api.auth.create_user('reader', '普通读者', 'readerpass123')
    reader = context.api.auth.login('reader', 'readerpass123')['accessToken']
    path = f'/api/v1/stocks/{stock}/analyses'
    for kind in ('trade', 'fundamental', 'chips'):
        status, result, _ = context.request('POST', path, {'kind': kind, 'snapshotId': snapshot}, token=reader, key='general-' + kind)
        assert status == 202, result
        task = result['data']
        assert task['strategy'] is None and task['stage'] == 'preparing'
        if kind == 'trade':
            conflict = context.request('POST', path, {'kind': kind, 'snapshotId': snapshot}, token=reader, key='general-duplicate')[1]
            assert conflict['error']['code'] == 'ANALYSIS_IN_PROGRESS'
        run_analysis(context)
        report = context.request('GET', '/api/v1/analyses/' + task['id'], token=reader)[1]['data']
        assert report['status'] == 'succeeded', report
        assert report['simulated'] and report['report']['title'].startswith('【模拟】')
        if kind == 'trade':
            assert '～' in report['report']['overview']['pricePlan']['entryPrice']
            assert report['report']['overview']['decision']['action'] == 'wait'
        assert context.request('GET', '/api/v1/analyses/' + task['id'])[0] == 404
    assert context.request('POST', path, {'kind': 'trade', 'strategy': 'short'}, key='bad-specific', token=reader)[0] == 409
    general = context.request('GET', '/api/v1/analyses?kind=trade&strategy=general', token=reader)[1]['data']
    assert general['total'] == 1 and general['items'][0]['strategy'] is None
    assert context.request('GET', '/api/v1/analyses?kind=trade&strategy=short', token=reader)[1]['data']['total'] == 0


def test_missing_history_still_allows_limited_general_report_without_price(context: Any) -> None:
    snapshot = context.published()
    stock = context.source.universe_stocks[0].stock_id
    with context.api.db.transaction() as connection:
        connection.execute(delete(t.bars).where(t.bars.c.snapshot_id == snapshot, t.bars.c.stock_id == stock))
    context.api.runtime = create_runtime(context.api.settings)
    path = f'/api/v1/stocks/{stock}/analyses'
    status, accepted, _ = context.request('POST', path, {'kind': 'trade'}, key='no-history-general')
    assert status == 202
    run_analysis(context)
    result = context.request('GET', '/api/v1/analyses/' + accepted['data']['id'])[1]['data']
    assert result['status'] == 'succeeded'
    report = result['report']
    assert report['overview']['pricePlan']['entryPrice'] == '暂无可靠参考'
    assert report['overview']['decision']['action'] == 'wait'
    assert any('60个连续交易日' in item for item in report['missingInformation'])
    assert context.request('POST', path, {'kind': 'chips'}, key='no-history-chips')[0] == 409


def test_general_and_specific_tasks_can_coexist_and_have_separate_history(context: Any) -> None:
    context.published()
    context.api.runtime = create_runtime(context.api.settings)
    stock = context.source.universe_stocks[0].stock_id
    path = f'/api/v1/stocks/{stock}/analyses'
    for payload, key in (({'kind': 'trade'}, 'mode-general'), ({'kind': 'trade', 'strategy': 'short'}, 'mode-short')):
        assert context.request('POST', path, payload, key=key)[0] == 202
    run_analysis(context); run_analysis(context)
    for mode, expected in [('general', None), ('short', 'short')]:
        result = context.request('GET', '/api/v1/analyses?kind=trade&strategy=' + mode)[1]['data']
        assert result['total'] == 1 and result['items'][0]['strategy'] == expected


def test_general_rules_ignore_future_reject_gaps_and_do_not_offer_weak_trend_entry() -> None:
    target = date(2026, 10, 30)
    records = [Bar(target - timedelta(days=79 - index), D(10) + D(index) / 100, D(10) + D(index) / 100 + D('.02'),
        D(10) + D(index) / 100 - D('.02'), D(10) + D(index) / 100, D(50000000), factor=D(1)) for index in range(80)]
    days = [bar.day for bar in records]
    state = StockState('trading', False, False)
    result = general_trade(records, days, target, state)
    assert result['plan']['entryPrice'] != '暂无可靠参考' and result['action'] == 'wait'
    future = replace(records[-1], day=target + timedelta(days=1), factor=D(100))
    assert general_trade([*records, future], [*days, future.day], target, state) == result
    assert general_trade(records[:30] + records[31:], days, target, state)['missing']
    weak = [replace(bar, open=D(30) - bar.open, high=D(30) - bar.low, low=D(30) - bar.high, close=D(30) - bar.close) for bar in records]
    assert general_trade(weak, days, target, state)['action'] == 'avoid'
    assert general_trade(weak, days, target, state)['plan']['entryPrice'] == '暂无可靠参考'
    assert general_trade(records, days, target, StockState('suspended', False, False))['plan']['exitPrice'] == '暂无可靠参考'
