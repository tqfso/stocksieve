"""本文件验证无模型配置时的模拟分析接口、真实计算复用、历史标识及模式切换边界。"""

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import select

from stocksieve import tables as t
from stocksieve.agent import DeepAgentsRuntime, create_runtime
from stocksieve.analysis import Analysis
from stocksieve.config import Settings
from stocksieve.mock_analysis import MockAnalysisRuntime
from stocksieve.sync import Sync
from stocksieve.worker import Worker


class ForbiddenResearch:
    """模拟运行如果触发检索或网页请求，用例立即失败。"""

    async def search(self, query: str) -> Any:
        raise AssertionError('模拟模式不应联网检索。')

    async def document(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError('模拟模式不应读取网页。')


def test_runtime_defaults_to_mock_and_live_does_not_fall_back(monkeypatch: Any) -> None:
    settings = Settings(database_url='postgresql+psycopg://unused/unused')
    assert isinstance(create_runtime(settings), MockAnalysisRuntime)
    live = create_runtime(replace(settings, ai_mode='live'))
    assert isinstance(live, DeepAgentsRuntime) and live.identity() is None
    with pytest.raises(ValueError):
        replace(settings, ai_mode='invalid')
    monkeypatch.setenv('STOCKSIEVE_DATABASE_URL', settings.database_url)
    monkeypatch.setenv('STOCKSIEVE_AI_MODE', 'live')
    assert Settings.from_env().ai_mode == 'live'


@pytest.mark.parametrize('kind', ['fundamental', 'chips', 'trade'])
def test_mock_reports_complete_without_model_or_research_and_keep_computed_values(context: Any, kind: str) -> None:
    context.published()
    runtime = create_runtime(context.api.settings)
    assert isinstance(runtime, MockAnalysisRuntime)
    context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    payload = {'kind': kind, **({'strategy': 'short'} if kind == 'trade' else {})}
    path = f'/api/v1/stocks/{stock}/analyses'
    status, accepted, _ = context.request('POST', path, payload, key='mock-analysis-test')
    assert status == 202 and accepted['data']['simulated'] is True
    job = accepted['data']['id']
    forbidden = ForbiddenResearch()
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry),
                    Analysis(context.api.tasks, runtime, forbidden, forbidden))
    assert asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', f'/api/v1/analyses/{job}')[1]['data']
    report = result['report']
    assert result['status'] == 'succeeded' and result['simulated'] is True
    assert report['simulated'] is True and report['title'].startswith('【模拟】')
    assert '模拟' in report['summary'] and report['dataQuality'] == 'partial'
    assert report['missingInformation'] and all(source['url'] is None for source in report['sources'])
    if kind == 'fundamental':
        assert report['metrics'] == []
        assert report['overview']['decision']['action'] == 'wait'
        assert any('减持' in text for text in report['missingInformation'])
    elif kind == 'chips':
        distribution = report['chipDistribution']
        assert abs(sum(item['ratio'] for item in distribution['bins']) - 100) < 1e-8
    else:
        with context.api.db.transaction() as connection:
            row = connection.execute(select(t.signals).where(t.signals.c.snapshot_id == result['snapshotId'],
                t.signals.c.stock_id == stock, t.signals.c.strategy_code == 'short')).mappings().one()
        expected = f"{row['metrics_json']['breakoutReference']:.2f} 元"
        assert report['overview']['pricePlan']['entryPrice'] == expected
    with context.api.db.transaction() as connection:
        saved = connection.execute(select(t.results).where(t.results.c.task_id == job)).mappings().one()
        assert saved['provider'] == 'local-mock'
        assert saved['usage_json']['inputTokens'] == saved['usage_json']['outputTokens'] == 0
    # 切换服务配置后，历史标识与幂等重放仍沿用受理时的模拟模式。
    context.api.runtime = create_runtime(replace(context.api.settings, ai_mode='live'))
    history = context.request('GET', '/api/v1/analyses')[1]['data']['items']
    assert history[0]['simulated'] is True and 'report' not in history[0]
    assert context.request('POST', path, payload, key='mock-analysis-test')[1]['data'] == accepted['data']


def test_mock_mode_still_requires_a_published_input(context: Any) -> None:
    context.api.runtime = create_runtime(context.api.settings)
    status, response, _ = context.request('POST', '/api/v1/stocks/SSE.600000/analyses',
                                         {'kind': 'fundamental'}, key='mock-without-input')
    assert status == 409
    assert response['error']['code'] == 'DATA_NOT_READY'


def test_switching_mode_before_execution_rejects_instead_of_relabeling_task(context: Any) -> None:
    context.published()
    context.api.runtime = create_runtime(context.api.settings)
    stock = context.source.universe_stocks[0].stock_id
    job = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {'kind': 'chips'}, key='mock-mode-change')[1]['data']['id']
    live = create_runtime(replace(context.api.settings, ai_mode='live'))
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, live))
    assert asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', f'/api/v1/analyses/{job}')[1]['data']
    assert result['simulated'] is True and result['report'] is None
    assert result['status'] == 'failed' and result['error']['code'] == 'ANALYSIS_CONFIG_CHANGED'
