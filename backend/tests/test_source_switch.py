"""本文件验证切换渠道时仅停止排队任务，保留历史快照、日志与原渠道，不撤销活动租约。"""

import pytest
from stocksieve.operations.source_switch import retire_source_jobs


def test_retire_queued_source_preserves_published_data_and_original_job_source(context) -> None:
    snapshot = context.published()
    job = context.submit('daily')
    dry = retire_source_jobs(context.api.db, 'fixture', 'sina')
    assert dry['taskIds'] == [job] and not dry['applied']
    assert context.api.tasks.detail(job, 'sync')['status'] == 'queued'
    retire_source_jobs(context.api.db, 'fixture', 'sina', apply=True)
    task = context.api.tasks.detail(job, 'sync')
    assert task['status'] == 'failed' and task['source'] == 'fixture'
    assert task['error']['code'] == 'SOURCE_CHANGED'
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']['id'] == snapshot
    assert retire_source_jobs(context.api.db, 'fixture', 'sina', apply=True)['taskIds'] == []


def test_retire_running_job_refuses_without_invalidating_lease(context) -> None:
    context.submit('initialize'); context.run()
    job = context.submit('daily')
    lease = context.api.tasks.claim('sync', 'worker')
    with pytest.raises(RuntimeError, match='运行中'):
        retire_source_jobs(context.api.db, 'fixture', 'sina', apply=True)
    assert context.api.tasks.detail(job, 'sync')['status'] == 'running'
    context.api.tasks.heartbeat(lease)
