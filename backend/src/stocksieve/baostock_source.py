"""本文件实现沪深四板块免费批量日线、日历和行业适配；相对复权因子依据源除权参考价计算。"""

import asyncio
import json
import sqlite3
import time
from datetime import date, datetime, time as daytime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .baostock_wire import BaostockWire
from .domain import Bar, Stock, StockState, decimal_value
from .errors import DomainError
from .sources import CalendarDay, DailyPacket, Initialization, SHANGHAI, SourceDeferred, report_progress
from .tencent_metadata import TencentMetadata

FOUR_BOARDS = frozenset({'SSE_MAIN', 'SZSE_MAIN', 'CHINEXT', 'STAR'})


def security(code: str, name: str, listed: str = '', ended: str = '', board_override: str | None = None) -> Stock:
    """仅接受上游股票类型及已核对的沪深代码规则，未知编码不归入主板。"""
    prefixes = {'600': 'SSE_MAIN', '601': 'SSE_MAIN', '603': 'SSE_MAIN', '605': 'SSE_MAIN',
                '688': 'STAR', '689': 'STAR', '000': 'SZSE_MAIN', '001': 'SZSE_MAIN',
                '002': 'SZSE_MAIN', '003': 'SZSE_MAIN', '300': 'CHINEXT', '301': 'CHINEXT'}
    exchange, number = code.split('.')
    market = {'sh': 'SSE', 'sz': 'SZSE'}.get(exchange)
    board = board_override or prefixes.get(number[:3])
    if not market or board not in FOUR_BOARDS:
        raise DomainError('DATA_INCOMPLETE', f'渠道返回了未确认上市板块的证券：{code}。', 503)
    return Stock(f'{market}.{number}', number, name, market, board,
                 date.fromisoformat(listed) if listed else None, date.fromisoformat(ended) if ended else None, code)


class BaostockSource:
    """每个交易日批量请求全部沪深股票，避免逐只股票请求；缓存按日期和证券分批恢复。"""

    id = 'baostock'
    name = 'Baostock（沪深四板块）'
    supported_kinds = frozenset({'initialize', 'catalog', 'daily'})
    boards = FOUR_BOARDS

    def __init__(self, wire: BaostockWire, cache: Path, history_days: int = 150, metadata: TencentMetadata | None = None) -> None:
        self.wire, self.cache, self.history_days = wire, cache, history_days
        self.metadata = metadata
        self.provenance = {'prices': 'baostock', 'volume': 'baostock', 'amount': 'baostock',
                           'calendar': 'baostock', 'industry': 'baostock', 'adjustment': 'baostock-preclose-relative-v1'}
        if metadata:
            self.name = '沪深免费组合（Baostock行情 / 腾讯市值）'
            self.provenance.update(totalMarketCap='tencent', newSecurityBoardTags='tencent')
        cache.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(cache) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, fetched REAL NOT NULL, count INTEGER NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS raw (code TEXT, day TEXT, payload TEXT NOT NULL, PRIMARY KEY(code,day))')
        self._directory: list[dict[str, str]] | None = None
        self._directory_at = 0.0
        self._days: dict[date, CalendarDay] | None = None
        self._calendar_fetched_at: datetime | None = None
        self._latest: dict[date, dict[str, dict[str, str]]] = {}

    def read(self, result: Any) -> list[dict[str, str]]:
        return self.wire.rows(result)

    def directory(self) -> list[dict[str, str]]:
        if self._directory is None or time.time() - self._directory_at > 600:
            self._directory = self.read(self.wire.sdk.query_stock_basic())
            if not self._directory:
                raise DomainError('DATA_INCOMPLETE', '渠道证券目录为空。', 503)
            self._directory_at = time.time()
        return self._directory

    def calendar(self, target: date) -> dict[date, CalendarDay]:
        today = datetime.now(SHANGHAI).date()
        if self._days is None or target not in self._days or self._calendar_fetched_at is None or self._calendar_fetched_at.date() != today:
            rows = None
            # 复用此前已成功保存的官方SDK证据，不因正式部署重复抓取同一份日历。
            for file in sorted(self.wire.policy.directory.glob('baostock-*.json'), reverse=True):
                if datetime.fromtimestamp(file.stat().st_mtime, SHANGHAI).date() != today:
                    continue
                saved = json.loads(file.read_text())
                record = next((item for item in saved.get('results', []) if item.get('id') == 'calendar' and item.get('errorCode') == '0' and item.get('completeFirstPage')), None)
                if record and record.get('fields') == ['calendar_date', 'is_trading_day']:
                    candidate = [dict(zip(record['fields'], values)) for values in record['rows']]
                    if any(row['calendar_date'] == str(target) for row in candidate) and min(row['calendar_date'] for row in candidate) <= str(target - timedelta(days=300)):
                        rows = candidate
                        self._calendar_fetched_at = datetime.fromtimestamp(file.stat().st_mtime, SHANGHAI)
                        break
            if rows is None:
                rows = self.read(self.wire.sdk.query_trade_dates(str(target - timedelta(days=730)), str(date(target.year, 12, 31))))
                self._calendar_fetched_at = datetime.now(SHANGHAI)
            days: dict[date, CalendarDay] = {}
            for row in rows:
                day = date.fromisoformat(row['calendar_date'])
                if row['is_trading_day'] not in {'0', '1'} or day in days:
                    raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '交易日历状态或唯一性不合法。', 503)
                opened = row['is_trading_day'] == '1'
                # 创业板、科创板包含盘后固定价格交易，整体收盘数据保守等待至15:30。
                days[day] = CalendarDay(opened, datetime.combine(day, daytime(15, 30), SHANGHAI) if opened else None)
            if target not in days:
                raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '交易日历未覆盖目标日期。', 503)
            self._days = days
        return self._days

    def stocks(self, target: date) -> tuple[Stock, ...]:
        eligible = []
        for row in self.directory():
            if row['type'] != '1' or row['ipoDate'] and date.fromisoformat(row['ipoDate']) > target or row['outDate'] and date.fromisoformat(row['outDate']) <= target:
                continue
            if row['code'].startswith(('sh.900', 'sz.200', 'bj.')):
                continue
            if row['status'] != '1' and not row['outDate']:
                continue
            eligible.append(row)
        if self.metadata:
            unknown = []
            for row in eligible:
                try:
                    security(row['code'], row['code_name'], board_override=self.metadata.board(row['code']))
                except DomainError:
                    unknown.append(row['code'].replace('.', ''))
            for index in range(0, len(unknown), 100):
                asyncio.run(self.metadata.fetch(unknown[index:index + 100]))
        result = []
        for row in eligible:
            try:
                stock = security(row['code'], row['code_name'], row['ipoDate'], row['outDate'], self.metadata.board(row['code']) if self.metadata else None)
            except DomainError:
                if not self.metadata:
                    raise
                asyncio.run(self.metadata.fetch([row['code'].replace('.', '')]))
                stock = security(row['code'], row['code_name'], row['ipoDate'], row['outDate'], self.metadata.board(row['code']))
            result.append(stock)
        if {stock.board for stock in result} != self.boards or len({stock.stock_id for stock in result}) != len(result):
            raise DomainError('DATA_INCOMPLETE', '证券目录分页或四板块覆盖不完整。', 503)
        return tuple(result)

    async def initialize(self) -> Initialization:
        def load() -> Initialization:
            target = datetime.now(SHANGHAI).date()
            stocks, days = self.stocks(target), self.calendar(target)
            report_progress(f'已取得{len(stocks)}只沪深股票及真实交易日历。', 9, True)
            return Initialization(stocks, {exchange: days for exchange in ('SSE', 'SZSE')}, self._calendar_fetched_at)
        return await asyncio.to_thread(load)

    def load_day(self, target: date) -> dict[str, dict[str, str]]:
        today = datetime.now(SHANGHAI).date()
        age = 600 if (today - target).days < 7 else 30 * 86400
        with sqlite3.connect(self.cache) as connection:
            saved = connection.execute('SELECT fetched FROM days WHERE day=?', (str(target),)).fetchone()
            if saved and 0 <= time.time() - saved[0] < age:
                return {code: json.loads(value) for code, value in connection.execute('SELECT code,payload FROM raw WHERE day=?', (str(target),))}
        rows = self.read(self.wire.sdk.query_daily_history_k_AStock(str(target)))
        if not rows:
            raise DomainError('DATA_NOT_AVAILABLE', '目标日批量收盘数据尚未提供。', 503)
        result = {}
        for row in rows:
            if row['date'] != str(target) or row['adjustflag'] != '3' or row['code'] in result:
                raise DomainError('DATA_INCOMPLETE', '批量行情日期、原价口径或唯一性不正确。', 503)
            if row['tradestatus'] not in {'0', '1'} or row['isST'] not in {'0', '1'}:
                raise DomainError('DATA_INCOMPLETE', '交易或ST状态未确认。', 503)
            result[row['code']] = row
        seen_boards = set()
        for code in result:
            try:
                seen_boards.add(security(code, code, board_override=self.metadata.board(code) if self.metadata else None).board)
            except DomainError:
                # 原始历史中可能有不在当前目录的旧证券；不为其猜测板块，不纳入当前范围。
                continue
        if not self.boards <= seen_boards:
            raise DomainError('DATA_INCOMPLETE', '批量行情未覆盖四板块。', 503)
        with sqlite3.connect(self.cache) as connection:
            connection.execute('DELETE FROM raw WHERE day=?', (str(target),))
            connection.executemany('INSERT INTO raw VALUES (?,?,?)', [(code, str(target), json.dumps(row)) for code, row in result.items()])
            connection.execute('INSERT INTO days VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET fetched=excluded.fetched,count=excluded.count', (str(target), time.time(), len(result)))
        return result

    async def precheck(self, target: date) -> list[dict[str, Any]]:
        def check() -> list[dict[str, Any]]:
            try:
                rows = self.load_day(target)
                self._latest[target] = rows
                status, message = 'ready', None
            except DomainError as error:
                if error.code != 'DATA_NOT_AVAILABLE':
                    raise
                status, message = 'pending', error.message
            return [{'board': board, 'status': status, 'latestDataDate': str(target) if status == 'ready' else None, 'message': message} for board in sorted(self.boards)]
        return await asyncio.to_thread(check)

    async def universe(self, target: date, known: tuple[Stock, ...]) -> tuple[Stock, ...]:
        def prepare() -> tuple[Stock, ...]:
            stocks = self.stocks(target)
            dates = sorted(day for day, entry in self.calendar(target).items() if entry.is_open and day <= target)[-self.history_days:]
            if len(dates) < 140:
                raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '历史交易日不足140天。', 503)
            with sqlite3.connect(self.cache) as connection:
                prepared = {day for (day,) in connection.execute('SELECT day FROM days')}
            completed = len(prepared & {str(day) for day in dates})
            # 先补最近日期，尽早核验批量接口的实际历史范围；最终仍需全部所需日期完成后发布。
            for day in reversed(dates):
                if str(day) in prepared and day < dates[-5]:
                    continue
                rows = self.load_day(day)
                if str(day) not in prepared:
                    completed += 1
                    prepared.add(str(day))
                    report_progress(f'历史准备 {completed}/{len(dates)} 个交易日：{day}，{len(rows)}只股票。', min(70, int(completed / len(dates) * 70)), True)
            current = self._latest.get(target) or self.load_day(target)
            missing = [stock.stock_id for stock in stocks if self.vendor_code(stock) not in current]
            if missing:
                raise DomainError('DATA_INCOMPLETE', f'批量行情缺少{len(missing)}只目录内证券，未发布。', 503)
            if self.metadata:
                try:
                    asyncio.run(self.metadata.prepare(stocks))
                except SourceDeferred as waiting:
                    if waiting.until.timestamp() - time.time() < 60:
                        raise
                    report_progress('补充市值渠道正在冷却，未取得的市值保持未知；行情渠道继续校验。', 9)
                except DomainError as error:
                    report_progress('市值资料本次未完整取得，缺失值保持未知：' + error.message, 9)
            return stocks
        return await asyncio.to_thread(prepare)

    @staticmethod
    def vendor_code(stock: Stock) -> str:
        return ('sh.' if stock.exchange == 'SSE' else 'sz.') + stock.code

    async def daily(self, stock: Stock, target: date, history: tuple[Bar, ...]) -> DailyPacket:
        def load() -> DailyPacket:
            code = self.vendor_code(stock)
            with sqlite3.connect(self.cache) as connection:
                rows = [json.loads(value) for (value,) in connection.execute('SELECT payload FROM raw WHERE code=? AND day<=? ORDER BY day', (code, str(target)))]
            current = next((row for row in reversed(rows) if row['date'] == str(target)), None)
            if not current:
                raise DomainError('DATA_INCOMPLETE', '证券目标日行情缺失。', 503)
            factor = Decimal(1)
            previous: Decimal | None = None
            bars: list[Bar] = []
            for row in rows:
                close = decimal_value(row['close'])
                if row['tradestatus'] == '0' and (close <= 0 or any(decimal_value(row[key]) <= 0 for key in ('open', 'high', 'low', 'preclose'))):
                    continue
                preclose = decimal_value(row['preclose'])
                if previous is not None:
                    if preclose <= 0:
                        raise DomainError('DATA_INCOMPLETE', '缺少可核验的除权参考价。', 503)
                    factor *= previous / preclose
                volume = decimal_value(row['volume']) if row['volume'] else None
                if volume is not None and volume != volume.to_integral_value():
                    raise DomainError('DATA_INCOMPLETE', '成交量不是整数股数。', 503)
                bars.append(Bar(date.fromisoformat(row['date']), *(decimal_value(row[key]) for key in ('open', 'high', 'low', 'close')),
                                decimal_value(row['amount']) if row['amount'] else None, int(volume) if volume is not None else None,
                                decimal_value(row['turn']) if row['turn'] else None, None, factor))
                previous = close
            # 相对因子的任意共同尺度在前复权锚定时抵消；与已有快照通过重叠日对齐。
            old = {bar.day: bar for bar in history}
            overlap = next((bar for bar in reversed(bars) if bar.day in old and old[bar.day].factor is not None), None)
            scale = old[overlap.day].factor / overlap.factor if overlap else Decimal(1)
            packet = []
            for bar in bars:
                prior = old.get(bar.day)
                cap = self.metadata.cap(stock, target, bar.close) if self.metadata and bar.day == target else prior.total_market_cap if prior else None
                normalized = Bar(bar.day, bar.open, bar.high, bar.low, bar.close, bar.amount, bar.volume, bar.turnover, cap, (bar.factor * scale).quantize(Decimal('.000000000001')))
                same = prior and all(getattr(prior, field) == getattr(normalized, field) for field in ('open', 'high', 'low', 'close', 'amount', 'volume', 'turnover', 'total_market_cap')) and prior.factor is not None and abs(prior.factor - normalized.factor) < Decimal('.0000000001')
                if not same:
                    packet.append(normalized)
            state = StockState('trading' if current['tradestatus'] == '1' else 'suspended', current['isST'] == '1', '退' in stock.name)
            return DailyPacket(stock, state, tuple(packet))
        return await asyncio.to_thread(load)

    async def catalog(self, stocks: tuple[Stock, ...]) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            rows = self.read(self.wire.sdk.query_stock_industry())
            if not rows:
                raise DomainError('DATA_NOT_AVAILABLE', '行业渠道未返回可核验记录。', 503)
            known = {self.vendor_code(stock): stock.stock_id for stock in stocks}
            relationships = {}
            for row in rows:
                if row['code'] not in known or not row.get('industry'):
                    continue
                name = row['industry']
                import hashlib
                identifier = 'bs_' + hashlib.sha256((row.get('industryClassification', '') + ':' + name).encode()).hexdigest()[:24]
                relationships[known[row['code']]] = {'industry': {'id': identifier, 'name': name, 'sourceCode': row.get('industry_code') or name}, 'concepts': None}
            # 官方SDK模块提供概念分类；仅使用匿名通道实际返回的关系，不以行业或模型代造题材。
            from baostock.security.sectorinfo import query_stock_concept
            try:
                concepts = self.read(query_stock_concept())
            except DomainError as error:
                if error.code != 'DATA_NOT_AVAILABLE':
                    raise
                concepts = []
                report_progress('当前免费概念接口未提供可核验资料，题材保持未知。', 9)
            for row in concepts:
                if row.get('code') not in known:
                    continue
                name = row.get('concept') or row.get('concept_name') or row.get('conceptName')
                if not name or any(mark in name for mark in (',', ';')):
                    continue
                import hashlib
                relation = relationships.setdefault(known[row['code']], {'industry': None, 'concepts': None})
                if relation['concepts'] is None:
                    relation['concepts'] = []
                item = {'id': 'bs_concept_' + hashlib.sha256(name.encode()).hexdigest()[:24], 'name': name, 'sourceCode': row.get('concept_code') or name}
                if item not in relation['concepts']:
                    relation['concepts'].append(item)
            if concepts:
                report_progress('已核对概念接口字段：' + ','.join(concepts[0]) + f'；源关系数{len(concepts)}。', 9)
            provenance = {'industry': 'baostock'}
            if any(relation.get('concepts') for relation in relationships.values()):
                provenance['concepts'] = 'baostock'
            return {'stocks': relationships, 'provenance': provenance}
        return await asyncio.to_thread(load)
