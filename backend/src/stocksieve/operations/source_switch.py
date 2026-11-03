"""本文件在切换默认渠道后停止旧渠道排队任务，保留原始渠道、检查点、日志与已发布数据。"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

import stocksieve.infrastructure.persistence.schema as t
from stocksieve.infrastructure.persistence.database import Database


def retire_source_jobs(database: Database, source: str, replacement: str, *, apply: bool = False) -> dict[str, Any]:
    """仅处理没有租约的排队收盘任务；遇运行中任务整批拒绝，避免撤销进行中的写入。"""
    if source == replacement:
        raise RuntimeError('旧渠道与默认渠道相同，未修改任务。')
    with database.transaction(serialized=True) as connection:
        rows = connection.execute(select(t.tasks).where(t.tasks.c.category == 'sync', t.tasks.c.kind == 'daily',
            t.tasks.c.data_source == source, t.tasks.c.status.in_(['queued', 'running'])).with_for_update()).mappings().all()
        if any(row['status'] != 'queued' or row['lease_owner'] for row in rows):
            raise RuntimeError('旧渠道仍有运行中的任务，请等待其释放租约后再切换。')
        message = f'默认行情渠道已切换为{replacement}，停止旧{source}任务；原检查点和已发布数据保留。'
        if apply:
            now = datetime.now(UTC)
            for row in rows:
                options = row['options_json']
                if options.get('buildingSnapshotId'):
                    connection.execute(update(t.snapshots).where(t.snapshots.c.id == options['buildingSnapshotId'],
                        t.snapshots.c.status == 'building').values(status='failed'))
                connection.execute(update(t.tasks).where(t.tasks.c.id == row['id']).values(status='failed',
                    finished_at=now, error_code='SOURCE_CHANGED', error_message=message, lease_owner=None, lease_expires_at=None))
                connection.execute(t.logs.insert().values(task_id=row['id'], time=now, level='info', message=message))
        return {'source': source, 'replacement': replacement, 'applied': apply, 'taskIds': [row['id'] for row in rows]}
