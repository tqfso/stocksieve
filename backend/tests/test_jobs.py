"""本文件验证任务租约、分批恢复、预检等待超时、日志游标和定时休市跳过。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from stocksieve import tables as t
from stocksieve.errors import DomainError
from stocksieve.sources import SourceDeferred
from stocksieve.worker import Scheduler


def test_expired_lease_reclaimed_old_owner_cannot_write(context):
    job = context.submit('initialize')
    old = context.api.tasks.claim('sync', 'old-worker')
    assert old.id == job
    context.clock.advance(121)
    new = context.api.tasks.claim('sync', 'new-worker')
    assert new.id == old.id and new.attempt == old.attempt + 1
    with pytest.raises(DomainError) as error:
        context.api.tasks.patch(old, {'progress': 100})
    assert error.value.code == 'TASK_LEASE_LOST'
    context.api.tasks.heartbeat(new)
    assert context.api.tasks.detail(job, 'sync')['status'] == 'running'


def test_repeated_process_interruptions_have_a_recovery_limit(context):
    job = context.submit('initialize')
    assert context.api.tasks.claim('sync', 'worker-0')
    for index in range(1, 4):
        context.clock.advance(121)
        assert context.api.tasks.claim('sync', f'worker-{index}')
    context.clock.advance(121)
    assert context.api.tasks.claim('sync', 'worker-4') is None
    assert context.api.tasks.detail(job, 'sync')['error']['code'] == 'TASK_RECOVERY_LIMIT'


def test_frequency_wait_resumes_same_snapshot_and_stock_cursor(context):
    context.submit('initialize'); context.run()
    original = context.source.daily
    calls = []
    paused = False
    async def delayed(stock, target, history):
        nonlocal paused
        calls.append(stock.stock_id)
        if len(calls) == 3 and not paused:
            paused = True
            raise SourceDeferred(context.clock.now() + timedelta(seconds=10), '等待')
        return await original(stock, target, history)
    context.source.daily = delayed
    job = context.submit('daily'); context.run()
    with context.api.db.transaction() as connection:
        task = connection.execute(select(t.tasks).where(t.tasks.c.id == job)).mappings().one()
        assert task['status'] == 'queued' and task['options_json']['nextIndex'] == 2
        building = task['options_json']['buildingSnapshotId']
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot'] is None
    context.clock.advance(11); context.run()
    result = context.api.tasks.detail(job, 'sync')
    assert result['status'] == 'succeeded' and result['result']['publishedSnapshotId'] == building
    assert calls.count(context.source.universe_stocks[0].stock_id) == 1
    assert calls.count(context.source.universe_stocks[1].stock_id) == 1


def test_readiness_timeout_does_not_publish_or_move_target_date(context):
    first = context.published()
    context.source.pending = True
    job = context.submit('daily'); context.run()
    assert context.api.tasks.detail(job, 'sync')['retryCount'] == 0
    context.clock.advance(4 * 3600 + 1); context.run()
    failed = context.api.tasks.detail(job, 'sync')
    assert failed['status'] == 'failed' and failed['error']['code'] == 'DATA_READY_TIMEOUT'
    assert failed['dataDate'] == '2026-10-30' and failed['retryCount'] == 1
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']['id'] == first


def test_closed_day_skips_without_querying_vendor(context):
    context.submit('initialize'); context.run()
    context.clock.advance(86400)
    async def unexpected(target):
        raise AssertionError('确认休市不应调用供应商预检')
    context.source.precheck = unexpected
    job = Scheduler(context.api.tasks).tick()
    assert job
    context.run()
    result = context.api.tasks.detail(job, 'sync')
    assert result['status'] == 'succeeded' and result['result']['outcome'] == 'market_closed'
    assert result['result']['processedCount'] == 0 and result['result']['publishedSnapshotId'] is None
    context.token = context.api.auth.login('admin', 'testpass123')['accessToken']
    assert context.request('POST', '/api/v1/admin/sync-jobs', {'kind': 'daily', 'source': 'fixture'}, key='closed-manual')[0] == 400


def test_log_cursor_scoped_to_job_and_sync_trigger_filter(context):
    first = context.submit('initialize'); context.run()
    second = context.submit('catalog'); context.run()
    status, page, _ = context.request('GET', f'/api/v1/admin/sync-jobs/{first}/logs?limit=1')
    assert status == 200 and page['data']['items']
    cursor = page['data']['nextCursor']
    assert context.request('GET', f'/api/v1/admin/sync-jobs/{second}/logs?after={cursor}')[0] == 400
    assert context.request('GET', '/api/v1/admin/sync-jobs?trigger=scheduled')[1]['data']['items'] == []
    assert context.request('GET', '/api/v1/admin/sync-jobs?trigger=invalid')[0] == 400
