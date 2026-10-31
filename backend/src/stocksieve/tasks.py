"""本文件实现任务受理、请求幂等、租约与恢复；外部调用不持有数据库事务。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Connection

from . import tables as t
from .config import Settings
from .database import Database, utc
from .errors import DomainError
from .http import encode
from .market import Market
from .sources import Registry
from .validation import AnalysisInput, SyncInput, integer, invalid, resource_id

SHANGHAI = ZoneInfo('Asia/Shanghai')


@dataclass(frozen=True)
class Lease:
    id: str
    owner: str
    attempt: int


class Tasks:
    """受理、去重和幂等记录原子提交；租约到期后旧执行者不能再写入结果。"""

    def __init__(self, database: Database, settings: Settings, registry: Registry, market: Market,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.db, self.settings, self.registry, self.market = database, settings, registry, market
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def view(self, connection: Connection, row: Any, *, include_report: bool = True) -> dict[str, Any]:
        def stamp(value: datetime | None) -> str | None:
            return utc(value).isoformat() if value is not None else None
        result: dict[str, Any] = {'id': row['id'], 'kind': row['kind'], 'dataDate': row['target_date'].isoformat() if row['target_date'] else None,
                                 'status': row['status'], 'progress': row['progress'], 'createdAt': stamp(row['created_at']),
                                 'startedAt': stamp(row['started_at']), 'finishedAt': stamp(row['finished_at']),
                                 'error': {'code': row['error_code'], 'message': row['error_message']} if row['error_code'] else None}
        if row['category'] == 'sync':
            stored = row['result_json']
            public_result = {key: stored.get(key) for key in ('outcome', 'totalCount', 'processedCount', 'successCount', 'skippedCount', 'failedCount', 'publishedSnapshotId')} if stored else None
            result.update(source=row['data_source'], trigger=row['trigger'], scheduledFor=stamp(row['scheduled_for']),
                          waitReason=row['wait_reason'], nextRetryAt=stamp(row['run_after']) if row['wait_reason'] else None,
                          retryDeadlineAt=stamp(row['retry_deadline_at']), retryCount=row['retry_count'], precheck=row['precheck_json'], result=public_result)
        else:
            result.update(stockId=row['stock_id'], strategy=row['strategy_code'], snapshotId=row['input_snapshot_id'], stage=(row['options_json'] or {}).get('stage', 'queued'))
            if include_report:
                report = connection.execute(select(t.results).where(t.results.c.task_id == row['id'])).mappings().first()
                result['report'] = {**report['payload_json'], 'generatedAt': stamp(report['generated_at'])} if report else None
        return result

    def find(self, connection: Connection, task_id: str, category: str, user_id: str | None = None) -> Any:
        code = 'ANALYSIS_NOT_FOUND' if category == 'analysis' else 'SYNC_JOB_NOT_FOUND'
        predicates = [t.tasks.c.id == resource_id(task_id, code), t.tasks.c.category == category]
        if user_id is not None:
            predicates.append(t.tasks.c.created_by == user_id)
        row = connection.execute(select(t.tasks).where(*predicates)).mappings().first()
        if not row:
            raise DomainError(code, '任务不存在。', 404)
        return row

    def detail(self, task_id: str, category: str, user_id: str | None = None) -> dict[str, Any]:
        with self.db.transaction() as connection:
            return self.view(connection, self.find(connection, task_id, category, user_id))

    def accept(self, user_id: str, path: str, key: str | None, payload: dict[str, Any],
               create: Callable[[Connection], Any]) -> tuple[dict[str, Any], dict[str, str]]:
        if not key or not 8 <= len(key) <= 128:
            raise invalid('Idempotency-Key', '需提供8～128位的幂等提交标识。')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        predicates = (t.idempotency.c.user_id == user_id, t.idempotency.c.http_method == 'POST',
                      t.idempotency.c.request_path == path, t.idempotency.c.idempotency_key == key)
        with self.db.transaction(serialized=True) as connection:
            previous = connection.execute(select(t.idempotency).where(*predicates)).mappings().first()
            if previous and utc(previous['expires_at']) > self.clock():
                if previous['request_hash'] != digest:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '相同提交标识对应的请求内容不同。', 409)
                return previous['response_data'], previous['response_headers']
            if previous:
                connection.execute(delete(t.idempotency).where(*predicates))
            row = create(connection)
            data = json.loads(encode(self.view(connection, row)))
            location = f"/api/v1/{'analyses' if row['category'] == 'analysis' else 'admin/sync-jobs'}/{row['id']}"
            headers = {'Location': location}
            connection.execute(t.idempotency.insert().values(user_id=user_id, http_method='POST', request_path=path,
                idempotency_key=key, request_hash=digest, task_id=row['id'], response_status=202,
                response_data=data, response_headers=headers, created_at=self.clock(), expires_at=self.clock() + timedelta(hours=24)))
            return data, headers

    def new(self, connection: Connection, *, category: str, kind: str, user_id: str | None,
            target: date | None, source: str | None = None, stock_id: str | None = None,
            snapshot_id: str | None = None, strategy: str | None = None, options: dict[str, Any] | None = None,
            scheduled_for: datetime | None = None) -> Any:
        dedupe = f'sync:{kind}:{target}' if category == 'sync' else f'analysis:{user_id}:{stock_id}:{snapshot_id}:{kind}:{strategy}'
        task_id = t.identifier()
        connection.execute(t.tasks.insert().values(id=task_id, category=category, kind=kind, created_by=user_id,
            trigger='scheduled' if scheduled_for else 'manual', data_source=source, target_date=target,
            stock_id=stock_id, input_snapshot_id=snapshot_id, strategy_code=strategy, status='queued', progress=0,
            dedupe_key=dedupe, scheduled_for=scheduled_for, run_after=self.clock(), retry_count=0,
            options_json=options or {}, attempt_no=0, created_at=self.clock()))
        return connection.execute(select(t.tasks).where(t.tasks.c.id == task_id)).mappings().one()

    def initialized(self, connection: Connection) -> bool:
        return connection.execute(select(t.tasks.c.id).where(t.tasks.c.category == 'sync', t.tasks.c.kind == 'initialize', t.tasks.c.status == 'succeeded').limit(1)).first() is not None

    def submit_sync(self, user: dict[str, Any], path: str, key: str | None, data: SyncInput) -> tuple[dict[str, Any], dict[str, str]]:
        def create(connection: Connection) -> Any:
            self.registry.get(data.source, data.kind)
            if data.kind != 'daily' and 'dataDate' in data.model_fields_set:
                raise invalid('dataDate', '该任务不接受数据日期。')
            if data.kind != 'initialize' and not self.initialized(connection):
                raise DomainError('DATA_NOT_INITIALIZED', '请先初始化股票基础信息。', 409)
            active = connection.execute(select(t.tasks.c.id).where(t.tasks.c.category == 'sync', t.tasks.c.status.in_(['queued', 'running']))).first()
            if active:
                raise DomainError('SYNC_IN_PROGRESS', '已有未完成的数据任务。', 409, {'jobId': active.id})
            target = None
            if data.kind == 'daily':
                target = data.dataDate or self.clock().astimezone(SHANGHAI).date()
                if target > self.clock().astimezone(SHANGHAI).date():
                    raise DomainError('MARKET_NOT_CLOSED', '不能同步未来日期。', 409)
                rows = connection.execute(select(t.calendar).where(t.calendar.c.trade_date == target)).mappings().all()
                if {row['exchange'] for row in rows} != {'SSE', 'SZSE', 'BSE'} or any(row['is_open'] is None for row in rows):
                    raise DomainError('SERVICE_UNAVAILABLE', '目标日期的交易日历尚未确认。', 503)
                if not any(row['is_open'] for row in rows):
                    raise DomainError('NOT_TRADING_DAY', '目标日期为确认休市日。')
                if any(row['is_open'] and (row['close_at'] is None or utc(row['close_at']) > self.clock()) for row in rows):
                    raise DomainError('MARKET_NOT_CLOSED', '目标交易日尚未确认收盘。', 409)
            return self.new(connection, category='sync', kind=data.kind, user_id=user['id'], target=target, source=data.source,
                            options={'retryMinutes': self.settings.retry_minutes, 'readinessHours': self.settings.readiness_hours})
        return self.accept(user['id'], path, key, data.model_dump(mode='json', exclude_unset=True), create)

    def submit_analysis(self, user: dict[str, Any], stock_id: str, path: str, key: str | None,
                        data: AnalysisInput, runtime_identity: dict[str, Any] | Callable[[], dict[str, Any] | None] | None) -> tuple[dict[str, Any], dict[str, str]]:
        def create(connection: Connection) -> Any:
            if data.kind == 'trade' and data.strategy is None:
                raise invalid('strategy', '买卖建议需选择策略。')
            if data.kind != 'trade' and 'strategy' in data.model_fields_set:
                raise invalid('strategy', '该分析类型不接受策略。')
            snapshot = self.market.snapshot(connection, data.snapshotId)
            record = next((row for row in self.market.records(connection, snapshot) if row['stock']['stockId'] == stock_id), None)
            if not record:
                raise DomainError('STOCK_NOT_FOUND', '股票不在所选快照内。', 404)
            if data.kind == 'trade' and data.strategy not in record['signalRows']:
                raise DomainError('ANALYSIS_DATA_UNAVAILABLE', '所选快照未入选该策略。', 409)
            history = self.market.history(connection, snapshot, stock_id)
            if not history or record['quote'] is None or (data.kind == 'chips' and len(history) < 60):
                raise DomainError('ANALYSIS_DATA_UNAVAILABLE', '该类分析所需行情不足。', 409)
            active = connection.execute(select(t.tasks.c.id).where(t.tasks.c.category == 'analysis', t.tasks.c.created_by == user['id'],
                t.tasks.c.stock_id == stock_id, t.tasks.c.input_snapshot_id == snapshot['id'], t.tasks.c.kind == data.kind,
                t.tasks.c.strategy_code == data.strategy, t.tasks.c.status.in_(['queued', 'running']))).first()
            if active:
                raise DomainError('ANALYSIS_IN_PROGRESS', '已有相同输入的分析进行中。', 409, {'analysisId': active.id})
            identity = runtime_identity() if callable(runtime_identity) else runtime_identity
            if identity is None:
                raise DomainError('SERVICE_UNAVAILABLE', '分析服务暂不可用，请稍后重试或联系管理员。', 503)
            return self.new(connection, category='analysis', kind=data.kind, user_id=user['id'], target=snapshot['data_date'],
                            stock_id=stock_id, snapshot_id=snapshot['id'], strategy=data.strategy,
                            options={'stage': 'queued', 'runtime': identity})
        return self.accept(user['id'], path, key, data.model_dump(mode='json', exclude_unset=True), create)

    def list(self, category: str, query: dict[str, str], user_id: str | None = None) -> dict[str, Any]:
        page, size = integer(query, 'page', 1), integer(query, 'pageSize', 20, maximum=100)
        predicates = [t.tasks.c.category == category]
        if user_id:
            predicates.append(t.tasks.c.created_by == user_id)
        allowed_kinds = {'initialize', 'catalog', 'daily'} if category == 'sync' else {'fundamental', 'chips', 'trade'}
        if 'kind' in query:
            if query['kind'] not in allowed_kinds:
                raise invalid('kind')
            predicates.append(t.tasks.c.kind == query['kind'])
        if 'status' in query:
            if query['status'] not in {'queued', 'running', 'succeeded', 'failed'}:
                raise invalid('status')
            predicates.append(t.tasks.c.status == query['status'])
        if 'source' in query:
            predicates.append(t.tasks.c.data_source == query['source'])
        if 'trigger' in query:
            if query['trigger'] not in {'manual', 'scheduled'}:
                raise invalid('trigger')
            predicates.append(t.tasks.c.trigger == query['trigger'])
        if 'stockId' in query:
            predicates.append(t.tasks.c.stock_id == query['stockId'])
        if 'strategy' in query:
            if query.get('kind') != 'trade' or query['strategy'] not in {'short', 'swing', 'long', 'trend_wait', 'base_wait'}:
                raise invalid('strategy')
            predicates.append(t.tasks.c.strategy_code == query['strategy'])
        if 'snapshotId' in query:
            predicates.append(t.tasks.c.input_snapshot_id == resource_id(query['snapshotId'], 'SNAPSHOT_NOT_FOUND'))
        with self.db.transaction() as connection:
            if 'snapshotId' in query:
                self.market.snapshot(connection, query['snapshotId'])
            total = connection.scalar(select(func.count()).select_from(t.tasks).where(*predicates))
            rows = connection.execute(select(t.tasks).where(*predicates).order_by(t.tasks.c.created_at.desc(), t.tasks.c.id.desc()).offset((page - 1) * size).limit(size)).mappings()
            return {'items': [self.view(connection, row, include_report=False) for row in rows], 'total': total, 'page': page, 'pageSize': size}

    def log_page(self, task_id: str, query: dict[str, str]) -> dict[str, Any]:
        limit = integer(query, 'limit', 100, maximum=200)
        after = query.get('after')
        with self.db.transaction() as connection:
            row = self.find(connection, task_id, 'sync')
            predicates = [t.logs.c.task_id == row['id']]
            if after:
                if len(after) > 19 or not after.isascii() or not after.isdigit() or int(after) > 9223372036854775807:
                    raise DomainError('INVALID_CURSOR', '日志游标无效。')
                cursor = connection.execute(select(t.logs.c.id).where(t.logs.c.task_id == row['id'], t.logs.c.id == int(after))).first()
                if not cursor:
                    raise DomainError('INVALID_CURSOR', '日志游标不属于该任务。')
                predicates.append(t.logs.c.id > int(after))
            rows = connection.execute(select(t.logs).where(*predicates).order_by(t.logs.c.id).limit(limit + 1)).mappings().all()
            items = [{'id': str(item['id']), 'time': utc(item['time']).isoformat(), 'level': item['level'], 'message': item['message']} for item in rows[:limit]]
            return {'items': items, 'hasMore': len(rows) > limit, 'nextCursor': items[-1]['id'] if items else after}

    def claim(self, category: str, owner: str) -> Lease | None:
        now = self.clock()
        with self.db.transaction(serialized=True) as connection:
            # 1. 租约过期只恢复可领取状态，保留阶段和已落库的批次。
            expired = connection.execute(select(t.tasks).where(t.tasks.c.category == category, t.tasks.c.status == 'running', t.tasks.c.lease_expires_at <= now)).mappings().all()
            for stale in expired:
                options = {**stale['options_json'], 'recoveryCount': stale['options_json'].get('recoveryCount', 0) + 1}
                if options['recoveryCount'] > 3:
                    connection.execute(update(t.tasks).where(t.tasks.c.id == stale['id']).values(status='failed', lease_owner=None, lease_expires_at=None,
                        finished_at=now, options_json=options, error_code='TASK_RECOVERY_LIMIT', error_message='后台任务重复中断，已停止自动恢复，请检查后重新发起。'))
                    if options.get('buildingSnapshotId'):
                        connection.execute(update(t.snapshots).where(t.snapshots.c.id == options['buildingSnapshotId'], t.snapshots.c.status == 'building').values(status='failed'))
                else:
                    connection.execute(update(t.tasks).where(t.tasks.c.id == stale['id']).values(status='queued', lease_owner=None, lease_expires_at=None, run_after=now, options_json=options))
            if connection.execute(select(t.tasks.c.id).where(t.tasks.c.category == category, t.tasks.c.status == 'running')).first():
                return None
            row = connection.execute(select(t.tasks).where(t.tasks.c.category == category, t.tasks.c.status == 'queued', t.tasks.c.run_after <= now)
                                     .order_by(t.tasks.c.created_at, t.tasks.c.id).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not row:
                return None
            attempt = row['attempt_no'] + 1
            connection.execute(update(t.tasks).where(t.tasks.c.id == row['id']).values(status='running', lease_owner=owner,
                lease_expires_at=now + timedelta(seconds=self.settings.lease_seconds), attempt_no=attempt, started_at=row['started_at'] or now))
            return Lease(row['id'], owner, attempt)

    def assert_lease(self, connection: Connection, lease: Lease) -> Any:
        row = connection.execute(select(t.tasks).where(t.tasks.c.id == lease.id).with_for_update()).mappings().one()
        if row['status'] != 'running' or row['lease_owner'] != lease.owner or row['attempt_no'] != lease.attempt or utc(row['lease_expires_at']) <= self.clock():
            raise DomainError('TASK_LEASE_LOST', '任务执行租约已失效。', 409)
        return row

    def heartbeat(self, lease: Lease) -> None:
        with self.db.transaction(serialized=True) as connection:
            self.assert_lease(connection, lease)
            connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(lease_expires_at=self.clock() + timedelta(seconds=self.settings.lease_seconds)))

    def patch(self, lease: Lease, values: dict[str, Any], message: str | None = None) -> None:
        with self.db.transaction(serialized=True) as connection:
            self.assert_lease(connection, lease)
            connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(**values))
            if message:
                connection.execute(t.logs.insert().values(task_id=lease.id, time=self.clock(), level='info', message=message))

    def fail(self, lease: Lease, error: DomainError) -> None:
        with self.db.transaction(serialized=True) as connection:
            row = self.assert_lease(connection, lease)
            snapshot_id = row['options_json'].get('buildingSnapshotId')
            if snapshot_id:
                connection.execute(update(t.snapshots).where(t.snapshots.c.id == snapshot_id, t.snapshots.c.status == 'building').values(status='failed'))
            connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(status='failed', lease_owner=None, lease_expires_at=None,
                               error_code=error.code, error_message=error.message, finished_at=self.clock()))
            connection.execute(t.logs.insert().values(task_id=lease.id, time=self.clock(), level='error', message=error.message))
