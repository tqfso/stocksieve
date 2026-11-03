"""本文件验证人工重试沿用原任务、快照与股票检查点，保持幂等、权限和配置冻结。"""

from dataclasses import replace
from typing import Any

import stocksieve.infrastructure.persistence.schema as t
from sqlalchemy import func, select, update


def failed_after_two(context: Any) -> tuple[str, list[str], dict[str, bool]]:
    """先保存两只股票再触发一次内部异常，模拟真实的部分成功后失败。"""
    context.submit('initialize'); context.run()
    original = context.source.daily
    calls: list[str] = []
    state = {'broken': True}

    async def daily(stock: Any, target: Any, history: Any) -> Any:
        calls.append(stock.stock_id)
        if stock == context.source.universe_stocks[2] and state['broken']:
            raise ValueError('测试异常')
        return await original(stock, target, history)

    context.source.daily = daily
    job = context.submit('daily'); context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'failed'
    return job, calls, state


def test_retry_resumes_same_snapshot_without_recalculating_completed_stocks(context: Any) -> None:
    job, calls, state = failed_after_two(context)
    with context.api.db.transaction() as connection:
        original = dict(context.api.tasks.find(connection, job, 'sync'))
        snapshot_id = original['options_json']['buildingSnapshotId']
        assert connection.scalar(select(t.snapshots.c.status).where(t.snapshots.c.id == snapshot_id)) == 'failed'
        before_bars = connection.scalar(select(func.count()).select_from(t.bars).where(t.bars.c.snapshot_id == snapshot_id))
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=250, history_max_trading_days=500)
    state['broken'] = False
    calls.clear()
    path = f'/api/v1/admin/sync-jobs/{job}/retry'
    status, first, headers = context.request('POST', path, {}, key='resume-same-job')
    assert status == 202, first
    assert first['data']['id'] == job and first['data']['stockProgress'] == {'processedCount': 2, 'totalCount': 5}
    assert first['data']['error'] is None and first['data']['finishedAt'] is None
    assert headers['Location'].endswith(job)
    assert context.request('POST', path, {}, key='resume-same-job')[1]['data'] == first['data']
    with context.api.db.transaction() as connection:
        resumed = context.api.tasks.find(connection, job, 'sync')
        for key in ('historyDays', 'historyMaxDays', 'marketBoards', 'stockIds', 'nextIndex', 'buildingSnapshotId'):
            assert resumed['options_json'][key] == original['options_json'][key]
        assert connection.scalar(select(func.count()).select_from(t.bars).where(t.bars.c.snapshot_id == snapshot_id)) == before_bars
        assert connection.scalar(select(t.snapshots.c.status).where(t.snapshots.c.id == snapshot_id)) == 'building'
    context.run()
    done = context.api.tasks.detail(job, 'sync')
    assert done['status'] == 'succeeded' and done['result']['publishedSnapshotId'] == snapshot_id
    assert calls == [stock.stock_id for stock in context.source.universe_stocks[2:]]
    logs = context.api.tasks.log_page(job, {'limit': '200'})['items']
    assert any('继续原任务' in item['message'] for item in logs)
    assert any(item['level'] == 'error' for item in logs)


def test_retry_rejects_active_conflict_and_preserves_failed_checkpoint(context: Any) -> None:
    job, _, state = failed_after_two(context)
    state['broken'] = False
    active = context.submit('daily')
    status, error, _ = context.request('POST', f'/api/v1/admin/sync-jobs/{job}/retry', {}, key='retry-conflict')
    assert status == 409 and error['error']['details']['jobId'] == active
    assert context.api.tasks.detail(job, 'sync')['status'] == 'failed'


def test_retry_only_accepts_failed_tasks_and_empty_input(context: Any) -> None:
    job = context.submit('initialize')
    path = f'/api/v1/admin/sync-jobs/{job}/retry'
    assert context.request('POST', path, {}, key='queued-retry')[0] == 409
    context.run()
    assert context.request('POST', path, {}, key='success-retry')[0] == 409
    failed, _, _ = failed_after_two(context)
    path = f'/api/v1/admin/sync-jobs/{failed}/retry'
    assert context.request('POST', path, {'source': 'other'}, key='replace-source')[0] == 400
    assert context.request('POST', path, {'dataDate': '2026-10-29'}, key='replace-date')[0] == 400
    assert context.request('POST', path, {})[0] == 400
    context.api.auth.create_user('viewer', '读者', 'testpass123')
    token = context.api.auth.login('viewer', 'testpass123')['accessToken']
    assert context.request('POST', path, {}, token=token, key='forbidden-retry')[0] == 403


def test_invalid_checkpoint_never_restarts_from_zero(context: Any) -> None:
    published = context.published()
    job, _, _ = failed_after_two(context)
    with context.api.db.transaction() as connection:
        task = context.api.tasks.find(connection, job, 'sync')
        connection.execute(update(t.tasks).where(t.tasks.c.id == job).values(options_json={**task['options_json'], 'buildingSnapshotId': published}))
    status, body, _ = context.request('POST', f'/api/v1/admin/sync-jobs/{job}/retry', {}, key='invalid-checkpoint')
    assert status == 409 and body['error']['code'] == 'SYNC_CHECKPOINT_INVALID'
    assert context.api.tasks.detail(job, 'sync')['status'] == 'failed'
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']['id'] == published


def test_initialization_retry_retains_job_without_a_snapshot(context: Any) -> None:
    original = context.source.initialize

    async def broken() -> Any:
        raise ValueError('测试初始化异常')

    context.source.initialize = broken
    job = context.submit('initialize'); context.run()
    context.source.initialize = original
    status, body, _ = context.request('POST', f'/api/v1/admin/sync-jobs/{job}/retry', {}, key='initialize-resume')
    assert status == 202 and body['data']['id'] == job
    context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'succeeded'
