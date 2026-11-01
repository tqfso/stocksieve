"""本文件验证分类范围保留、资料时间和历史快照冻结。"""

from datetime import timedelta
from typing import Any


def test_partial_concept_catalog_does_not_rewrite_old_snapshot(context: Any) -> None:
    old = context.published()
    original = context.source.catalog
    observed = (context.clock.now() - timedelta(minutes=25)).isoformat()
    async def partial(stocks: Any) -> dict[str, Any]:
        return {**await original(stocks), 'conceptCoverage': 'partial', 'observedAt': observed}
    context.source.catalog = partial
    context.submit('catalog'); context.run()
    context.submit('daily'); context.run()
    latest = context.request('GET', '/api/v1/catalog/filters')[1]['data']
    historical = context.request('GET', '/api/v1/catalog/filters?snapshotId=' + old)[1]['data']
    assert latest['coverage']['concepts'] == 'partial' and latest['concepts']
    assert historical['coverage']['concepts'] == 'complete'
    concept = latest['concepts'][0]['id']
    result = context.request('GET', '/api/v1/opportunities?strategy=short&conceptId=' + concept)[1]['data']
    assert result['snapshot']['catalogAsOfAt'] == observed
    assert result['total'] > 0 and all(any(item['id'] == concept for item in row['stock']['concepts']) for row in result['items'])


def test_industry_only_refresh_keeps_verified_concepts_and_their_source(context: Any) -> None:
    from sqlalchemy import select
    from stocksieve import tables as t
    original = context.source.catalog
    async def known(stocks: Any) -> dict[str, Any]:
        return {**await original(stocks), 'provenance': {'industry': 'fixture', 'concepts': 'fixture'}}
    context.source.catalog = known
    old = context.published()
    class IndustryOnly:
        id = 'industry_only'
        name = '行业测试源'
        supported_kinds = frozenset({'catalog'})
        boards = frozenset()
        async def catalog(self, stocks: Any) -> dict[str, Any]:
            return {'fields': ['industry'], 'provenance': {'industry': self.id}, 'stocks': {
                stock.stock_id: {'industry': {'id': 'industry_new', 'name': '新行业'}, 'concepts': None} for stock in stocks}}
    context.api.registry.register(IndustryOnly())
    status, _, _ = context.request('POST', '/api/v1/admin/sync-jobs', {'kind': 'catalog', 'source': 'industry_only'}, key='industry_preserve_concepts')
    assert status == 202
    context.run()
    context.submit('daily'); context.run()
    current = context.request('GET', '/api/v1/catalog/filters')[1]['data']
    previous = context.request('GET', '/api/v1/catalog/filters?snapshotId=' + old)[1]['data']
    assert current['concepts'] == previous['concepts'] and current['coverage']['concepts'] == 'complete'
    assert current['industries'][0]['name'] == '新行业' and previous['industries'][0]['name'] != '新行业'
    snapshot = context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']
    assert snapshot['dataProvenance'] == {'industry': 'industry_only', 'concepts': 'fixture'}
    with context.api.db.transaction() as connection:
        codes = connection.scalar(select(t.categories.c.source_codes).where(t.categories.c.id == current['concepts'][0]['id']))
    assert 'industry_only' not in codes
