"""本文件用已发布真实同源历史验证新浪增量路径，记录请求类别，不写行情业务表。"""

from datetime import date
from typing import Any

from sqlalchemy import select

import stocksieve.infrastructure.persistence.schema as t
from stocksieve.infrastructure.persistence.database import Database, utc
from stocksieve.modules.ingestion.application.ports import CalendarDay
from stocksieve.modules.ingestion.infrastructure.providers.sina import (
    SAMPLES,
    SinaSource,
)
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.application.queries import Market
from stocksieve.modules.market.domain.models import Stock
from stocksieve.shared.errors import DomainError
from stocksieve.shared.settings import Settings


async def check_incremental(database: Database, settings: Settings, policy: SourcePolicy, target: date) -> dict[str, Any]:
    """四板块样本使用真实数据库历史与官方因子，拒绝用模拟行情证明增量功能。"""
    source = SinaSource(policy, settings.state_dir / 'source-cache', settings.history_trading_days, settings.history_max_trading_days)
    market = Market(database)
    with database.transaction() as connection:
        snapshot = connection.execute(select(t.snapshots).where(t.snapshots.c.source == 'sina', t.snapshots.c.status == 'published',
            t.snapshots.c.data_date <= target).order_by(t.snapshots.c.data_date.desc(), t.snapshots.c.version.desc()).limit(1)).mappings().first()
        if not snapshot:
            raise DomainError('DATA_NOT_AVAILABLE', '没有已发布新浪历史，不能验收增量。')
        rows = connection.execute(select(t.calendar).where(t.calendar.c.exchange.in_(['SSE', 'SZSE']))).mappings().all()
        calendar: dict[str, dict[date, CalendarDay]] = {'SSE': {}, 'SZSE': {}}
        for row in rows:
            calendar[row['exchange']][row['trade_date']] = CalendarDay(row['is_open'], utc(row['close_at']) if row['close_at'] else None)
        source.use_calendar(calendar, min(utc(row['fetched_at']) for row in rows))
        stocks = tuple(Stock(f'{"SSE" if symbol.startswith("sh") else "SZSE"}.{symbol[2:]}', symbol[2:], symbol,
            'SSE' if symbol.startswith('sh') else 'SZSE', board) for board, symbol in SAMPLES.items())
        histories = {}
        for stock in stocks:
            days = sorted(day for day, entry in calendar[stock.exchange].items() if entry.is_open and day <= target)
            histories[stock.stock_id] = tuple(market.history(connection, snapshot, stock.stock_id, since=days[-151]))
    original = source.document
    requests = {'quotes': 0, 'factors': 0, 'history': 0, 'shares': 0}

    async def document(url: str, seconds: int = 600) -> Any:
        if 'hq.sinajs' in url:
            requests['quotes'] += 1
        elif '/hfq.js' in url:
            requests['factors'] += 1
        elif 'hisdata' in url:
            requests['history'] += 1
        elif 'getAmountBySymbol' in url:
            requests['shares'] += 1
        return await original(url, seconds)

    source.document = document
    try:
        await source.prepare_quotes(stocks)
        samples = []
        for stock in stocks:
            packet = await source.incremental_packet(stock, target, histories[stock.stock_id])
            if packet is None or not packet.bars:
                raise DomainError('INCREMENTAL_NOT_READY', f'{stock.code}缺少连续历史或已确认收盘报价，需回查历史。')
            bar = packet.bars[-1]
            samples.append({'code': stock.code, 'board': stock.board, 'date': str(bar.day), 'returnedBars': len(packet.bars),
                'close': str(bar.close), 'factor': str(bar.factor), 'totalMarketCap': str(bar.total_market_cap), 'turnover': str(bar.turnover)})
        return {'targetDate': str(target), 'inputSnapshotId': snapshot['id'], 'requests': requests, 'samples': samples,
            'incrementalVerified': requests['history'] == requests['shares'] == 0 and len(samples) == 4}
    finally:
        await source.close()
