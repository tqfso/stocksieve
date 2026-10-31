"""本文件运行后台执行器和北京时间18点调度，持久化任务跨重启恢复。"""

import asyncio
from datetime import datetime, time
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select, update

from . import tables as t
from .errors import DomainError
from .database import utc
from .sources import SourceDeferred
from .sync import Sync
from .tasks import Lease, SHANGHAI, Tasks


class AnalysisRunner(Protocol):
    async def run(self, lease: Lease) -> None: ...


class Worker:
    """同步与分析各自占用一个执行名额，等待外部调用时定期续租。"""

    def __init__(self, tasks: Tasks, sync: Sync, analysis: AnalysisRunner | None = None) -> None:
        self.tasks, self.sync, self.analysis = tasks, sync, analysis
        self.owner = uuid4().hex

    def fail_if_owned(self, lease: Lease, error: DomainError) -> None:
        try:
            self.tasks.fail(lease, error)
        except DomainError as lost:
            if lost.code != 'TASK_LEASE_LOST':
                raise

    async def run_once(self, category: str) -> bool:
        lease = self.tasks.claim(category, self.owner)
        if lease is None:
            return False
        operation: asyncio.Task[None] | None = None
        try:
            if category == 'analysis' and self.analysis is None:
                raise DomainError('ANALYSIS_UNAVAILABLE', '分析执行服务未就绪。', 503)
            operation = asyncio.create_task(self.sync.run(lease) if category == 'sync' else self.analysis.run(lease))  # type: ignore[union-attr]
            while not operation.done():
                completed, _ = await asyncio.wait({operation}, timeout=max(.1, self.tasks.settings.lease_seconds / 3))
                if not completed:
                    self.tasks.heartbeat(lease)
            await operation
        except SourceDeferred as deferred:
            if category == 'sync':
                with self.tasks.db.transaction() as connection:
                    task = self.tasks.assert_lease(connection, lease)
                    deadline = utc(task['retry_deadline_at']) if task['retry_deadline_at'] and task['options_json'].get('readinessRetryPending') else None
                if deadline and deadline <= self.tasks.clock():
                    self.fail_if_owned(lease, DomainError('DATA_READY_TIMEOUT', '等待收盘数据已达到截止时间。', 503))
                else:
                    retry = min(deferred.until, deadline) if deadline else deferred.until
                    self.tasks.patch(lease, {'status': 'queued', 'lease_owner': None, 'lease_expires_at': None, 'run_after': retry},
                                     f'遵循渠道频控，下次可执行时间：{retry.isoformat()}。')
            else:
                self.fail_if_owned(lease, DomainError('ANALYSIS_SOURCES_UNAVAILABLE', '资料渠道暂处于频控等待，请稍后重新发起分析。', 503))
        except DomainError as error:
            if error.code != 'TASK_LEASE_LOST':
                self.fail_if_owned(lease, error)
        except asyncio.CancelledError:
            if operation:
                operation.cancel()
            raise
        except Exception:
            self.fail_if_owned(lease, DomainError('TASK_EXECUTION_FAILED', '后台任务执行失败，请查看任务记录后重试。', 503))
        finally:
            if operation and not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        return True

    async def serve(self) -> None:
        async def queue(category: str) -> None:
            while True:
                if not await self.run_once(category):
                    await asyncio.sleep(1)
        await asyncio.gather(queue('sync'), queue('analysis'))


class Scheduler:
    """每日仅登记一次计划，受阻时保留触发记录，后续继续关联任务。"""

    def __init__(self, tasks: Tasks) -> None:
        self.tasks = tasks

    def tick(self) -> str | None:
        local = self.tasks.clock().astimezone(SHANGHAI)
        if local.time() < time(18):
            return None
        day = local.date()
        planned = datetime.combine(day, time(18), SHANGHAI)
        with self.tasks.db.transaction(serialized=True) as connection:
            row = connection.execute(select(t.triggers).where(t.triggers.c.scheduled_date == day)).mappings().first()
            if row and row['task_id']:
                return row['task_id']
            if not row:
                connection.execute(t.triggers.insert().values(scheduled_date=day, scheduled_for=planned, created_at=self.tasks.clock()))
            existing = connection.execute(select(t.tasks).where(t.tasks.c.category == 'sync', t.tasks.c.kind == 'daily', t.tasks.c.target_date == day,
                t.tasks.c.status.in_(['queued', 'running'])).order_by(t.tasks.c.created_at).limit(1)).mappings().first()
            if existing:
                task_id = existing['id']
            else:
                try:
                    self.tasks.registry.get(self.tasks.settings.default_source, 'daily')
                    if not self.tasks.initialized(connection):
                        raise DomainError('DATA_NOT_INITIALIZED', '股票基础信息尚未初始化。', 409)
                except DomainError as error:
                    connection.execute(update(t.triggers).where(t.triggers.c.scheduled_date == day).values(last_error=error.code))
                    return None
                created = self.tasks.new(connection, category='sync', kind='daily', user_id=None, target=day,
                    source=self.tasks.settings.default_source, scheduled_for=planned,
                    options={'retryMinutes': self.tasks.settings.retry_minutes, 'readinessHours': self.tasks.settings.readiness_hours})
                task_id = created['id']
            connection.execute(update(t.triggers).where(t.triggers.c.scheduled_date == day).values(task_id=task_id, linked_at=self.tasks.clock(), last_error=None))
            return task_id

    async def serve(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(30)
