"""本文件验证真实浏览器、HTTP和隔离数据库，以及正式部署的行情与模拟分析链路。"""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import HTTPServer
from stocksieve.interfaces.http.api import API
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.infrastructure.agent import create_runtime
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker
from sqlalchemy import delete
from stocksieve.infrastructure.persistence import schema as t

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from browser_check import run_access_check, run_check, run_concept_check, run_strategy_check, run_research_check, run_readiness_check


def test_browser_against_isolated_market_and_mock_analysis(context: Any, request: Any) -> None:
    if not request.config.getoption('--browser'):
        pytest.skip('浏览器验收需显式--browser。')
    if context.server is None:
        pytest.fail('--browser需配合--http。')
    context.published()
    context.source.pending = True
    waiting = context.submit('daily')
    context.run()
    assert context.api.tasks.detail(waiting, 'sync')['waitReason'] == 'BOARD_DATA_NOT_READY'
    web = Path(__file__).resolve().parents[2] / 'frontend/dist'
    if not (web / 'index.html').exists():
        pytest.fail('请先构建前端。')
    settings = replace(context.api.settings, web_dir=web)
    runtime = create_runtime(settings)
    api = API(context.api.db, settings, context.api.registry, runtime, context.clock.now)
    api.app.freeze()
    server = HTTPServer(api.app)
    worker = Worker(api.tasks, Sync(api.tasks, api.registry), Analysis(api.tasks, runtime))
    future = asyncio.run_coroutine_threadsafe(worker.serve(), server.loop)
    try:
        result = run_check(server.base_url, 'admin', 'testpass123', require_data=True)
        assert result['pageErrors'] == 0
        assert any('/candles' in path for path in result['apiPaths'])
        assert '/auth/login' in result['apiPaths'] and '/auth/logout' in result['apiPaths']
        assert run_concept_check(server.base_url, 'admin', 'testpass123')['matchedStocks'] > 0
        assert run_strategy_check(server.base_url, 'admin', 'testpass123')['filterResetChecked']
        with context.api.db.transaction() as connection:
            connection.execute(delete(t.signals).where(t.signals.c.stock_id == context.source.universe_stocks[1].stock_id))
        assert run_research_check(server.base_url, 'admin', 'testpass123')['unselectedStockAnalyzed']
        assert run_readiness_check(server.base_url, 'admin', 'testpass123')['waitingStateChecked']
        run_access_check(server.base_url, 'admin', 'testpass123')
    finally:
        future.cancel()
        async def settle() -> None:
            await asyncio.sleep(.1)
        asyncio.run_coroutine_threadsafe(settle(), server.loop).result(timeout=5)
        server.close()


def test_browser_against_deployed_database(request: Any) -> None:
    base = request.config.getoption('--deployed-base')
    file = request.config.getoption('--deployed-credentials')
    if not base or not file:
        pytest.skip('正式部署只读验收需显式指定地址和私有凭据文件。')
    credentials = json.loads(Path(file).read_text())
    result = run_check(base, credentials['username'], credentials['password'], require_data=request.config.getoption('--deployed-data'))
    assert result['pageErrors'] == 0


def test_browser_against_deployed_concepts(request: Any) -> None:
    if not request.config.getoption('--deployed-concepts'):
        pytest.skip('题材正式验收需显式--deployed-concepts。')
    base = request.config.getoption('--deployed-base')
    file = request.config.getoption('--deployed-credentials')
    if not base or not file:
        pytest.fail('请指定正式地址与私有凭据文件。')
    credentials = json.loads(Path(file).read_text())
    result = run_concept_check(base, credentials['username'], credentials['password'], screenshot=Path(__file__).resolve().parents[1] / '.state/concept-filter.png')
    assert result['snapshotId']
    print(json.dumps(result, ensure_ascii=False))


def test_browser_against_deployed_strategies(request: Any) -> None:
    if not request.config.getoption('--deployed-strategies'):
        pytest.skip('正式策略验收需显式--deployed-strategies。')
    base = request.config.getoption('--deployed-base')
    file = request.config.getoption('--deployed-credentials')
    if not base or not file:
        pytest.fail('请指定正式地址与私有凭据文件。')
    credentials = json.loads(Path(file).read_text())
    result = run_strategy_check(base, credentials['username'], credentials['password'])
    assert result['filterResetChecked']
    print(json.dumps(result, ensure_ascii=False))


def test_browser_against_deployed_research(request: Any) -> None:
    if not request.config.getoption('--deployed-research'):
        pytest.skip('正式个股模拟分析验收需显式--deployed-research，会保存模拟报告。')
    base = request.config.getoption('--deployed-base')
    file = request.config.getoption('--deployed-credentials')
    if not base or not file:
        pytest.fail('请指定正式地址与私有凭据文件。')
    credentials = json.loads(Path(file).read_text())
    result = run_research_check(base, credentials['username'], credentials['password'], screenshot=Path(__file__).resolve().parents[1] / '.state/stock-research-mobile.png')
    assert result['unselectedStockAnalyzed']
    print(json.dumps(result, ensure_ascii=False))


def test_browser_against_deployed_readiness(request: Any) -> None:
    if not request.config.getoption('--deployed-readiness'):
        pytest.skip('正式等待状态只读验收需--deployed-readiness。')
    base = request.config.getoption('--deployed-base')
    file = request.config.getoption('--deployed-credentials')
    if not base or not file:
        pytest.fail('请指定正式地址与私有凭据文件。')
    credentials = json.loads(Path(file).read_text())
    result = run_readiness_check(base, credentials['username'], credentials['password'])
    print(json.dumps(result, ensure_ascii=False))
