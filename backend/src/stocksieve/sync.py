"""本文件执行基础信息、独立分类与收盘同步，分批恢复并原子发布行情和策略快照。"""

import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import Connection

from . import tables as t
from .database import utc
from .domain import BOARDS, Bar, Stock, adjusted_history
from .errors import DomainError, MissingData
from .market import strategy_catalog
from .sources import Registry, Source
from .strategies import Context, SORTS, evaluate
from .tasks import Lease, SHANGHAI, Tasks


def upsert(connection: Connection, table: Any, keys: dict[str, Any], values: dict[str, Any]) -> None:
    """调用者持有短事务锁，更新只作用于指定版本的记录。"""
    predicate = [table.c[key] == value for key, value in keys.items()]
    if connection.execute(select(next(iter(table.primary_key.columns))).where(*predicate)).first():
        connection.execute(update(table).where(*predicate).values(**values))
    else:
        connection.execute(table.insert().values(**keys, **values))


def stock_value(row: Any) -> Stock:
    return Stock(row['stock_id'], row['code'], row['name'], row['exchange'], row['board'], row['listed_on'])


class Sync:
    """每批写入与租约校验同事务，失败或等待不会发布 building 快照。"""

    def __init__(self, tasks: Tasks, registry: Registry) -> None:
        self.tasks, self.db, self.registry = tasks, tasks.db, registry
        self.market = tasks.market

    async def run(self, lease: Lease) -> None:
        with self.db.transaction() as connection:
            task = dict(self.tasks.assert_lease(connection, lease))
        source = self.registry.get(task['data_source'], task['kind'])
        if task['kind'] == 'initialize':
            await self.initialize(lease, source)
        elif task['kind'] == 'catalog':
            await self.catalog(lease, source)
        else:
            await self.daily(lease, source, task)

    def finish(self, connection: Connection, lease: Lease, outcome: str, total: int,
               *, snapshot_id: str | None = None, skipped: int = 0, internal: dict[str, Any] | None = None) -> None:
        self.tasks.assert_lease(connection, lease)
        result = {'outcome': outcome, 'totalCount': total, 'processedCount': total,
                  'successCount': total - skipped, 'skippedCount': skipped, 'failedCount': 0,
                  'publishedSnapshotId': snapshot_id, **(internal or {})}
        connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(status='succeeded', progress=100,
            result_json=result, result_snapshot_id=snapshot_id, finished_at=self.tasks.clock(), lease_owner=None, lease_expires_at=None, wait_reason=None))
        connection.execute(t.logs.insert().values(task_id=lease.id, time=self.tasks.clock(), level='info', message='数据任务已完成。'))

    def validate_universe(self, stocks: tuple[Stock, ...]) -> None:
        if not stocks or len({stock.stock_id for stock in stocks}) != len(stocks) or {stock.board for stock in stocks} != set(BOARDS):
            raise DomainError('DATA_INCOMPLETE', '证券范围未完整覆盖五个上市板块或包含重复记录。', 503)

    async def initialize(self, lease: Lease, source: Source) -> None:
        data = await source.initialize()
        self.validate_universe(data.stocks)
        if set(data.calendar) != {'SSE', 'SZSE', 'BSE'} or not all(data.calendar.values()):
            raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '三个交易所的日历未准备完整。', 503)
        with self.db.transaction(serialized=True) as connection:
            self.tasks.assert_lease(connection, lease)
            for stock in data.stocks:
                self.save_stock(connection, stock, source.id)
            for exchange, days in data.calendar.items():
                for day, entry in days.items():
                    if entry.is_open is True and (entry.close_at is None or entry.close_at.tzinfo is None or entry.close_at.astimezone(SHANGHAI).date() != day):
                        raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '开市日缺少明确的收盘时间。', 503)
                    upsert(connection, t.calendar, {'exchange': exchange, 'trade_date': day},
                           {'is_open': entry.is_open, 'close_at': entry.close_at if entry.is_open else None, 'source': source.id, 'fetched_at': self.tasks.clock()})
            self.finish(connection, lease, 'initialized', len(data.stocks))

    def save_stock(self, connection: Connection, stock: Stock, source: str, *, insert_only: bool = False) -> None:
        old = connection.execute(select(t.stocks).where(t.stocks.c.stock_id == stock.stock_id)).mappings().first()
        if old and insert_only:
            return
        codes = {**(old['source_codes'] if old else {}), source: stock.stock_id}
        upsert(connection, t.stocks, {'stock_id': stock.stock_id}, {'code': stock.code, 'name': stock.name,
               'exchange': stock.exchange, 'board': stock.board, 'listed_on': stock.listed_on,
               'delisted_on': stock.delisted_on, 'source_codes': codes, 'updated_at': self.tasks.clock()})

    async def catalog(self, lease: Lease, source: Source) -> None:
        with self.db.transaction() as connection:
            stocks = tuple(stock_value(row) for row in connection.execute(select(t.stocks)).mappings())
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
            for relation in data['stocks'].values():
                if not isinstance(relation, dict):
                    raise DomainError('DATA_INCOMPLETE', '股票分类关联格式无效。', 503)
                relation['_conceptsKnown'] = isinstance(relation.get('concepts'), list)
                if not relation['_conceptsKnown']:
                    relation['concepts'] = []
                for kind, category in [('industry', relation.get('industry'))] + [('concept', item) for item in relation.get('concepts', [])]:
                    if category:
                        if not category.get('id') or not category.get('name'):
                            raise DomainError('DATA_INCOMPLETE', '分类ID或名称缺失。', 503)
                        old = connection.execute(select(t.categories).where(t.categories.c.id == category['id'])).mappings().first()
                        if old and old['kind'] != kind:
                            raise DomainError('DATA_INCOMPLETE', '同一分类ID不能同时用作行业和题材。', 503)
                        codes = {**(old['source_codes'] if old else {}), source.id: category['id']}
                        upsert(connection, t.categories, {'id': category['id']}, {'kind': kind, 'name': category['name'], 'source_codes': codes, 'updated_at': self.tasks.clock()})
            self.finish(connection, lease, 'catalog_updated', len(stocks), internal={'_classifications': data['stocks'], '_catalogRevision': revision})

    async def precheck(self, lease: Lease, source: Source, task: dict[str, Any]) -> bool:
        target = task['target_date']
        retry_pending = bool(task['options_json'].get('readinessRetryPending'))
        with self.db.transaction() as connection:
            calendar = {row['exchange']: row for row in connection.execute(select(t.calendar).where(t.calendar.c.trade_date == target)).mappings()}
        if set(calendar) == {'SSE', 'SZSE', 'BSE'} and all(row['is_open'] is False for row in calendar.values()):
            checks = [{'board': board, 'status': 'market_closed', 'latestDataDate': None, 'message': '交易日历确认休市。'} for board in BOARDS]
        else:
            checks = await source.precheck(target)
        if len(checks) != 5 or {item.get('board') for item in checks} != set(BOARDS):
            raise DomainError('DATA_INCOMPLETE', '预检没有覆盖五个上市板块。', 503)
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
        options = {**task['options_json'], 'readinessRetryPending': False}
        self.tasks.patch(lease, {'precheck_json': precheck, 'retry_count': retry_count, 'options_json': options}, '已完成五板块就绪检查。')
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
            self.tasks.patch(lease, {'status': 'queued', 'lease_owner': None, 'lease_expires_at': None,
                'wait_reason': 'BOARD_DATA_NOT_READY', 'run_after': retry, 'retry_deadline_at': deadline}, '板块尚未就绪，等待下一轮预检。')
            return False
        self.tasks.patch(lease, {'wait_reason': None}, '所有开市板块已就绪。')
        return True

    async def daily(self, lease: Lease, source: Source, task: dict[str, Any]) -> None:
        if not await self.precheck(lease, source, task):
            return
        with self.db.transaction() as connection:
            current = dict(self.tasks.assert_lease(connection, lease))
            existing = connection.execute(select(t.snapshots).where(t.snapshots.c.status == 'published', t.snapshots.c.data_date == task['target_date'], t.snapshots.c.source == source.id).order_by(t.snapshots.c.version.desc()).limit(1)).mappings().first()
        if task['trigger'] == 'scheduled' and existing:
            with self.db.transaction(serialized=True) as connection:
                self.finish(connection, lease, 'already_synced', 0, snapshot_id=existing['id'])
            return
        if not current['options_json'].get('buildingSnapshotId'):
            with self.db.transaction() as connection:
                known = tuple(stock_value(row) for row in connection.execute(select(t.stocks)).mappings())
            universe = await source.universe(task['target_date'], known)
            self.validate_universe(universe)
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
            merged = {bar.day: bar for bar in history}
            merged.update({bar.day: bar for bar in packet.bars})
            data = sorted(merged.values(), key=lambda bar: bar.day)
            dates = [date.fromisoformat(key) for key, opened in snapshot['calendar_json'][stock.exchange].items() if opened is True]
            current_bar = merged.get(task['target_date'])
            if packet.state.trade_status == 'trading' and (current_bar is None or current_bar.amount is None or current_bar.factor is None):
                raise DomainError('DATA_INCOMPLETE', '开市股票的当日行情、成交额或复权因子缺失。', 503)
            evaluation = evaluate(data, dates, task['target_date'], packet.state)
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
                for bar in packet.bars:
                    upsert(connection, t.bars, {'snapshot_id': snapshot['id'], 'stock_id': stock_id, 'trade_date': bar.day},
                           {'open': bar.open, 'high': bar.high, 'low': bar.low, 'close': bar.close, 'amount': bar.amount,
                            'volume': bar.volume, 'turnover': bar.turnover, 'total_market_cap': bar.total_market_cap,
                            'adj_factor': bar.factor, 'source': source.id, 'fetched_at': self.tasks.clock()})
                connection.execute(update(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot['id'], t.snapshot_stocks.c.stock_id == stock_id).values(**quote))
                connection.execute(delete(t.signals).where(t.signals.c.snapshot_id == snapshot['id'], t.signals.c.stock_id == stock_id))
                for signal in evaluation.signals:
                    connection.execute(t.signals.insert().values(id=signal['id'], snapshot_id=snapshot['id'], stock_id=stock_id,
                        strategy_code=signal['strategy'], reason=signal['reason'], metrics_json=signal['metrics'], condition_results_json=signal['conditionResults'],
                        invalidation_json=signal['invalidation'], sort_value=Decimal(str(signal['metrics'][SORTS[signal['strategy']][0]]))))
                options = {**latest['options_json'], 'nextIndex': index + 1}
                start = min((bar.day for bar in data), default=task['target_date'])
                old_start = snapshot['history_start_date']
                connection.execute(update(t.snapshots).where(t.snapshots.c.id == snapshot['id']).values(history_start_date=min(start, old_start) if old_start else start))
                connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(options_json=options, progress=min(95, 10 + int((index + 1) / len(options['stockIds']) * 85))))
            await asyncio.sleep(0)
        self.publish(lease)

    def begin_snapshot(self, lease: Lease, source: Source, task: dict[str, Any], universe: tuple[Stock, ...]) -> None:
        with self.db.transaction(serialized=True) as connection:
            row = self.tasks.assert_lease(connection, lease)
            base = connection.execute(select(t.snapshots).where(t.snapshots.c.status == 'published').order_by(t.snapshots.c.version.desc()).limit(1)).mappings().first()
            cutoff = datetime.combine(task['target_date'], time.max, SHANGHAI)
            catalog = connection.execute(select(t.tasks).where(t.tasks.c.kind == 'catalog', t.tasks.c.status == 'succeeded', t.tasks.c.finished_at <= cutoff)
                .order_by(func.coalesce(t.tasks.c.result_json['_catalogRevision'].as_integer(), 0).desc(), t.tasks.c.finished_at.desc()).limit(1)).mappings().first()
            classifications = (catalog['result_json'] or {}).get('_classifications', {}) if catalog else {}
            calendar: dict[str, dict[str, bool | None]] = {key: {} for key in ('SSE', 'SZSE', 'BSE')}
            for entry in connection.execute(select(t.calendar)).mappings():
                calendar[entry['exchange']][entry['trade_date'].isoformat()] = entry['is_open']
            snapshot_id = t.identifier()
            connection.execute(t.snapshots.insert().values(id=snapshot_id, sync_task_id=lease.id,
                base_snapshot_id=base['id'] if base else None, data_date=task['target_date'], source=source.id, status='building',
                adjustment='qfq', strategy_version='v1', calculation_version='v1', strategy_config_json=strategy_catalog(), calendar_json=calendar,
                history_start_date=base['history_start_date'] if base and base['source'] == source.id else None, created_at=self.tasks.clock()))
            for stock in universe:
                self.save_stock(connection, stock, source.id, insert_only=True)
                relation = classifications.get(stock.stock_id, {})
                industry = relation.get('industry')
                connection.execute(t.snapshot_stocks.insert().values(snapshot_id=snapshot_id, stock_id=stock.stock_id,
                    code=stock.code, name=stock.name, exchange=stock.exchange, board=stock.board, listed_on=stock.listed_on,
                    industry_id=industry['id'] if industry else None, industry_name=industry['name'] if industry else None))
                for concept in relation.get('concepts', []):
                    connection.execute(t.concepts.insert().values(snapshot_id=snapshot_id, stock_id=stock.stock_id, concept_id=concept['id'], concept_name=concept['name']))
            options = {**row['options_json'], 'buildingSnapshotId': snapshot_id,
                       'stockIds': [stock.stock_id for stock in universe], 'nextIndex': 0, 'catalogTaskId': catalog['id'] if catalog else None,
                       'catalogKnownCount': sum(stock.stock_id in classifications and classifications[stock.stock_id].get('_conceptsKnown', True) for stock in universe)}
            connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(options_json=options, progress=10))

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
            version = connection.scalar(text('SELECT nextval(CAST(:sequence AS regclass))'), {'sequence': f'{self.db.schema or "public"}.snapshot_version_seq'}) if self.db.engine.dialect.name == 'postgresql' else (connection.scalar(select(func.max(t.snapshots.c.version))) or 0) + 1
            connection.execute(update(t.snapshots).where(t.snapshots.c.id == snapshot_id).values(status='published', version=version,
                published_at=self.tasks.clock(), coverage_json={'totalStocks': len(rows), 'filters': filters, 'catalogTaskId': options['catalogTaskId']}))
            self.finish(connection, lease, 'updated', len(rows), snapshot_id=snapshot_id, skipped=options.get('skipped', 0))
