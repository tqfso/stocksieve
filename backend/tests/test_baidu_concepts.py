"""本文件验证百度题材的明确类别、稳定编码、批量上限、缓存时间及跨任务恢复。"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.application.progress import progress_sink
from stocksieve.modules.ingestion.infrastructure.providers.baidu import (
    BaiduConcepts,
    parse_concepts,
)
from stocksieve.modules.ingestion.infrastructure.providers.baostock import security
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.shared.errors import DomainError


def groups(concepts: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    result = [{'name': '行业', 'list': [{'name': '银行', 'xcx_query': 'code=IND1'}]},
              {'name': '地域', 'list': [{'name': '广东', 'xcx_query': 'code=AREA1'}]}]
    if concepts is not None:
        result.append({'name': '概念', 'list': concepts})
    return result


def test_concepts_are_explicit_and_unknown_is_different_from_empty() -> None:
    data = {'ResultCode': '0', 'Result': {'000001': groups([{'name': '测试概念', 'xcx_query': 'code=003631&name=test'}]),
                                         '600000': groups([]), '688981': groups(None)}}
    parsed = parse_concepts(data, {'000001', '600000', '688981', '300750'})
    assert parsed['000001'] == [{'id': 'baidu_003631', 'name': '测试概念', 'sourceCode': '003631'}]
    assert parsed['600000'] == [] and parsed['688981'] is None and parsed['300750'] is None
    with pytest.raises(DomainError):
        parse_concepts(data, {'000001'})
    with pytest.raises(DomainError):
        parse_concepts({'ResultCode': 1, 'Result': data['Result']}, set(data['Result']))
    with pytest.raises(DomainError):
        parse_concepts({'ResultCode': 0, 'Result': {'000001': groups([{'name': '测试概念', 'xcx_query': 'name=no-code'}])}}, {'000001'})


def test_batches_resume_and_expand_without_repeating_known_stocks(tmp_path: Path, monkeypatch: Any) -> None:
    import stocksieve.modules.ingestion.infrastructure.providers.baidu as module
    calls: list[list[str]] = []
    captured = datetime.now(timezone.utc) - timedelta(minutes=25)
    class HTTP:
        def __init__(self, *args: Any) -> None:
            pass
        async def document(self, vendor: str, url: str) -> tuple[bytes, datetime]:
            selected = [row['code'] for row in json.loads(parse_qs(urlsplit(url).query)['stock'][0])]
            assert len(selected) <= 20
            calls.append(selected)
            response = {'ResultCode': 0, 'Result': {code: groups([{'name': '测试主题', 'xcx_query': 'code=GN2047'}]) for code in selected}}
            return json.dumps(response).encode(), captured
        async def close(self) -> None:
            pass
    monkeypatch.setattr(module, 'SourceHTTP', HTTP)
    stocks = tuple(security(f'sh.{600000 + index}', '测试股票') for index in range(21))
    provider = BaiduConcepts(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache', batch_limit=1)
    with progress_sink(lambda *args: None, operation_id='first-job'):
        first = asyncio.run(provider.read(stocks))
        assert asyncio.run(provider.read(stocks)) == first
    assert first['conceptCoverage'] == 'partial' and first['conceptCollection']['knownStocks'] == 20
    assert len(calls) == 1 and first['observedAt'] == captured.isoformat()
    with progress_sink(lambda *args: None, operation_id='second-job'):
        complete = asyncio.run(provider.read(stocks))
    assert complete['conceptCoverage'] == 'complete' and calls[-1] == ['600020']
    assert all(row['concepts'][0]['id'] == 'baidu_GN2047' for row in complete['stocks'].values())
    assert complete['fields'] == ['concepts']


def test_frequency_wait_keeps_completed_batch_and_same_operation_budget(tmp_path: Path, monkeypatch: Any) -> None:
    import stocksieve.modules.ingestion.infrastructure.providers.baidu as module
    deferred = False
    class HTTP:
        def __init__(self, *args: Any) -> None:
            pass
        async def document(self, vendor: str, url: str) -> tuple[bytes, datetime]:
            nonlocal deferred
            codes = [row['code'] for row in json.loads(parse_qs(urlsplit(url).query)['stock'][0])]
            if codes == ['600020'] and not deferred:
                deferred = True
                raise SourceDeferred(datetime.now(timezone.utc) + timedelta(seconds=10), '测试间隔')
            return json.dumps({'ResultCode': 0, 'Result': {code: groups([{'name': '测试主题', 'xcx_query': 'code=GN2047'}]) for code in codes}}).encode(), datetime.now(timezone.utc)
        async def close(self) -> None:
            pass
    monkeypatch.setattr(module, 'SourceHTTP', HTTP)
    stocks = tuple(security(f'sh.{600000 + index}', '测试股票') for index in range(21))
    provider = BaiduConcepts(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache', batch_limit=2)
    with progress_sink(lambda *args: None, operation_id='same-job'):
        with pytest.raises(SourceDeferred):
            asyncio.run(provider.read(stocks))
        saved = json.loads(provider.path.read_text())
        assert saved['batches']['same-job']['used'] == 1 and len(saved['records']) == 20
        result = asyncio.run(provider.read(stocks))
    assert result['conceptCollection']['knownStocks'] == 21
    assert json.loads(provider.path.read_text())['batches']['same-job']['used'] == 2


def test_concept_only_source_keeps_previous_industry_and_snapshot(context: Any) -> None:
    from stocksieve.modules.ingestion.infrastructure.providers.baidu import (
        BaiduCatalogSource,
    )
    old = context.published()
    class Provider:
        async def read(self, stocks: Any) -> dict[str, Any]:
            return {'fields': ['concepts'], 'provenance': {'concepts': 'baidu'}, 'conceptCoverage': 'complete',
                    'stocks': {stock.stock_id: {'concepts': [{'id': 'baidu_GN2047', 'name': '测试新题材'}]} for stock in stocks}}
    context.api.registry.register(BaiduCatalogSource(Provider()))
    status, _, _ = context.request('POST', '/api/v1/admin/sync-jobs', {'kind': 'catalog', 'source': 'baidu_concepts'}, key='concept_preserve_industry')
    assert status == 202
    context.run()
    context.submit('daily'); context.run()
    latest = context.request('GET', '/api/v1/catalog/filters')[1]['data']
    previous = context.request('GET', '/api/v1/catalog/filters?snapshotId=' + old)[1]['data']
    assert latest['industries'] == previous['industries']
    assert latest['concepts'] == [{'id': 'baidu_GN2047', 'name': '测试新题材'}]
    assert previous['concepts'] != latest['concepts']
