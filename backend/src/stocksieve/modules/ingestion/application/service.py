"""本文件执行基础信息、独立分类与收盘同步，分批恢复并原子发布行情和策略快照。"""

import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import zip_longest
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

import stocksieve.infrastructure.persistence.schema as t
from stocksieve.infrastructure.persistence.database import utc
from stocksieve.modules.ingestion.application.ports import (
    CalendarCacheSource,
    CalendarDay,
    HistorySource,
    SharedCalendarSource,
    Source,
    SourceSession,
)
from stocksieve.modules.ingestion.application.progress import progress_sink
from stocksieve.modules.ingestion.application.registry import Registry
from stocksieve.modules.jobs.application.service import Tasks
from stocksieve.modules.jobs.domain.models import Lease
from stocksieve.modules.market.application.queries import strategy_catalog
from stocksieve.modules.market.domain.history import HistoryPolicy
from stocksieve.modules.market.domain.models import BOARDS, Stock, adjusted_history
from stocksieve.modules.market.domain.strategies import SORTS, Context, evaluate
from stocksieve.shared.errors import DomainError, MissingData
from stocksieve.shared.time import SHANGHAI


def stock_value(row: Any) -> Stock:
    return Stock(row['stock_id'], row['code'], row['name'], row['exchange'], row['board'], row['listed_on'])


from stocksieve.infrastructure.persistence.operations import upsert


class Sync:
    """每批写入与租约校验同事务，失败或等待不会发布 building 快照。"""

    def __init__(self, tasks: Tasks, registry: Registry) -> None:
        self.tasks, self.db, self.registry = tasks, tasks.db, registry
        self.market = tasks.market

    async def run(self, lease: Lease) -> None:
        with self.db.transaction() as connection:
            task = dict(self.tasks.assert_lease(connection, lease))
        source = self.registry.get(task['data_source'], task['kind'], self.tasks.boards(task))
        policy = HistoryPolicy(task['options_json'].get('historyDays', 150), task['options_json'].get('historyMaxDays', 0))
        if isinstance(source, HistorySource):
            source = source.for_history(policy)
        try:
            with progress_sink(lambda message, progress, advanced: self.tasks.source_progress(lease, message, progress, advanced), operation_id=lease.id, background=task['options_json'].get('historyBackfill', False)):
                if task['kind'] == 'initialize':
                    await self.initialize(lease, source, task)
                elif task['kind'] == 'catalog':
                    await self.catalog(lease, source)
                else:
                    await self.daily(lease, source, task)
        finally:
            if isinstance(source, SourceSession):
                await source.close()

    def finish(self, connection: Connection, lease: Lease, outcome: str, total: int,
               *, snapshot_id: str | None = None, skipped: int = 0, internal: dict[str, Any] | None = None) -> None:
        self.tasks.assert_lease(connection, lease)
        result = {'outcome': outcome, 'totalCount': total, 'processedCount': total,
                  'successCount': total - skipped, 'skippedCount': skipped, 'failedCount': 0,
                  'publishedSnapshotId': snapshot_id, **(internal or {})}
        connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(status='succeeded', progress=100,
            result_json=result, result_snapshot_id=snapshot_id, finished_at=self.tasks.clock(), lease_owner=None, lease_expires_at=None, wait_reason=None))
        connection.execute(t.logs.insert().values(task_id=lease.id, time=self.tasks.clock(), level='info', message='数据任务已完成。'))

    def validate_universe(self, stocks: tuple[Stock, ...], boards: tuple[str, ...]) -> None:
        if not stocks or len({stock.stock_id for stock in stocks}) != len(stocks) or {stock.board for stock in stocks} != set(boards):
            raise DomainError('DATA_INCOMPLETE', '证券范围未完整覆盖本次任务的全部上市板块或包含重复记录。', 503)

    async def initialize(self, lease: Lease, source: Source, task: dict[str, Any]) -> None:
        data = await source.initialize()
        boards = self.tasks.boards(task)
        stocks = tuple(stock for stock in data.stocks if stock.board in boards)
        self.validate_universe(stocks, boards)
        exchanges = {BOARDS[board][1] for board in boards}
        if not exchanges <= data.calendar.keys() or not all(data.calendar[exchange] for exchange in exchanges):
            raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '本次任务所需交易所日历未准备完整。', 503)
        with self.db.transaction(serialized=True) as connection:
            self.tasks.assert_lease(connection, lease)
            for stock in stocks:
                self.save_stock(connection, stock, source.id)
            for exchange, days in data.calendar.items():
                if exchange not in exchanges:
                    continue
                for day, entry in days.items():
                    if entry.is_open is True and (entry.close_at is None or entry.close_at.tzinfo is None or entry.close_at.astimezone(SHANGHAI).date() != day):
                        raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '开市日缺少明确的收盘时间。', 503)
                    upsert(connection, t.calendar, {'exchange': exchange, 'trade_date': day},
                           {'is_open': entry.is_open, 'close_at': entry.close_at if entry.is_open else None, 'source': source.id, 'fetched_at': data.calendar_fetched_at or self.tasks.clock()})
            self.finish(connection, lease, 'initialized', len(stocks))

    def save_stock(self, connection: Connection, stock: Stock, source: str, *, insert_only: bool = False) -> None:
        old = connection.execute(select(t.stocks).where(t.stocks.c.stock_id == stock.stock_id)).mappings().first()
        if old and insert_only:
            return
        codes = {**(old['source_codes'] if old else {}), source: stock.source_code or stock.stock_id}
        upsert(connection, t.stocks, {'stock_id': stock.stock_id}, {'code': stock.code, 'name': stock.name,
               'exchange': stock.exchange, 'board': stock.board, 'listed_on': stock.listed_on,
               'delisted_on': stock.delisted_on, 'source_codes': codes, 'updated_at': self.tasks.clock()})

    async def catalog(self, lease: Lease, source: Source) -> None:
        with self.db.transaction() as connection:
            pool = tuple(stock_value(row) for row in connection.execute(select(t.stocks).order_by(t.stocks.c.stock_id)).mappings())
            latest = connection.scalar(select(t.snapshots.c.id).where(t.snapshots.c.status == 'published').order_by(t.snapshots.c.data_date.desc(), t.snapshots.c.version.desc()).limit(1))
            priority = set(connection.scalars(select(t.signals.c.stock_id).where(t.signals.c.snapshot_id == latest))) if latest else set()
        # 分批渠道优先覆盖当前候选股，其余股票按板块交错，保持所有渠道使用统一股票输入。
        grouped = [[stock for stock in pool if stock.board == board and stock.stock_id not in priority] for board in BOARDS]
        grouped.append([stock for stock in pool if stock.board not in BOARDS and stock.stock_id not in priority])
        stocks = tuple(stock for stock in pool if stock.stock_id in priority) + tuple(stock for group in zip_longest(*grouped) for stock in group if stock is not None)
        data = await source.catalog(stocks)
        if not isinstance(data, dict) or not isinstance(data.get('stocks'), dict):
            raise DomainError('DATA_INCOMPLETE', '分类响应缺少股票关联。', 503)
        valid_ids = {stock.stock_id for stock in stocks}
        if set(data['stocks']) - valid_ids:
            raise DomainError('DATA_INCOMPLETE', '分类包含未知股票。', 503)
        with self.db.transaction(serialized=True) as connection:
            self.tasks.assert_lease(connection, lease)
            previous = connection.execute(select(t.tasks.c.result_json).where(t.tasks.c.kind == 'catalog', t.tasks.c.status == 'succeeded')).scalars().all()
            revision = max((item.get('_catalogRevision', 0) for item in previous if item), default=0) + 1
            fields = set(data.get('fields', ['industry', 'concepts']))
            if not fields or not fields <= {'industry', 'concepts'}:
                raise DomainError('DATA_INCOMPLETE', '分类更新范围无效。', 503)
            # 单类分类渠道保留另一类资料及来源，避免因能力范围不同清空已有关系。
            missing = {'industry', 'concepts'} - fields
            if missing:
                prior = max((item for item in previous if item), key=lambda item: item.get('_catalogRevision', 0), default={})
                for stock_id, old in prior.get('_classifications', {}).items():
                    if stock_id in valid_ids:
                        relation = data['stocks'].setdefault(stock_id, {})
                        for field in missing:
                            relation[field] = old.get(field) if field != 'concepts' or old.get('_conceptsKnown', True) else None
                provenance = dict(data.get('provenance', {}))
                for field in missing:
                    if provider := prior.get('_classificationProvenance', {}).get(field):
                        provenance[field] = provider
                data['provenance'] = provenance
                if 'concepts' in missing:
                    data.update(conceptCoverage=prior.get('_conceptCoverage'), conceptCollection=prior.get('_conceptCollection'))
            categories: dict[str, tuple[str, dict[str, str]]] = {}
            for relation in data['stocks'].values():
                if not isinstance(relation, dict):
                    raise DomainError('DATA_INCOMPLETE', '股票分类关联格式无效。', 503)
                relation['_conceptsKnown'] = isinstance(relation.get('concepts'), list)
                if not relation['_conceptsKnown']:
                    relation['concepts'] = []
                for kind, category in [('industry', relation.get('industry'))] + [('concept', item) for item in relation.get('concepts', [])]:
                    if ('concepts' if kind == 'concept' else kind) not in fields:
                        continue
                    if category:
                        if not category.get('id') or not category.get('name'):
                            raise DomainError('DATA_INCOMPLETE', '分类ID或名称缺失。', 503)
                        if category['id'] in categories and categories[category['id']][0] != kind:
                            raise DomainError('DATA_INCOMPLETE', '同一分类ID不能同时用作行业和题材。', 503)
                        categories[category['id']] = (kind, category)
            # 同一分类仅写一次，避免大量股票共有标签导致长事务与重复往返。
            for key, (kind, category) in categories.items():
                old = connection.execute(select(t.categories).where(t.categories.c.id == key)).mappings().first()
                if old and old['kind'] != kind:
                    raise DomainError('DATA_INCOMPLETE', '同一分类ID不能同时用作行业和题材。', 503)
                codes = {**(old['source_codes'] if old else {}), source.id: category.get('sourceCode', key)}
                upsert(connection, t.categories, {'id': key}, {'kind': kind, 'name': category['name'], 'source_codes': codes, 'updated_at': self.tasks.clock()})
            self.finish(connection, lease, 'catalog_updated', len(stocks), internal={'_classifications': data['stocks'], '_catalogRevision': revision,
                '_classificationProvenance': data.get('provenance', {}), '_conceptCoverage': data.get('conceptCoverage'),
                '_catalogObservedAt': data.get('observedAt'), '_conceptCollection': data.get('conceptCollection')})

    async def precheck(self, lease: Lease, source: Source, task: dict[str, Any]) -> bool:
        target = task['target_date']
        background = task['options_json'].get('historyBackfill', False)
        if not background:
            self.tasks.patch(lease, {'options_json': {**task['options_json'], 'syncPhase': 'checking',
                'sourceProgressMessage': f'正在检查{target}的收盘数据是否就绪，尚未开始同步。'}})
        retry_pending = bool(task['options_json'].get('readinessRetryPending'))
        boards = self.tasks.boards(task)
        exchanges = {BOARDS[board][1] for board in boards}
        with self.db.transaction() as connection:
            calendar = {row['exchange']: row for row in connection.execute(select(t.calendar).where(t.calendar.c.trade_date == target, t.calendar.c.exchange.in_(exchanges))).mappings()}
        if set(calendar) == exchanges and all(row['is_open'] is False for row in calendar.values()):
            checks = [{'board': board, 'status': 'market_closed', 'latestDataDate': None, 'message': '交易日历确认休市。'} for board in boards]
        else:
            checks = await source.precheck(target)
            checks = [item for item in checks if item.get('board') in boards]
        if len(checks) != len(boards) or {item.get('board') for item in checks} != set(boards):
            raise DomainError('DATA_INCOMPLETE', '预检未覆盖本次任务的全部上市板块。', 503)
        allowed = {'ready', 'pending', 'market_closed', 'unknown', 'unsupported'}
        normalized = []
        for item in checks:
            result = {key: item.get(key) for key in ('board', 'status', 'latestDataDate', 'message')}
            if result['status'] not in allowed:
                result['status'] = 'unknown'
            entry = calendar.get(BOARDS[result['board']][1])
            if entry is None or entry['is_open'] is None:
                result['status'] = 'unknown'
            elif entry['is_open'] is False:
                result['status'] = 'market_closed'
            elif entry['close_at'] is None or utc(entry['close_at']) > self.tasks.clock():
                result['status'] = 'pending'
            elif result['status'] == 'market_closed':
                result['status'] = 'unknown'
            elif result['status'] == 'ready' and str(result['latestDataDate']) != str(target):
                result['status'] = 'pending'
            if isinstance(result['latestDataDate'], date):
                result['latestDataDate'] = result['latestDataDate'].isoformat()
            normalized.append(result)
        precheck = {'checkedAt': self.tasks.clock().isoformat(), 'boards': normalized}
        retry_count = task['retry_count'] + int(retry_pending)
        with self.db.transaction() as connection:
            latest_options = self.tasks.assert_lease(connection, lease)['options_json']
        options = {**latest_options, 'readinessRetryPending': False, 'readinessPassed': False}
        self.tasks.patch(lease, {'precheck_json': precheck, 'retry_count': retry_count, 'options_json': options}, '已完成本次覆盖板块的就绪检查。')
        if any(item['status'] == 'unsupported' for item in normalized):
            raise DomainError('BOARD_DATA_UNSUPPORTED', '所选渠道不支持某个开市板块。', 503)
        if all(item['status'] == 'market_closed' for item in normalized):
            with self.db.transaction(serialized=True) as connection:
                self.finish(connection, lease, 'market_closed', 0)
            return False
        if any(item['status'] not in {'ready', 'market_closed'} for item in normalized):
            deadline = utc(task['retry_deadline_at']) if task['retry_deadline_at'] else self.tasks.clock() + timedelta(hours=task['options_json']['readinessHours'])
            if self.tasks.clock() >= deadline:
                missing = '、'.join(item['board'] for item in normalized if item['status'] not in {'ready', 'market_closed'})
                raise DomainError('DATA_READY_TIMEOUT', f'等待收盘数据超时，未就绪板块：{missing}。', 503)
            retry = min(self.tasks.clock() + timedelta(minutes=task['options_json']['retryMinutes']), deadline)
            names = '、'.join(BOARDS[item['board']][0] for item in normalized if item['status'] not in {'ready', 'market_closed'})
            message = f'{target}收盘数据暂未就绪：{names}；下次检查{retry.astimezone(SHANGHAI):%H:%M}，已有快照继续可用。'
            self.tasks.patch(lease, {'status': 'queued', 'lease_owner': None, 'lease_expires_at': None,
                'wait_reason': 'BOARD_DATA_NOT_READY', 'run_after': retry, 'retry_deadline_at': deadline,
                'options_json': {**options, 'syncPhase': 'waiting_data', 'sourceProgressMessage': message}}, message)
            return False
        if not background:
            options['sourceProgressMessage'] = f'{target}收盘资料检查通过，正在同步行情。'
        self.tasks.patch(lease, {'wait_reason': None, 'options_json': {**options, 'readinessPassed': True, 'syncPhase': 'syncing'}}, '所有开市板块已就绪。')
        return True

    async def extend_history_calendar(self, lease: Lease, source: Source, task: dict[str, Any]) -> None:
        """扩大历史窗口时先补真实日历；取数在事务外，失败保留原日历与快照。"""
        if not isinstance(source, HistorySource):
            return
        target = task['target_date']
        policy = HistoryPolicy(task['options_json'].get('historyDays', 150), task['options_json'].get('historyMaxDays', 0))
        exchanges = {BOARDS[board][1] for board in self.tasks.boards(task)}
        with self.db.transaction() as connection:
            earliest = dict(connection.execute(select(t.calendar.c.exchange, func.min(t.calendar.c.trade_date))
                .where(t.calendar.c.exchange.in_(exchanges)).group_by(t.calendar.c.exchange)).all())
            present = set(connection.scalars(select(t.calendar.c.exchange).where(t.calendar.c.exchange.in_(exchanges), t.calendar.c.trade_date == target)))
        start = policy.calendar_start(target)
        if present == exchanges and all(earliest.get(exchange) is not None and earliest[exchange] <= start for exchange in exchanges):
            if isinstance(source, CalendarCacheSource):
                with self.db.transaction() as connection:
                    query = select(t.calendar).where(t.calendar.c.exchange.in_(exchanges))
                    if not isinstance(source, SharedCalendarSource) or not source.accepts_shared_calendar:
                        query = query.where(t.calendar.c.source == source.id)
                    rows = connection.execute(query).mappings().all()
                calendar: dict[str, dict[date, CalendarDay]] = {exchange: {} for exchange in exchanges}
                for row in rows:
                    calendar[row['exchange']][row['trade_date']] = CalendarDay(row['is_open'], utc(row['close_at']) if row['close_at'] else None)
                if rows:
                    source.use_calendar(calendar, min(utc(row['fetched_at']) for row in rows))
            return
        calendar, fetched = await source.history_calendar(target)
        if not exchanges <= set(calendar) or any(not calendar[exchange] or min(calendar[exchange]) > start or target not in calendar[exchange] for exchange in exchanges):
            raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '真实日历未覆盖配置要求的历史范围。', 503)
        with self.db.transaction(serialized=True) as connection:
            self.tasks.assert_lease(connection, lease)
            for exchange in exchanges:
                for day, entry in calendar[exchange].items():
                    if entry.is_open is True and (entry.close_at is None or entry.close_at.tzinfo is None or entry.close_at.astimezone(SHANGHAI).date() != day):
                        raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '开市日缺少明确的收盘时间。', 503)
                    upsert(connection, t.calendar, {'exchange': exchange, 'trade_date': day},
                           {'is_open': entry.is_open, 'close_at': entry.close_at if entry.is_open else None, 'source': source.id, 'fetched_at': fetched})

    async def daily(self, lease: Lease, source: Source, task: dict[str, Any]) -> None:
        await self.extend_history_calendar(lease, source, task)
        # 已通过的同一任务预检在频控续跑时复用；个股未就绪会显式使该标记失效。
        if not task['options_json'].get('readinessPassed') and not await self.precheck(lease, source, task):
            return
        with self.db.transaction() as connection:
            current = dict(self.tasks.assert_lease(connection, lease))
            existing = connection.execute(select(t.snapshots).where(t.snapshots.c.status == 'published', t.snapshots.c.data_date == task['target_date'], t.snapshots.c.source == source.id).order_by(t.snapshots.c.version.desc()).limit(1)).mappings().first()
        if task['trigger'] == 'scheduled' and existing and not task['options_json'].get('historyBackfill'):
            with self.db.transaction(serialized=True) as connection:
                self.finish(connection, lease, 'already_synced', 0, snapshot_id=existing['id'])
            return
        if not current['options_json'].get('buildingSnapshotId'):
            with self.db.transaction() as connection:
                known = tuple(stock_value(row) for row in connection.execute(select(t.stocks)).mappings())
            universe = tuple(stock for stock in await source.universe(task['target_date'], known) if stock.board in self.tasks.boards(task))
            if task['options_json'].get('historyBackfill'):
                self.tasks.yield_to_foreground(lease)
            self.validate_universe(universe, self.tasks.boards(task))
            self.begin_snapshot(lease, source, task, universe)
        # 每只股票独立形成受租约保护的批次；频控等待后从 nextIndex 恢复。
        while True:
            with self.db.transaction() as connection:
                row = dict(self.tasks.assert_lease(connection, lease))
                options = row['options_json']
                snapshot = connection.execute(select(t.snapshots).where(t.snapshots.c.id == options['buildingSnapshotId'])).mappings().one()
                index = options['nextIndex']
                if index >= len(options['stockIds']):
                    break
                stock_id = options['stockIds'][index]
                stock_row = connection.execute(select(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot['id'], t.snapshot_stocks.c.stock_id == stock_id)).mappings().one()
                stock = stock_value(stock_row)
                base = connection.execute(select(t.snapshots).where(t.snapshots.c.id == snapshot['base_snapshot_id'])).mappings().first() if snapshot['base_snapshot_id'] else None
                history = self.market.history(connection, base, stock_id) if base and base['source'] == source.id else []
                history = [bar for bar in history if bar.day <= task['target_date']]
                floor = date.fromisoformat(options['historyCutoffDate']) if options.get('historyCutoffDate') else None
                if floor:
                    history = [bar for bar in history if bar.day >= floor]
            if snapshot['calendar_json'][stock.exchange].get(task['target_date'].isoformat()) is False:
                with self.db.transaction(serialized=True) as connection:
                    latest = self.tasks.assert_lease(connection, lease)
                    options = {**latest['options_json'], 'nextIndex': index + 1, 'skipped': latest['options_json'].get('skipped', 0) + 1}
                    connection.execute(update(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot['id'], t.snapshot_stocks.c.stock_id == stock_id).values(excluded_reason='MARKET_CLOSED'))
                    connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(options_json=options))
                await asyncio.sleep(0)
                continue
            packet = await source.daily(stock, task['target_date'], tuple(history))
            if packet.stock.stock_id != stock_id or any(bar.day > task['target_date'] for bar in packet.bars) or len({bar.day for bar in packet.bars}) != len(packet.bars):
                raise DomainError('DATA_INCOMPLETE', '行情身份、日期或唯一性校验失败。', 503)
            if floor:
                packet = replace(packet, bars=tuple(bar for bar in packet.bars if bar.day >= floor))
            merged = {bar.day: bar for bar in history}
            merged.update({bar.day: bar for bar in packet.bars})
            data = sorted(merged.values(), key=lambda bar: bar.day)
            dates = [date.fromisoformat(key) for key, opened in snapshot['calendar_json'][stock.exchange].items() if opened is True]
            current_bar = merged.get(task['target_date'])
            if packet.state.trade_status == 'trading' and (current_bar is None or current_bar.amount is None or current_bar.factor is None and packet.excluded_reason != 'ADJUSTMENT_UNAVAILABLE'):
                raise DomainError('DATA_INCOMPLETE', '开市股票的当日行情、成交额或复权因子缺失。', 503)
            evaluation = evaluate(data, dates, task['target_date'], packet.state)
            if packet.excluded_reason:
                evaluation = replace(evaluation, signals=(), excluded_reason=packet.excluded_reason)
            quote = {'trade_status': packet.state.trade_status, 'is_st': packet.state.is_st, 'is_delisting': packet.state.is_delisting,
                     'quote_price': current_bar.close if current_bar else None, 'amount': current_bar.amount if current_bar else None,
                     'turnover': current_bar.turnover if current_bar else None, 'total_market_cap': current_bar.total_market_cap if current_bar else None,
                     'excluded_reason': evaluation.excluded_reason, 'change_pct': None, **{f'ma{length}': None for length in (5, 10, 20, 60, 120)}}
            context = Context(data, dates, task['target_date'])
            for length in (2, 5, 10, 20, 60, 120):
                try:
                    window = adjusted_history(context.window(length), task['target_date'])
                    if length == 2:
                        quote['change_pct'] = (window[-1].close / window[-2].close - 1) * 100
                    else:
                        quote[f'ma{length}'] = sum((bar.close for bar in window), Decimal(0)) / length
                except MissingData:
                    pass
            with self.db.transaction(serialized=True) as connection:
                latest = self.tasks.assert_lease(connection, lease)
                if packet.bars:
                    # 每只股票一批写入，避免首次全市场历史产生数百万次逐行往返。
                    statement = (postgres_insert if self.db.engine.dialect.name == 'postgresql' else sqlite_insert)(t.bars)
                    fields = ('open', 'high', 'low', 'close', 'amount', 'volume', 'turnover', 'total_market_cap', 'adj_factor', 'source', 'fetched_at')
                    statement = statement.on_conflict_do_update(index_elements=['snapshot_id', 'stock_id', 'trade_date'], set_={field: getattr(statement.excluded, field) for field in fields})
                    connection.execute(statement, [{'snapshot_id': snapshot['id'], 'stock_id': stock_id, 'trade_date': bar.day,
                        'open': bar.open, 'high': bar.high, 'low': bar.low, 'close': bar.close, 'amount': bar.amount,
                        'volume': bar.volume, 'turnover': bar.turnover, 'total_market_cap': bar.total_market_cap,
                        'adj_factor': bar.factor, 'source': source.id, 'fetched_at': self.tasks.clock()} for bar in packet.bars])
                connection.execute(update(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot['id'], t.snapshot_stocks.c.stock_id == stock_id).values(**quote))
                connection.execute(delete(t.signals).where(t.signals.c.snapshot_id == snapshot['id'], t.signals.c.stock_id == stock_id))
                for signal in evaluation.signals:
                    connection.execute(t.signals.insert().values(id=signal['id'], snapshot_id=snapshot['id'], stock_id=stock_id,
                        strategy_code=signal['strategy'], reason=signal['reason'], metrics_json=signal['metrics'], condition_results_json=signal['conditionResults'],
                        invalidation_json=signal['invalidation'], sort_value=Decimal(str(signal['metrics'][SORTS[signal['strategy']][0]]))))
                options = {**latest['options_json'], 'nextIndex': index + 1,
                           'sourceProgressMessage': f'已计算并保存{index + 1}/{len(options["stockIds"])}只股票。'}
                start = min((bar.day for bar in data), default=task['target_date'])
                old_start = snapshot['history_start_date']
                connection.execute(update(t.snapshots).where(t.snapshots.c.id == snapshot['id']).values(history_start_date=min(start, old_start) if old_start else start))
                connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(options_json=options, progress=min(98, 80 + int((index + 1) / len(options['stockIds']) * 18))))
            if task['options_json'].get('historyBackfill'):
                self.tasks.yield_to_foreground(lease)
            await asyncio.sleep(0)
        self.publish(lease)

    def begin_snapshot(self, lease: Lease, source: Source, task: dict[str, Any], universe: tuple[Stock, ...]) -> None:
        with self.db.transaction(serialized=True) as connection:
            row = self.tasks.assert_lease(connection, lease)
            base = connection.execute(select(t.snapshots).where(t.snapshots.c.status == 'published').order_by(t.snapshots.c.version.desc()).limit(1)).mappings().first()
            frozen_universe = None
            if row['options_json'].get('historyBackfill'):
                base = connection.execute(select(t.snapshots).where(t.snapshots.c.id == row['options_json']['strategySnapshotId'],
                    t.snapshots.c.status == 'published', t.snapshots.c.source == source.id, t.snapshots.c.data_date == task['target_date'])).mappings().one()
                frozen_universe = tuple(stock_value(item) for item in connection.execute(select(t.snapshot_stocks)
                    .where(t.snapshot_stocks.c.snapshot_id == base['id'])).mappings())
                if {stock.stock_id for stock in frozen_universe} != {stock.stock_id for stock in universe}:
                    raise DomainError('DATA_INCOMPLETE', '历史回补的证券范围与原策略快照不一致。', 503)
                universe = frozen_universe
            cutoff = datetime.combine(task['target_date'], time.max, SHANGHAI)
            exchanges = {BOARDS[board][1] for board in self.tasks.boards(task)}
            latest_closed = connection.scalar(select(func.max(t.calendar.c.trade_date)).where(t.calendar.c.exchange.in_(exchanges), t.calendar.c.is_open.is_(True), t.calendar.c.close_at <= self.tasks.clock()))
            # 最新收盘研究可使用构建时取得的标签，另存实际资料时间；历史回补不套用后取得分类。
            if latest_closed == task['target_date']:
                cutoff = self.tasks.clock()
            catalog = connection.execute(select(t.tasks).where(t.tasks.c.kind == 'catalog', t.tasks.c.status == 'succeeded', t.tasks.c.finished_at <= cutoff)
                .order_by(func.coalesce(t.tasks.c.result_json['_catalogRevision'].as_integer(), 0).desc(), t.tasks.c.finished_at.desc()).limit(1)).mappings().first()
            if frozen_universe is not None:
                # 跨日回补沿用原策略快照已取得的分类，不因后来获取资料而抹去或替换标签。
                catalog_id = (base['coverage_json'] or {}).get('catalogTaskId')
                catalog = connection.execute(select(t.tasks).where(t.tasks.c.id == catalog_id)).mappings().first() if catalog_id else None
            classifications = (catalog['result_json'] or {}).get('_classifications', {}) if catalog else {}
            calendar: dict[str, dict[str, bool | None]] = {key: {} for key in ('SSE', 'SZSE', 'BSE')}
            for entry in connection.execute(select(t.calendar)).mappings():
                calendar[entry['exchange']][entry['trade_date'].isoformat()] = entry['is_open']
            policy = HistoryPolicy(row['options_json'].get('historyDays', 150), row['options_json'].get('historyMaxDays', 0))
            floor = policy.cutoff((date.fromisoformat(day) for exchange in exchanges for day, opened in calendar[exchange].items() if opened is True), task['target_date'])
            history_start = base['history_start_date'] if base and base['source'] == source.id else None
            if history_start and floor:
                history_start = max(history_start, floor)
            snapshot_id = t.identifier()
            connection.execute(t.snapshots.insert().values(id=snapshot_id, sync_task_id=lease.id,
                base_snapshot_id=base['id'] if base else None, data_date=task['target_date'], source=source.id, status='building',
                adjustment='qfq', strategy_version='v1', calculation_version='v1', strategy_config_json=strategy_catalog(), calendar_json=calendar,
                history_start_date=history_start, created_at=self.tasks.clock()))
            for stock in universe:
                self.save_stock(connection, stock, source.id, insert_only=True)
                relation = classifications.get(stock.stock_id, {})
                industry = relation.get('industry')
                connection.execute(t.snapshot_stocks.insert().values(snapshot_id=snapshot_id, stock_id=stock.stock_id,
                    code=stock.code, name=stock.name, exchange=stock.exchange, board=stock.board, listed_on=stock.listed_on,
                    industry_id=industry['id'] if industry else None, industry_name=industry['name'] if industry else None))
                if relation.get('concepts'):
                    connection.execute(t.concepts.insert(), [{'snapshot_id': snapshot_id, 'stock_id': stock.stock_id,
                        'concept_id': concept['id'], 'concept_name': concept['name']} for concept in relation['concepts']])
            options = {**row['options_json'], 'buildingSnapshotId': snapshot_id,
                       'syncPhase': 'calculating',
                       'historyCutoffDate': floor.isoformat() if floor else None,
                       'sourceProgressMessage': '历史与补充资料准备完成，正在计算策略并保存结果。',
                       'stockIds': [stock.stock_id for stock in universe], 'nextIndex': 0, 'catalogTaskId': catalog['id'] if catalog else None,
                       'catalogAsOfAt': ((catalog['result_json'] or {}).get('_catalogObservedAt') or utc(catalog['finished_at']).isoformat()) if catalog else None,
                       'conceptCoverage': (catalog['result_json'] or {}).get('_conceptCoverage') if catalog else None,
                       'dataProvenance': {**getattr(source, 'provenance', {}), **((catalog['result_json'] or {}).get('_classificationProvenance', {}) if catalog else {})},
                       'catalogKnownCount': sum(stock.stock_id in classifications and classifications[stock.stock_id].get('_conceptsKnown', True) for stock in universe)}
            connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(options_json=options, progress=80))

    def publish(self, lease: Lease) -> None:
        with self.db.transaction(serialized=True) as connection:
            task = self.tasks.assert_lease(connection, lease)
            options = task['options_json']
            snapshot_id = options['buildingSnapshotId']
            rows = connection.execute(select(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot_id)).mappings().all()
            if options['nextIndex'] != len(options['stockIds']) or len(rows) != len(options['stockIds']):
                raise DomainError('DATA_INCOMPLETE', '快照尚未完成全部股票校验。', 503)
            def coverage(count: int) -> str:
                return 'complete' if count == len(rows) else 'partial' if count else 'unavailable'
            filters = {'board': coverage(sum(row['board'] is not None for row in rows)),
                       'industry': coverage(sum(row['industry_id'] is not None for row in rows)),
                       'concepts': coverage(options['catalogKnownCount']),
                       'turnover': coverage(sum(row['turnover'] is not None for row in rows)),
                       'totalMarketCap': coverage(sum(row['total_market_cap'] is not None for row in rows))}
            if options.get('conceptCoverage') == 'partial' and filters['concepts'] == 'complete':
                filters['concepts'] = 'partial'
            version = connection.scalar(text('SELECT nextval(CAST(:sequence AS regclass))'), {'sequence': f'{self.db.schema or "public"}.snapshot_version_seq'}) if self.db.engine.dialect.name == 'postgresql' else (connection.scalar(select(func.max(t.snapshots.c.version))) or 0) + 1
            connection.execute(update(t.snapshots).where(t.snapshots.c.id == snapshot_id).values(status='published', version=version,
                published_at=self.tasks.clock(), coverage_json={'totalStocks': len(rows), 'boards': list(self.tasks.boards(task)), 'filters': filters, 'catalogTaskId': options['catalogTaskId'], 'catalogAsOfAt': options.get('catalogAsOfAt'), 'dataProvenance': options.get('dataProvenance', {}),
                    'historyPolicy': {'initialDays': options.get('historyDays', 150), 'maxDays': options.get('historyMaxDays', 0)}}))
            self.finish(connection, lease, 'updated', len(rows), snapshot_id=snapshot_id, skipped=options.get('skipped', 0))
            self.schedule_backfill(connection, task, snapshot_id)

    def schedule_backfill(self, connection: Connection, task: Any, snapshot_id: str) -> None:
        """近期策略先发布，较早历史作为后续任务补齐；完成过的相同窗口不重复安排。"""
        options = task['options_json']
        initial, maximum = options.get('historyDays', 150), options.get('historyMaxDays', 0)
        source = self.registry.get(task['data_source'], 'daily', self.tasks.boards(task))
        if maximum <= initial or options.get('historyBackfill') or not isinstance(source, HistorySource):
            return
        previous = connection.execute(select(t.tasks.c.id).where(t.tasks.c.category == 'sync', t.tasks.c.kind == 'daily',
            t.tasks.c.data_source == task['data_source'], t.tasks.c.status.in_(['queued', 'running', 'succeeded']),
            t.tasks.c.options_json['historyBackfill'].as_boolean().is_(True),
            t.tasks.c.options_json['historyMaxDays'].as_integer() >= maximum)).first()
        if previous:
            return
        self.tasks.new(connection, category='sync', kind='daily', user_id=task['created_by'],
            target=task['target_date'], source=task['data_source'], scheduled_for=task['scheduled_for'], options={
                'marketBoards': list(self.tasks.boards(task)), 'historyDays': maximum, 'historyMaxDays': maximum,
                'historyBackfill': True, 'strategySnapshotId': snapshot_id,
                'sourceProgressMessage': f'近期策略已发布，后台继续补齐最多{maximum}个交易日的历史行情。',
                'retryMinutes': options['retryMinutes'], 'readinessHours': options['readinessHours']})
