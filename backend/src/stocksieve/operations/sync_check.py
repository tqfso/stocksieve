"""本文件只读检查同步检查点并复现下一只股票的取数，保留任务和业务数据库不变。"""

import traceback
from typing import Any

from sqlalchemy import func, select

import stocksieve.infrastructure.persistence.schema as t
from stocksieve.infrastructure.persistence.database import utc
from stocksieve.modules.ingestion.application.ports import (
    CalendarCacheSource,
    CalendarDay,
    HistorySource,
    SourceDeferred,
    SourceSession,
)
from stocksieve.modules.ingestion.application.registry import Registry
from stocksieve.modules.ingestion.application.service import stock_value
from stocksieve.modules.jobs.application.service import Tasks
from stocksieve.modules.market.domain.history import HistoryPolicy
from stocksieve.shared.errors import DomainError, MissingData


async def inspect_sync(tasks: Tasks, registry: Registry, job_id: str, *, probe: bool = False) -> dict[str, Any]:
    """输出当前证券及异常位置，不输出凭据；供应商读取仍使用正式共享频控。"""
    with tasks.db.transaction() as connection:
        task = dict(tasks.find(connection, job_id, 'sync'))
        options = task['options_json']
        index, ids = options.get('nextIndex', 0), options.get('stockIds', [])
        snapshot_id = options.get('buildingSnapshotId')
        snapshot = connection.execute(select(t.snapshots).where(t.snapshots.c.id == snapshot_id)).mappings().first() if snapshot_id else None
        stock_row = connection.execute(select(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot_id,
            t.snapshot_stocks.c.stock_id == ids[index])).mappings().first() if index < len(ids) else None
        calendar_rows = connection.execute(select(t.calendar)).mappings().all() if probe else []
        bar_count = connection.scalar(select(func.count()).select_from(t.bars).where(t.bars.c.snapshot_id == snapshot_id)) if snapshot_id else 0
        result = {'job': tasks.view(connection, task), 'snapshotId': snapshot_id, 'snapshotStatus': snapshot['status'] if snapshot else None,
            'savedBars': bar_count, 'nextStock': {'id': stock_row['stock_id'], 'name': stock_row['name']} if stock_row else None}
        history = []
        if snapshot and snapshot['base_snapshot_id'] and stock_row:
            base = connection.execute(select(t.snapshots).where(t.snapshots.c.id == snapshot['base_snapshot_id'])).mappings().first()
            if base and base['source'] == task['data_source']:
                history = tasks.market.history(connection, base, stock_row['stock_id'])
    if not probe or not stock_row:
        return result
    source = registry.get(task['data_source'], task['kind'], tasks.boards(task))
    if isinstance(source, HistorySource):
        source = source.for_history(HistoryPolicy(options.get('historyDays', 150), options.get('historyMaxDays', 0)))
    if isinstance(source, CalendarCacheSource):
        calendar: dict[str, dict[Any, CalendarDay]] = {}
        for row in calendar_rows:
            calendar.setdefault(row['exchange'], {})[row['trade_date']] = CalendarDay(row['is_open'], utc(row['close_at']) if row['close_at'] else None)
        source.use_calendar(calendar, min(utc(row['fetched_at']) for row in calendar_rows))
    try:
        packet = await source.daily(stock_value(stock_row), task['target_date'], tuple(history))
        result['probe'] = {'ok': True, 'bars': len(packet.bars), 'excludedReason': packet.excluded_reason}
    except (ValueError, TypeError, KeyError, ArithmeticError, DomainError, MissingData, SourceDeferred) as error:
        result['probe'] = {'ok': False, 'exception': type(error).__name__,
            'message': str(error)[:300],
            'frames': [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name} for frame in traceback.extract_tb(error.__traceback__)[-6:]]}
    finally:
        if isinstance(source, SourceSession):
            await source.close()
    return result
