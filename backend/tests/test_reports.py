"""本文件通过HTTP验证报告、独立筹码、输入冻结和导出，受控模型仅在测试目录提供。"""

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
from stocksieve.modules.analysis.infrastructure.agent import LangGraphRuntime
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker
from stocksieve.shared.settings import Settings

from .analysis_fixtures import FixtureAnalysisRuntime, generate


def test_report_has_seven_answers_actual_metrics_and_no_invented_research(context: Any) -> None:
    snapshot = context.published()
    task = generate(context)
    report = task['report']
    assert task['kind'] == 'report' and task['snapshotId'] == snapshot
    assert not report['simulated'] and not report['title'].startswith('【模拟】')
    assert [section['id'] for section in report['sections']] == ['company', 'advantages', 'risks', 'holding', 'entry', 'sell']
    assert 'chipDistribution' not in report
    assert report['shareholders']['latestCount'] == 88254 and report['shareholders']['trend'] == 'unknown'
    assert report['sections'][0]['title'] == '公司做什么'
    metrics = {item['label']: item['value'] for item in report['metrics']}
    assert all(metrics[key] != '待核验' for key in ['收盘价', '5日平均价', '10日平均价', '20日平均价', '60日平均价', '筹码平均成本'])
    assert report['valuation']['industryPe'] is None and report['valuation']['companyPe'] is None
    assert report['researchStatus'] == 'researched' and report['dataQuality'] == 'partial'
    assert any(source['url'] == 'https://www.sse.com.cn/fixture/disclosure' for source in report['sources'])
    assert report['overview']['decision']['action'] == 'wait'
    assert '暂无' not in report['overview']['pricePlan']['entryPrice']
    with context.api.db.transaction() as connection:
        saved = connection.execute(select(t.results).where(t.results.c.task_id == task['id'])).mappings().one()
        assert saved['schema_version'] == 4 and saved['provider'] == 'fixture'
        assert saved['usage_json']['inputTokens'] == saved['usage_json']['outputTokens'] == 10


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
        assert '【模拟】' not in xml and task['report']['title'] in xml
        for section in task['report']['sections']:
            assert section['title'] in xml
        assert '本期股东户数' in xml
        assert '行业估值资料' in xml and task['dataDate'] in xml
    status, pdf, headers = context.request('GET', path + '?format=pdf')
    assert status == 200 and pdf.startswith(b'%PDF') and headers['Content-Type'] == 'application/pdf'
    reader = PdfReader(BytesIO(pdf))
    content = ''.join(page.extract_text() for page in reader.pages)
    assert '本期股东户数' in content
    assert len(reader.pages) >= 2 and '【模拟】' not in content and '行业估值资料' in content
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


def test_model_config_change_fails_a_queued_report(context: Any) -> None:
    context.published()
    context.api.runtime = FixtureAnalysisRuntime('fixture-before')
    stock = context.source.universe_stocks[0].stock_id
    task = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key='config-change-test')[1]['data']
    changed = FixtureAnalysisRuntime('fixture-after')
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, changed))
    assert asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', '/api/v1/analyses/' + task['id'])[1]['data']
    assert not result['simulated'] and result['status'] == 'failed' and result['report'] is None
    assert result['error']['code'] == 'ANALYSIS_CONFIG_CHANGED'
    assert context.request('GET', '/api/v1/analyses/' + task['id'] + '/export?format=pdf')[0] == 409


def test_analysis_requires_published_input_and_model_configuration(context: Any) -> None:
    context.api.runtime = LangGraphRuntime(Settings(database_url='postgresql+psycopg://unused/unused'))
    assert context.api.runtime.identity() is None
    path = '/api/v1/stocks/SSE.600000/analyses'
    assert context.request('POST', path, {}, key='without-snapshot')[0] == 409
    context.published()
    assert context.request('POST', path, {}, key='without-model-config')[0] == 503


def test_historical_report_retains_its_original_marker(context: Any) -> None:
    context.published()
    task = generate(context)
    # 只在隔离schema准备旧版本记录，验证历史数据不被重新标为真实模型报告。
    legacy = {**task['report'], 'title': '【模拟】历史报告', 'simulated': True, 'researchStatus': 'not_searched'}
    legacy.pop('generatedAt', None)
    with context.api.db.transaction() as connection:
        connection.execute(t.results.update().where(t.results.c.task_id == task['id']).values(payload_json=legacy))
        connection.execute(t.tasks.update().where(t.tasks.c.id == task['id']).values(
            options_json={'stage': 'completed', 'runtime': {'mode': 'mock', 'provider': 'local-mock'}}))
    detail = context.request('GET', '/api/v1/analyses/' + task['id'])[1]['data']
    assert detail['simulated'] and detail['report']['simulated']
    listed = context.request('GET', '/api/v1/analyses')[1]['data']['items']
    assert next(item for item in listed if item['id'] == task['id'])['simulated']
    word = context.request('GET', '/api/v1/analyses/' + task['id'] + '/export?format=docx')[1]
    with ZipFile(BytesIO(word)) as archive:
        assert '【模拟】历史报告' in archive.read('word/document.xml').decode()


@pytest.mark.parametrize(('price', 'expected'), [('10', '2.00 : 1'), ('12', '暂无可靠参考'), ('9', '暂无可靠参考')])
def test_technical_risk_reward_is_based_on_current_price_and_valid_reference_range(context: Any, price: str, expected: str) -> None:
    from decimal import Decimal as D

    original = context.source.daily

    async def daily(*args: Any) -> Any:
        packet = await original(*args)
        bars = list(packet.bars)
        # 前20日技术区间固定9至12元；现价10元时，上行2元、下行1元。
        bars[-21:-1] = [replace(bar, open=D(10), high=D(12), low=D(9), close=D(10)) for bar in bars[-21:-1]]
        current = D(price)
        bars[-1] = replace(bars[-1], open=current, high=current + D('.01'), low=current - D('.01'), close=current)
        return replace(packet, bars=tuple(bars))

    context.source.daily = daily
    context.published()
    task = generate(context)
    metric = next(item for item in task['report']['metrics'] if item['label'] == '技术情景盈亏比')
    assert metric['value'] == expected
    assert task['report']['overview']['decision']['action'] == 'wait'
    path = '/api/v1/analyses/' + task['id'] + '/export'
    word = context.request('GET', path + '?format=docx')[1]
    with ZipFile(BytesIO(word)) as archive:
        content = archive.read('word/document.xml').decode()
        assert '技术情景盈亏比' in content and expected in content
    pdf = PdfReader(BytesIO(context.request('GET', path + '?format=pdf')[1]))
    content = ''.join(page.extract_text() for page in pdf.pages)
    assert '技术情景盈亏比' in content and expected in content
