"""本文件通过HTTP验证统一模拟报告、独立筹码、输入冻结及Word/PDF导出。"""

import asyncio
from dataclasses import replace
from io import BytesIO
from typing import Any
from zipfile import ZipFile

import pytest
from pypdf import PdfReader
from sqlalchemy import func, select
from stocksieve.infrastructure.persistence import schema as t
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.infrastructure.agent import create_runtime
from stocksieve.modules.analysis.infrastructure.mock_runtime import MockAnalysisRuntime
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker
from stocksieve.shared.settings import Settings


class ForbiddenResearch:
    """模拟模式发生任何联网调用时立即失败。"""

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError('模拟模式不允许联网')

    async def document(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError('模拟模式不允许联网')


def generate(context: Any, *, runtime: Any = None, key: str = 'unified-report-test') -> dict[str, Any]:
    runtime = runtime or create_runtime(context.api.settings)
    context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    status, body, _ = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key=key)
    assert status == 202, body
    forbidden = ForbiddenResearch()
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, runtime, forbidden, forbidden))
    assert asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', '/api/v1/analyses/' + body['data']['id'])[1]['data']
    assert result['status'] == 'succeeded', result
    return result


def test_mock_report_has_seven_answers_actual_metrics_and_no_invented_research(context: Any) -> None:
    snapshot = context.published()
    task = generate(context)
    report = task['report']
    assert task['kind'] == 'report' and task['snapshotId'] == snapshot
    assert report['simulated'] and report['title'].startswith('【模拟】')
    assert [section['id'] for section in report['sections']] == ['company', 'advantages', 'risks', 'holding', 'buy', 'avoid', 'sell']
    assert 'chipDistribution' not in report
    assert report['shareholders']['latestCount'] is None and report['shareholders']['trend'] == 'unknown'
    assert '股东户数' in report['sections'][0]['text'] and report['sections'][0]['title'] == '公司基本资料'
    metrics = {item['label']: item['value'] for item in report['metrics']}
    assert all(metrics[key] != '待核验' for key in ['收盘价', 'MA5', 'MA10', 'MA20', 'MA60', '筹码平均成本'])
    assert report['valuation']['industryPe'] is None and report['valuation']['companyPe'] is None
    assert report['researchStatus'] == 'not_searched' and report['dataQuality'] == 'partial'
    assert all(source['url'] is None for source in report['sources'])
    assert report['overview']['decision']['action'] == 'wait'
    assert '暂无' not in report['overview']['pricePlan']['entryPrice']
    with context.api.db.transaction() as connection:
        saved = connection.execute(select(t.results).where(t.results.c.task_id == task['id'])).mappings().one()
        assert saved['schema_version'] == 3 and saved['provider'] == 'local-mock'
        assert saved['usage_json']['inputTokens'] == saved['usage_json']['outputTokens'] == 0


def test_chips_are_available_without_an_analysis_task(context: Any) -> None:
    snapshot = context.published()
    with context.api.db.transaction() as connection:
        before = connection.scalar(select(func.count()).select_from(t.tasks))
    stock = context.source.universe_stocks[0].stock_id
    status, body, _ = context.request('GET', f'/api/v1/stocks/{stock}/chips?snapshotId={snapshot}')
    assert status == 200 and body['data']['snapshotId'] == snapshot
    distribution = body['data']['distribution']
    assert abs(sum(row['ratio'] for row in distribution['bins']) - 100) < 1e-8
    with context.api.db.transaction() as connection:
        assert connection.scalar(select(func.count()).select_from(t.tasks)) == before
    assert context.request('GET', f'/api/v1/stocks/{stock}/chips', token=None)[0] == 401


def test_exports_are_valid_documents_keep_saved_content_and_do_not_reanalyse(context: Any) -> None:
    context.published()
    task = generate(context)
    path = '/api/v1/analyses/' + task['id'] + '/export'
    with context.api.db.transaction() as connection:
        count = connection.scalar(select(func.count()).select_from(t.tasks))
    status, word, headers = context.request('GET', path + '?format=docx')
    assert status == 200 and isinstance(word, bytes)
    assert headers['Content-Type'].startswith('application/vnd.openxmlformats')
    assert "filename*=UTF-8''" in headers['Content-Disposition']
    with ZipFile(BytesIO(word)) as archive:
        xml = archive.read('word/document.xml').decode()
        assert '【模拟】' in xml and task['report']['title'] in xml
        for section in task['report']['sections']:
            assert section['title'] in xml
        assert '股东户数与筹码集中观察' in xml
        assert '行业 PE（TTM）' in xml and task['dataDate'] in xml
    status, pdf, headers = context.request('GET', path + '?format=pdf')
    assert status == 200 and pdf.startswith(b'%PDF') and headers['Content-Type'] == 'application/pdf'
    reader = PdfReader(BytesIO(pdf))
    content = ''.join(page.extract_text() for page in reader.pages)
    assert '股东户数与筹码集中观察' in content
    assert len(reader.pages) >= 2 and '模拟' in content and '行业 PE（TTM）' in content
    for section in task['report']['sections']:
        assert section['title'] in content
    with context.api.db.transaction() as connection:
        assert connection.scalar(select(func.count()).select_from(t.tasks)) == count
    assert context.request('GET', path + '?format=html')[0] == 400
    assert context.request('GET', path + '?format=pdf', token=None)[0] == 401
    context.request('POST', '/api/v1/admin/users', {'username': 'reader', 'name': '读者', 'password': 'readerpass123'})
    token = context.request('POST', '/api/v1/auth/login', {'username': 'reader', 'password': 'readerpass123'}, token=None)[1]['data']['accessToken']
    assert context.request('GET', path + '?format=pdf', token=token)[0] == 404
    assert context.request('GET', '/api/v1/analyses/' + task['id'])[1]['data']['report'] == task['report']


def test_mode_switch_does_not_relabel_a_queued_report(context: Any) -> None:
    context.published()
    context.api.runtime = create_runtime(context.api.settings)
    stock = context.source.universe_stocks[0].stock_id
    task = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key='mode-change-test')[1]['data']
    live = create_runtime(replace(context.api.settings, ai_mode='live'))
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, live))
    assert asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', '/api/v1/analyses/' + task['id'])[1]['data']
    assert result['simulated'] and result['status'] == 'failed' and result['report'] is None
    assert context.request('GET', '/api/v1/analyses/' + task['id'] + '/export?format=pdf')[0] == 409


def test_mock_mode_requires_a_published_input(context: Any) -> None:
    context.api.runtime = create_runtime(context.api.settings)
    assert context.request('POST', '/api/v1/stocks/SSE.600000/analyses', {}, key='without-snapshot')[0] == 409
    assert isinstance(create_runtime(Settings(database_url='postgresql+psycopg://unused/unused')), MockAnalysisRuntime)
    assert create_runtime(replace(context.api.settings, ai_mode='live')).identity() is None
    with pytest.raises(ValueError):
        replace(context.api.settings, ai_mode='invalid')
