"""本文件通过HTTP验证全目录搜索、未入选个股报告及资料不足时的展示边界。"""

from typing import Any

from sqlalchemy import delete
from stocksieve.infrastructure.persistence import schema as t

from .analysis_fixtures import generate


def test_directory_search_is_independent_of_signals(context: Any) -> None:
    context.submit('initialize')
    context.run()
    stock = context.source.universe_stocks[0]
    path = '/api/v1/stocks?search=' + stock.code
    assert context.request('GET', path)[1]['data']['items'][0]['dataStatus'] == 'pending'
    context.submit('daily')
    context.run()
    with context.api.db.transaction() as connection:
        connection.execute(delete(t.signals).where(t.signals.c.stock_id == stock.stock_id))
    for search in (stock.name, stock.code, stock.stock_id):
        result = context.request('GET', '/api/v1/stocks?search=' + search)[1]['data']
        assert result['total'] == 1 and result['items'][0]['dataStatus'] == 'ready'
    task = generate(context)
    assert task['stockId'] == stock.stock_id and len(task['report']['sections']) == 6
    assert context.request('GET', '/api/v1/stocks?search=不存在的股票')[1]['data']['total'] == 0
    assert context.request('GET', path, token=None)[0] == 401


def test_missing_history_keeps_a_limited_report_and_no_chip_graph(context: Any) -> None:
    snapshot = context.published()
    stock = context.source.universe_stocks[0].stock_id
    with context.api.db.transaction() as connection:
        connection.execute(delete(t.bars).where(t.bars.c.snapshot_id == snapshot, t.bars.c.stock_id == stock))
    chips = context.request('GET', f'/api/v1/stocks/{stock}/chips')[1]['data']
    assert chips['distribution'] is None and chips['missingInformation']
    report = generate(context)['report']
    assert report['overview']['pricePlan']['entryPrice'] == '暂无可靠参考'
    assert report['dataQuality'] == 'partial' and len(report['sections']) == 6
    assert next(item['value'] for item in report['metrics'] if item['label'] == '技术情景盈亏比') == '暂无可靠参考'


def test_report_input_and_list_reject_removed_types_and_strategy_modes(context: Any) -> None:
    context.published()
    path = f'/api/v1/stocks/{context.source.universe_stocks[0].stock_id}/analyses'
    for body in ({'kind': 'trade'}, {'strategy': 'short'}, {'scenario': 'normal'}):
        assert context.request('POST', path, body, key='removed-mode-test')[0] == 400
    for query in ('kind=chips', 'strategy=general'):
        assert context.request('GET', '/api/v1/analyses?' + query)[0] == 400
