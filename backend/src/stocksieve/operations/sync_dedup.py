"""本文件修复旧版本已受理的重复收盘任务，复用成功结果并保留访问账本和所有数据。"""

from typing import Any

from sqlalchemy import select, update

import stocksieve.infrastructure.persistence.schema as t
from stocksieve.modules.jobs.application.service import Tasks


def reconcile_duplicates(tasks: Tasks, *, apply: bool = False) -> dict[str, Any]:
    """仅跳过完全匹配已有结果的普通任务；分类、配置或日期变化时保持原任务。"""
    result = []
    with tasks.db.transaction(serialized=True) as connection:
        rows = connection.execute(select(t.tasks).where(t.tasks.c.category == 'sync', t.tasks.c.kind == 'daily',
            t.tasks.c.status.in_(['queued', 'running'])).with_for_update()).mappings().all()
        for row in rows:
            options = row['options_json']
            if options.get('force') or options.get('historyBackfill'):
                continue
            completed = tasks.completed_sync(connection, row['data_source'], row['target_date'], tasks.boards(row),
                options.get('historyDays', 150), options.get('historyMaxDays', 0))
            if not completed:
                continue
            snapshot_id = completed['result_snapshot_id']
            message = f'{row["target_date"]}已有同渠道、同配置的成功结果，已复用快照并跳过重复更新。'
            result.append({'jobId': row['id'], 'reusedJobId': completed['id'], 'snapshotId': snapshot_id})
            if apply:
                if building := options.get('buildingSnapshotId'):
                    connection.execute(update(t.snapshots).where(t.snapshots.c.id == building, t.snapshots.c.status == 'building').values(status='failed'))
                result_json = {'outcome': 'already_synced', 'totalCount': 0, 'processedCount': 0, 'successCount': 0,
                    'skippedCount': 0, 'failedCount': 0, 'publishedSnapshotId': snapshot_id}
                connection.execute(update(t.tasks).where(t.tasks.c.id == row['id']).values(status='succeeded', progress=100,
                    lease_owner=None, lease_expires_at=None, wait_reason=None, finished_at=tasks.clock(),
                    result_snapshot_id=snapshot_id, result_json=result_json, options_json={**options, 'sourceProgressMessage': message}))
                connection.execute(t.logs.insert().values(task_id=row['id'], time=tasks.clock(), level='info', message=message))
    return {'applied': apply, 'duplicates': result}
