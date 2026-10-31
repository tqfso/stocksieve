"""本文件验证同步发布、冻结分类、排序与筛选计数、失败保留及历史图表分页。"""

from stocksieve import tables as t
from stocksieve.worker import Scheduler
from sqlalchemy import select


def test_snapshot_query_filter_and_pagination(context):
    snapshot = context.published()
    status, response, _ = context.request('GET', '/api/v1/opportunities?strategy=short&pageSize=2')
    assert status == 200, response
    result = response['data']
    assert result['snapshot']['id'] == snapshot and result['total'] == 5
    assert result['summary']['strategyCounts']['short'] == 5
    assert len(result['items']) == 2
    assert result['items'][0]['signal']['metrics']['amountMultiple'] >= result['items'][1]['signal']['metrics']['amountMultiple']
    assert context.request('GET', '/api/v1/opportunities?strategy=short&minMarketCap=10000000000')[1]['data']['total'] == 4
    assert context.request('GET', '/api/v1/opportunities?strategy=short&minMarketCap=10000000000&maxMarketCap=10000000000')[1]['data']['total'] == 1
    assert context.request('GET', '/api/v1/opportunities?strategy=short&minTurnover=3&maxTurnover=1')[0] == 400
    assert context.request('GET', '/api/v1/opportunities?strategy=short&minMarketCap=NaN')[0] == 400
    assert context.request('GET', '/api/v1/opportunities?strategy=short&sort=default&order=asc')[0] == 400
    empty = context.request('GET', '/api/v1/opportunities?strategy=short&search=没有此股票')[1]['data']
    assert empty['items'] == [] and empty['total'] == 0
    ordered = context.request('GET', '/api/v1/opportunities?strategy=short&sort=totalMarketCap&order=asc')[1]['data']['items']
    assert ordered[-1]['quote']['totalMarketCap'] is None


def test_classification_refresh_does_not_rewrite_snapshot(context):
    first = context.published()
    old = context.request('GET', f'/api/v1/catalog/filters?snapshotId={first}')[1]['data']
    context.source.catalog_name = '新行业名称'
    context.submit('catalog'); context.run()
    assert context.request('GET', f'/api/v1/catalog/filters?snapshotId={first}')[1]['data'] == old
    context.submit('daily'); context.run()
    latest = context.request('GET', '/api/v1/catalog/filters')[1]['data']
    assert latest['snapshotId'] != first and latest['industries'][0]['name'] == '新行业名称'
    assert context.source.catalog_calls == 2


def test_wait_failure_and_backfill_preserve_latest(context):
    first = context.published()
    context.source.pending = True
    job = context.submit('daily'); context.run()
    waiting = context.api.tasks.detail(job, 'sync')
    assert waiting['status'] == 'queued' and waiting['waitReason'] == 'BOARD_DATA_NOT_READY'
    assert waiting['error'] is None and waiting['finishedAt'] is None
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']['id'] == first
    context.clock.advance(601); context.source.pending = False; context.source.bad = True
    context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'failed'
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']['id'] == first
    context.source.bad = False
    historical = context.submit('daily', day='2026-10-29'); context.run()
    assert context.api.tasks.detail(historical, 'sync')['status'] == 'succeeded'
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']['id'] == first


def test_candle_averages_use_history_outside_page(context):
    snapshot = context.published()
    stock = context.source.universe_stocks[0].stock_id
    status, first, _ = context.request('GET', f'/api/v1/stocks/{stock}/candles?snapshotId={snapshot}&limit=2')
    assert status == 200, first
    data = first['data']
    assert len(data['items']) == 2 and data['hasMore']
    assert data['items'][-1]['ma120'] is not None
    next_page = context.request('GET', f"/api/v1/stocks/{stock}/candles?snapshotId={snapshot}&limit=2&before={data['nextBefore']}")[1]['data']
    assert next_page['items'][-1]['time'] < data['items'][0]['time']
    assert context.request('GET', f'/api/v1/stocks/{stock}/candles?period=month')[0] == 400


def test_scheduler_records_once_and_links_existing_manual_job(context):
    context.submit('initialize'); context.run()
    manual = context.submit('daily')
    scheduler = Scheduler(context.api.tasks)
    assert scheduler.tick() == scheduler.tick() == manual
    with context.api.db.transaction() as connection:
        assert len(connection.execute(select(t.triggers)).all()) == 1
