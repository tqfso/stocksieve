"""本文件读取不可变快照，提供一致的筛选、策略汇总、个股与 K 线查询。"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from . import tables as t
from .candles import candles
from .database import Database, utc
from .domain import BOARDS, Bar
from .errors import DomainError, MissingData
from .strategies import CONDITIONS, INVALIDATION, NAMES, SORTS
from .validation import date_value, integer, invalid, number, resource_id


def strategy_catalog() -> dict[str, Any]:
    descriptions = {
        'short': ('放量突破', '3～10 个交易日', '关注突破近期高点、成交额同步放大的机会。'),
        'swing': ('回调企稳', '2～8 周', '关注上升趋势中的缩量回调与企稳。'),
        'long': ('趋势持续', '3～12 个月', '关注中长期均线持续向上。'),
        'trend_wait': ('缩量整理', '1～4 周', '关注上升趋势中的整理，等待突破确认。'),
        'base_wait': ('筑底观察', '2～8 周', '关注下跌后低位横盘，等待趋势修复。'),
    }
    labels = {
        'price_breakout': '价格突破', 'amount_expansion': '成交放大', 'ma20_distance': '距MA20适中',
        'uptrend': '趋势向上', 'pullback_near_ma20': '回调至MA20附近', 'pullback_amount': '回调缩量', 'stabilization': '当日企稳',
        'ma_alignment': '均线多头排列', 'ma60_rising': '中期均线上行', 'ma120_rising': '长期均线上行', 'ma60_distance': '距MA60适中',
        'trend_holding': '趋势保持', 'range_contraction': '区间收窄', 'amount_contraction': '成交减少', 'near_range_upper': '接近区间上沿且未突破',
        'trend_unrepaired': '趋势尚待修复', 'low_position': '历史低位', 'no_new_low': '近期未创新低', 'inside_range': '尚未突破',
    }
    details = {
        'price_breakout': '收盘价超过此前20个交易日的最高价。',
        'amount_expansion': '当日成交额至少为此前5日日均成交额的1.5倍。',
        'ma20_distance': '收盘价高于MA20，偏离不超过10%。',
        'uptrend': 'MA20高于MA60，且MA60较5个交易日前抬升。',
        'pullback_near_ma20': '此前5日存在下跌回调，前一日收于MA20之上，回调日距MA20不超过3%。',
        'pullback_amount': '此前5日均额不超过此前20日均额的80%。',
        'stabilization': '当日收盘高于昨日收盘，并高于MA20。',
        'ma_alignment': 'MA20高于MA60，MA60高于MA120。',
        'ma60_rising': 'MA60高于20个交易日前的MA60。',
        'ma120_rising': 'MA120高于20个交易日前的MA120。',
        'ma60_distance': '收盘价高于MA60，偏离不超过20%。',
        'trend_holding': 'MA20不低于MA60，MA60不低于5个交易日前的值。',
        'range_contraction': '此前20日区间上沿除以下沿后的增幅不超过12%。',
        'amount_contraction': '含当日的近5日均额不超过近20日均额的80%。',
        'near_range_upper': '收盘仍在区间内，距离上沿不超过3%。',
        'trend_unrepaired': 'MA20低于MA60。',
        'low_position': '收盘处于此前120日高低价区间的0～35%位置，区间不能退化为同价。',
        'no_new_low': '最近5日最低价不低于此前15日最低价。',
        'inside_range': '收盘仍在此前20日区间上下沿之间。',
    }
    return {'version': 'v1', 'items': [
        {'id': code, 'name': name, 'subtitle': descriptions[code][0], 'observationPeriod': descriptions[code][1],
         'description': descriptions[code][2], 'conditions': [{'id': key, 'label': labels[key], 'description': details[key]} for key in CONDITIONS[code]],
         'invalidationDescription': INVALIDATION[code], 'defaultSort': {'field': SORTS[code][0], 'order': SORTS[code][1]}}
        for code, name in NAMES.items()]}


class Market:
    """所有页面查询先解析固定快照，再按该版本读取历史修订和冻结分类。"""

    def __init__(self, database: Database) -> None:
        self.db = database

    def snapshot(self, connection: Connection, snapshot_id: str | None = None) -> Any:
        query = select(t.snapshots).where(t.snapshots.c.status == 'published')
        if snapshot_id:
            query = query.where(t.snapshots.c.id == resource_id(snapshot_id, 'SNAPSHOT_NOT_FOUND'))
        row = connection.execute(query.order_by(t.snapshots.c.data_date.desc(), t.snapshots.c.published_at.desc(), t.snapshots.c.version.desc()).limit(1)).mappings().first()
        if not row:
            raise DomainError('SNAPSHOT_NOT_FOUND' if snapshot_id else 'DATA_NOT_READY', '所选数据不存在。' if snapshot_id else '收盘数据尚未准备好。', 404 if snapshot_id else 409)
        return row

    @staticmethod
    def snapshot_view(row: Any) -> dict[str, Any]:
        return {'id': row['id'], 'dataDate': row['data_date'].isoformat(), 'publishedAt': utc(row['published_at']).isoformat(),
                'adjustment': row['adjustment'], 'strategyVersion': row['strategy_version']}

    @staticmethod
    def signal_view(row: Any, *, detail: bool = False) -> dict[str, Any]:
        result = {'id': row['id'], 'strategy': row['strategy_code'], 'reason': row['reason'], 'metrics': row['metrics_json']}
        if detail:
            result.update(conditionResults=row['condition_results_json'], invalidation=row['invalidation_json'])
        return result

    def records(self, connection: Connection, snapshot: Any) -> list[dict[str, Any]]:
        rows = connection.execute(select(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot['id'])).mappings().all()
        concepts: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in connection.execute(select(t.concepts).where(t.concepts.c.snapshot_id == snapshot['id']).order_by(t.concepts.c.concept_id)).mappings():
            concepts[row['stock_id']].append({'id': row['concept_id'], 'name': row['concept_name']})
        signals: dict[str, dict[str, Any]] = defaultdict(dict)
        for row in connection.execute(select(t.signals).where(t.signals.c.snapshot_id == snapshot['id'])).mappings():
            signals[row['stock_id']][row['strategy_code']] = row
        return [{
            'stock': {'stockId': row['stock_id'], 'code': row['code'], 'name': row['name'], 'exchange': row['exchange'], 'board': row['board'],
                      'industry': {'id': row['industry_id'], 'name': row['industry_name']} if row['industry_id'] else None,
                      'concepts': concepts[row['stock_id']]},
            'quote': {'price': row['quote_price'], 'change': row['change_pct'], 'turnover': row['turnover'], 'amount': row['amount'], 'totalMarketCap': row['total_market_cap']} if row['quote_price'] is not None else None,
            'signalRows': signals[row['stock_id']],
        } for row in rows]

    def filters_view(self, snapshot: Any, records: list[dict[str, Any]]) -> dict[str, Any]:
        industries = {row['stock']['industry']['id']: row['stock']['industry'] for row in records if row['stock']['industry']}
        concepts = {concept['id']: concept for row in records for concept in row['stock']['concepts']}
        coverage = (snapshot['coverage_json'] or {}).get('filters', {})
        return {'snapshotId': snapshot['id'], 'boards': [{'id': key, 'name': value[0], 'exchange': value[1]} for key, value in BOARDS.items()],
                'industries': sorted(industries.values(), key=lambda row: row['id']), 'concepts': sorted(concepts.values(), key=lambda row: row['id']),
                'coverage': {key: coverage.get(key, 'unavailable') for key in ('board', 'industry', 'concepts', 'turnover', 'totalMarketCap')}}

    def filters(self, snapshot_id: str | None) -> dict[str, Any]:
        with self.db.transaction() as connection:
            snapshot = self.snapshot(connection, snapshot_id)
            return self.filters_view(snapshot, self.records(connection, snapshot))

    def opportunities(self, query: dict[str, str]) -> dict[str, Any]:
        code = query.get('strategy')
        if code not in NAMES:
            raise invalid('strategy', '请选择有效策略。')
        sort = query.get('sort', 'default')
        if sort not in {'default', 'change', 'turnover', 'amount', 'totalMarketCap'} or (sort == 'default' and 'order' in query):
            raise invalid('sort', '排序参数无效。')
        order = query.get('order', 'desc') if sort != 'default' else SORTS[code][1]
        if order not in {'asc', 'desc'}:
            raise invalid('order')
        page, size = integer(query, 'page', 1), integer(query, 'pageSize', 20, maximum=100)
        search = query.get('search', '').strip().casefold()
        if len(search) > 50:
            raise invalid('search')
        if query.get('board') and query['board'] not in BOARDS:
            raise invalid('board')
        bounds = {key: number(query, key, Decimal(100) if 'Turnover' in key else None)
                  for key in ('minTurnover', 'maxTurnover', 'minMarketCap', 'maxMarketCap')}
        for lower, upper in (('minTurnover', 'maxTurnover'), ('minMarketCap', 'maxMarketCap')):
            if bounds[lower] is not None and bounds[upper] is not None and bounds[lower] > bounds[upper]:
                raise invalid(lower, '最小值不能大于最大值。')
        with self.db.transaction() as connection:
            snapshot = self.snapshot(connection, query.get('snapshotId'))
            records = self.records(connection, snapshot)
            filters = self.filters_view(snapshot, records)
        for key, coverage_key in (('industryId', 'industry'), ('conceptId', 'concepts'), ('board', 'board')):
            if key in query and filters['coverage'][coverage_key] == 'unavailable':
                raise DomainError('FILTER_UNAVAILABLE', '所选快照未覆盖该筛选条件。')
        for key, choices in (('industryId', filters['industries']), ('conceptId', filters['concepts'])):
            if key in query and query[key] not in {item['id'] for item in choices}:
                raise invalid(key, '分类选项不存在。')
        for keys, coverage_key in ((('minTurnover', 'maxTurnover'), 'turnover'), (('minMarketCap', 'maxMarketCap'), 'totalMarketCap')):
            if any(key in query for key in keys) and filters['coverage'][coverage_key] == 'unavailable':
                raise DomainError('FILTER_UNAVAILABLE', '所选快照未覆盖该指标。')
        # 1. 先应用策略之外的筛选，以此计算五类策略计数。
        filtered = []
        for row in records:
            stock, quote = row['stock'], row['quote']
            if search and search not in stock['code'].casefold() and search not in stock['name'].casefold():
                continue
            if 'board' in query and stock['board'] != query['board']:
                continue
            if 'industryId' in query and (not stock['industry'] or stock['industry']['id'] != query['industryId']):
                continue
            if 'conceptId' in query and query['conceptId'] not in {item['id'] for item in stock['concepts']}:
                continue
            include = True
            for lower, upper, field in (('minTurnover', 'maxTurnover', 'turnover'), ('minMarketCap', 'maxMarketCap', 'totalMarketCap')):
                value = quote[field] if quote else None
                if (bounds[lower] is not None or bounds[upper] is not None) and (value is None or (bounds[lower] is not None and value < bounds[lower]) or (bounds[upper] is not None and value > bounds[upper])):
                    include = False
            if include:
                filtered.append(row)
        counts = {strategy: sum(strategy in row['signalRows'] for row in filtered) for strategy in NAMES}
        selected = [row for row in filtered if code in row['signalRows']]
        total = len(selected)
        industry_count = len({row['stock']['industry']['id'] for row in selected if row['stock']['industry']})
        field = SORTS[code][0] if sort == 'default' else sort
        # 2. 先固定股票ID次序，再稳定排序，空值无论升降序都放在最后。
        selected.sort(key=lambda row: row['stock']['stockId'])
        def value(row: dict[str, Any]) -> Any:
            return row['signalRows'][code]['sort_value'] if sort == 'default' else (row['quote'] or {}).get(sort)
        known = [row for row in selected if value(row) is not None]
        unknown = [row for row in selected if value(row) is None]
        known.sort(key=value, reverse=order == 'desc')
        paged = (known + unknown)[(page - 1) * size:page * size]
        return {'snapshot': self.snapshot_view(snapshot), 'items': [
            {'stock': row['stock'], 'quote': row['quote'], 'strategies': [key for key in NAMES if key in row['signalRows']], 'signal': self.signal_view(row['signalRows'][code])}
            for row in paged], 'page': page, 'pageSize': size, 'total': total,
            'summary': {'industryCount': industry_count, 'strategyCounts': counts}, 'sort': {'field': field, 'order': order}}

    def stock(self, stock_id: str, snapshot_id: str | None = None) -> dict[str, Any]:
        with self.db.transaction() as connection:
            snapshot = self.snapshot(connection, snapshot_id)
            found = next((row for row in self.records(connection, snapshot) if row['stock']['stockId'] == stock_id), None)
        if not found:
            raise DomainError('STOCK_NOT_FOUND', '股票不在该快照覆盖范围内。', 404)
        return {'snapshot': self.snapshot_view(snapshot), 'stock': found['stock'], 'quote': found['quote'],
                'signals': [self.signal_view(found['signalRows'][key], detail=True) for key in NAMES if key in found['signalRows']]}

    def history(self, connection: Connection, snapshot: Any, stock_id: str) -> list[Bar]:
        rows = connection.execute(select(t.bars).join(t.snapshots, t.bars.c.snapshot_id == t.snapshots.c.id).where(
            t.bars.c.stock_id == stock_id, t.bars.c.source == snapshot['source'], t.snapshots.c.status == 'published',
            t.snapshots.c.version <= snapshot['version'], t.bars.c.trade_date <= snapshot['data_date'],
            t.bars.c.trade_date >= snapshot['history_start_date']).order_by(t.bars.c.trade_date, t.snapshots.c.version.desc())).mappings()
        unique: dict[date, Bar] = {}
        for row in rows:
            if row['trade_date'] not in unique:
                unique[row['trade_date']] = Bar(row['trade_date'], row['open'], row['high'], row['low'], row['close'], row['amount'], row['volume'], row['turnover'], row['total_market_cap'], row['adj_factor'])
        return list(unique.values())

    def candle_page(self, stock_id: str, query: dict[str, str]) -> dict[str, Any]:
        period = query.get('period', 'day')
        if period not in {'day', 'week', 'year'}:
            raise invalid('period')
        limit = integer(query, 'limit', 240, maximum=1000)
        before = date_value(query.get('before'), 'before')
        with self.db.transaction() as connection:
            snapshot = self.snapshot(connection, query.get('snapshotId'))
            stock = connection.execute(select(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == snapshot['id'], t.snapshot_stocks.c.stock_id == stock_id)).mappings().first()
            if not stock:
                raise DomainError('STOCK_NOT_FOUND', '股票不在该快照覆盖范围内。', 404)
            history = self.history(connection, snapshot, stock_id)
            calendar = {date.fromisoformat(key): value for key, value in (snapshot['calendar_json'] or {}).get(stock['exchange'], {}).items()}
        if not history:
            return {'stockId': stock_id, 'snapshotId': snapshot['id'], 'dataDate': snapshot['data_date'], 'period': period, 'adjustment': 'qfq', 'items': [], 'hasMore': False, 'nextBefore': None}
        try:
            anchor = None
            if history[-1].day != snapshot['data_date']:
                day = history[-1].day + timedelta(days=1)
                while day <= snapshot['data_date']:
                    if calendar.get(day) is not False:
                        raise MissingData('快照日复权锚定资料缺失')
                    day += timedelta(days=1)
                anchor = history[-1].day
            output = candles(history, calendar, snapshot['data_date'], period, before, limit, anchor_day=anchor)
        except MissingData as error:
            raise DomainError('DATA_NOT_READY', '该快照的图表资料不完整。', 409) from error
        return {**output, 'stockId': stock_id, 'snapshotId': snapshot['id'], 'dataDate': snapshot['data_date']}
