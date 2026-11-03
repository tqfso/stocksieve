"""本文件提供新浪收盘行情渠道，独立读取原价、累计复权因子、历史股本和真实交易日历。"""

import json
import re
from bisect import bisect_right
from copy import copy
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlencode
from uuid import uuid4

from stocksieve.modules.ingestion.application.ports import (
    CalendarDay,
    DailyPacket,
    Initialization,
    SourceDeferred,
)
from stocksieve.modules.ingestion.application.progress import (
    is_background_sync,
    report_progress,
)
from stocksieve.modules.ingestion.infrastructure.checkpoint import SourceCheckpoint
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.providers.sina_catalog import (
    SinaCatalog,
)
from stocksieve.modules.ingestion.infrastructure.providers.sina_codec import (
    SinaDecoder,
    assignment,
)
from stocksieve.modules.ingestion.infrastructure.providers.sina_metadata import (
    SinaMarketData,
    quote_rows,
)
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.domain.history import HistoryPolicy
from stocksieve.modules.market.domain.models import (
    Bar,
    Stock,
    StockState,
    decimal_value,
)
from stocksieve.shared.errors import DomainError
from stocksieve.shared.time import SHANGHAI

BOARDS = frozenset({'SSE_MAIN', 'SZSE_MAIN', 'CHINEXT', 'STAR'})
SAMPLES = {'SSE_MAIN': 'sh600000', 'SZSE_MAIN': 'sz000001', 'CHINEXT': 'sz300750', 'STAR': 'sh688981'}
BASE = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'


def effective(rows: list[tuple[date, Decimal | None]], day: date) -> Decimal | None:
    """按生效日向后匹配；无效记录保留为空，不能跨过它沿用此前有效值。"""
    index = bisect_right([item[0] for item in rows], day) - 1
    return rows[index][1] if index >= 0 else None


def dated_values(rows: list[dict[str, Any]], day_key: str, value_key: str, multiplier: int = 1) -> list[tuple[date, Decimal | None]]:
    values: dict[date, Decimal | None] = {}
    for row in rows:
        day = date.fromisoformat(str(row[day_key])[:10])
        try:
            number = decimal_value(row[value_key]) * multiplier
            values[day] = number if number > 0 else None
        except (ValueError, ArithmeticError):
            values[day] = None
    return sorted(values.items())


class SinaSource:
    """由新浪提供价格、因子和元资料，历史窗口由任务冻结。"""

    id = 'sina'
    name = '新浪行情（沪深四板块）'
    supported_kinds = frozenset({'daily', 'initialize'})
    boards = BOARDS
    accepts_shared_calendar = True
    provenance: ClassVar[dict[str, str]] = {'prices': 'sina', 'volume': 'sina', 'amount': 'sina', 'calendar': 'sina',
                  'adjustment': 'sina-hfq-factor', 'turnover': 'sina-historical-float-shares-and-quotes', 'totalMarketCap': 'sina'}

    def __init__(self, policy: SourcePolicy, cache: Path, history_days: int = 150, history_max_days: int = 0) -> None:
        self.policy, self.cache = policy, cache
        self.history_policy = HistoryPolicy(history_days, history_max_days)
        self._calendar: dict[date, CalendarDay] = {}
        self._calendar_at: datetime | None = None
        self._decoder: SinaDecoder | None = None
        self._http: SourceHTTP | None = None
        self._quotes: dict[str, dict[str, str]] = {}
        quotes_path = cache / 'sina-quotes.json'
        if quotes_path.exists():
            self._quotes = json.loads(quotes_path.read_text())
        self.market_data = SinaMarketData(cache)
        self.market_data.quotes = self._quotes
        self._known: tuple[Stock, ...] = ()
        self.force_read = False
        self.history_revision = False

    @property
    def incremental_history_days(self) -> int:
        return self.history_policy.initial_days

    def use_securities(self, stocks: tuple[Stock, ...]) -> None:
        self._known = stocks

    def for_snapshot(self, provenance: dict[str, Any], *, force: bool = False) -> 'SinaSource':
        source = copy(self)
        source.force_read = force
        source.provenance = {**self.provenance, **provenance}
        source._http = None
        return source

    def for_history(self, policy: HistoryPolicy) -> 'SinaSource':
        source = copy(self)
        source.history_policy = policy
        source._http = None
        return source

    async def document(self, url: str, seconds: int = 600) -> tuple[bytes, datetime]:
        # 同一任务复用连接，减少每份资料的TLS握手；所有端点仍共享供应商账本。
        if self._http is None:
            self._http = SourceHTTP(self.policy, self.cache)
            self._http.client.headers['Referer'] = 'https://finance.sina.com.cn/'
        return await self._http.document('sina', url, 0 if self.force_read else seconds)

    async def close(self) -> None:
        """在任务结束、长等待或取消后释放连接，下次续跑重新建立会话。"""
        if self._http:
            await self._http.close()
            self._http = None

    def decode(self, raw: bytes) -> list[Any]:
        if self._decoder is None:
            self._decoder = SinaDecoder()
        return self._decoder.decode(raw)

    async def raw_daily(self, symbol: str) -> list[dict[str, Any]]:
        raw, _ = await self.document(f'https://finance.sina.com.cn/realstock/company/{symbol}/hisdata_klc2/klc_kl.js')
        rows = self.decode(raw)
        dates = [date.fromisoformat(str(row['date'])[:10]) for row in rows]
        if len(set(dates)) != len(dates) or dates != sorted(dates):
            raise DomainError('DATA_INCOMPLETE', '新浪日线日期重复或乱序。', 503)
        return rows

    def use_calendar(self, calendar: dict[str, dict[date, CalendarDay]], fetched_at: datetime) -> None:
        if calendar.get('SSE') and calendar.get('SSE') == calendar.get('SZSE'):
            self._calendar, self._calendar_at = dict(calendar['SSE']), fetched_at
            self.provenance = {**self.provenance, 'calendar': 'stored-confirmed-exchange-calendar'}

    async def history_calendar(self, target: date) -> tuple[dict[str, dict[date, CalendarDay]], datetime]:
        """交易日取自新浪日历，只有其覆盖范围内的其他日期才判定休市。"""
        raw, fetched = await self.document('https://finance.sina.com.cn/realstock/company/klc_td_sh.txt', 86400)
        opened = {date.fromisoformat(str(value)[:10]) for value in self.decode(raw)}
        # 原始响应可能覆盖三十多年；先准备最大保留窗口的真实日历，便于过滤范围外的记录。
        calendar_policy = HistoryPolicy(max(self.history_policy.initial_days, self.history_policy.max_days), self.history_policy.max_days)
        start = calendar_policy.calendar_start(target)
        if not opened or min(opened) > start or max(opened) < target:
            raise DomainError('TRADING_CALENDAR_UNAVAILABLE', '新浪真实日历未覆盖目标日期及历史范围。', 503)
        days = {}
        cursor = start
        while cursor <= max(opened):
            is_open = cursor in opened
            days[cursor] = CalendarDay(is_open, datetime.combine(cursor, time(15, 30), SHANGHAI) if is_open else None)
            cursor += timedelta(days=1)
        self.use_calendar({key: days for key in ('SSE', 'SZSE')}, fetched)
        self.provenance = {**self.provenance, 'calendar': 'sina'}
        return {key: days for key in ('SSE', 'SZSE')}, fetched

    async def directory(self, known: tuple[Stock, ...] = (), *, require_dates: bool = False) -> tuple[Stock, ...]:
        """逐页保存已核对目录；等待后从下一页继续，不受HTTP短期缓存过期影响。"""
        checkpoint = SourceCheckpoint(self.cache / 'sina-preparation')
        state = checkpoint.data
        if 'directoryTotal' not in state:
            raw, _ = await self.document(BASE + 'Market_Center.getHQNodeStockCount?node=hs_a')
            state.update(directoryTotal=int(assignment(raw)), directoryRows=[])
            checkpoint.save()
        total = state['directoryTotal']
        if not 1 <= total <= 100000:
            raise DomainError('DATA_INCOMPLETE', '新浪股票总数异常。', 503)
        prior = {stock.stock_id: stock for stock in known}
        accepted = state['directoryRows']
        seen = {row['symbol'] for row in accepted}
        for offset in range(len(accepted), total, 80):
            url = BASE + 'Market_Center.getHQNodeData?' + urlencode({'page': offset // 80 + 1, 'num': 80,
                'sort': 'symbol', 'asc': 1, 'node': 'hs_a', 'symbol': '', '_s_r_a': 'page'})
            raw, _ = await self.document(url)
            rows = assignment(raw)
            if not isinstance(rows, list) or len(rows) != min(80, total - offset):
                raise DomainError('DATA_INCOMPLETE', '新浪股票目录分页不完整。', 503)
            page = []
            for row in rows:
                symbol = row['symbol']
                if not re.fullmatch(r'(sh|sz|bj)[0-9]{6}', symbol) or symbol in seen or not row.get('name', '').strip():
                    raise DomainError('DATA_INCOMPLETE', '新浪股票目录代码无效或重复。', 503)
                seen.add(symbol)
                page.append(row)
            accepted.extend(page)
            checkpoint.save()
            report_progress(f'已核对新浪目录{min(offset + 80, total)}/{total}只证券。', 15, True)
        self.market_data.remember_rows(accepted)
        catalog = SinaCatalog(self.cache, self.document)
        result = []
        for row in accepted:
            symbol = row['symbol']
            if symbol.startswith('bj'):
                continue
            exchange = 'SSE' if symbol.startswith('sh') else 'SZSE'
            stock_id = f'{exchange}.{symbol[2:]}'
            old = prior.get(stock_id)
            prefix = symbol[2:5]
            board = old.board if old else ({'600': 'SSE_MAIN', '601': 'SSE_MAIN', '603': 'SSE_MAIN', '605': 'SSE_MAIN',
                '688': 'STAR', '689': 'STAR', '000': 'SZSE_MAIN', '001': 'SZSE_MAIN', '002': 'SZSE_MAIN',
                '003': 'SZSE_MAIN', '300': 'CHINEXT', '301': 'CHINEXT'}.get(prefix))
            if board is None:
                board = await catalog.board(symbol)
            if board not in self.boards:
                raise DomainError('DATA_INCOMPLETE', f'新浪证券{symbol}的板块尚未确认。', 503)
            listed = old.listed_on if old else None
            if require_dates or known and listed is None:
                listed = await catalog.listed_on(symbol, listed)
            result.append(Stock(stock_id, symbol[2:], row['name'], exchange, board,
                listed, old.delisted_on if old else None, symbol))
        if {stock.board for stock in result} != self.boards or len(seen) != total:
            raise DomainError('DATA_INCOMPLETE', '新浪目录未覆盖全部沪深板块。', 503)
        return tuple(sorted(result, key=lambda stock: stock.stock_id))

    async def initialize(self) -> Initialization:
        stocks = await self.directory(self._known, require_dates=True)
        calendar, fetched = await self.history_calendar(datetime.now(SHANGHAI).date())
        return Initialization(stocks, calendar, fetched)

    async def catalog(self, stocks: tuple[Stock, ...]) -> dict[str, Any]:
        raise DomainError('SOURCE_UNAVAILABLE', '行业与题材请选择百度分类渠道。')

    async def precheck(self, target: date) -> list[dict[str, Any]]:
        """先探测四板块日线日期，正式同步仍逐只核对目标日，不把抽样当作全量验收。"""
        samples = tuple(Stock(f'{"SSE" if symbol.startswith("sh") else "SZSE"}.{symbol[2:]}', symbol[2:], symbol,
            'SSE' if symbol.startswith('sh') else 'SZSE', board) for board, symbol in SAMPLES.items())
        await self.prepare_quotes(samples)
        checks = []
        for board, symbol in SAMPLES.items():
            quote = self._quotes.get(symbol, {})
            latest = date.fromisoformat(quote['date']) if quote.get('date') else None
            if latest is None or latest > target or not quote.get('time'):
                rows = await self.raw_daily(symbol)
                latest = max((date.fromisoformat(str(row['date'])[:10]) for row in rows
                    if str(row['date'])[:10] <= str(target) and decimal_value(row['volume']) > 0), default=None)
            elif latest == target and time.fromisoformat(quote['time']) < time(15):
                checks.append({'board': board, 'status': 'pending', 'latestDataDate': str(latest), 'message': '新浪尚未返回目标日收盘报价。'})
                continue
            checks.append({'board': board, 'status': 'ready' if latest == target else 'pending',
                'latestDataDate': str(latest) if latest else None,
                'message': '板块样本已就绪，随后逐只核对。' if latest == target else '新浪目标日收盘数据暂未就绪。'})
        return checks

    async def prepare_quotes(self, stocks: tuple[Stock, ...], *, checkpoint: SourceCheckpoint | None = None) -> None:
        """批量报价带完整日期，用于确认停牌/未就绪，不能只凭无日线推定停牌。"""
        start = checkpoint.data.get('quoteIndex', 0) if checkpoint else 0
        for offset in range(start, len(stocks), 100):
            symbols = [('sh' if s.exchange == 'SSE' else 'sz') + s.code for s in stocks[offset:offset + 100]]
            raw, _ = await self.document('https://hq.sinajs.cn/list=' + ','.join(symbols))
            parsed = quote_rows(raw, set(symbols))
            self._quotes.update(parsed)
            self.market_data.remember_quotes(parsed)
            self.cache.mkdir(parents=True, exist_ok=True)
            path = self.cache / 'sina-quotes.json'
            temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(self._quotes, ensure_ascii=False))
            temporary.replace(path)
            if checkpoint:
                count = min(offset + 100, len(stocks))
                checkpoint.data['quoteIndex'] = count
                checkpoint.save()
                report_progress(f'已取得新浪{count}/{len(stocks)}只股票的日期和交易状态。', 79, True)

    async def universe(self, target: date, known: tuple[Stock, ...]) -> tuple[Stock, ...]:
        if is_background_sync():
            return known
        stocks = tuple(stock for stock in await self.directory(known) if stock.listed_on is None or stock.listed_on <= target)
        checkpoint = SourceCheckpoint(self.cache / 'sina-preparation')
        await self.prepare_quotes(stocks, checkpoint=checkpoint)
        return stocks

    def target_cap(self, stock: Stock, target: date, close: Decimal) -> Decimal | None:
        return self.market_data.cap(stock, target, close)

    async def factors(self, symbol: str) -> list[tuple[date, Decimal | None]]:
        """按每次更新取得官方累计因子，不能假设昨日因子当天仍不变。"""
        raw, _ = await self.document(f'https://finance.sina.com.cn/realstock/company/{symbol}/hfq.js')
        payload = assignment(raw)
        return dated_values(payload.get('data', []) if isinstance(payload, dict) else [], 'd', 'f')

    async def incremental_packet(self, stock: Stock, target: date, history: tuple[Bar, ...]) -> DailyPacket | None:
        """连续历史采用批量当日报价，只刷新因子；日期缺口回查历史，不拼接其他价格源。"""
        symbol = ('sh' if stock.exchange == 'SSE' else 'sz') + stock.code
        quote = self._quotes.get(symbol, {})
        if (quote.get('date') != str(target) or not quote.get('time')) and not self.force_read:
            quote = self.market_data.closed_quote(symbol, target)
        required = {'open', 'high', 'low', 'close', 'amount', 'volume', 'date', 'time', 'previousClose'}
        if not required <= quote.keys() or quote['date'] != str(target) or time.fromisoformat(quote['time']) < time(15):
            return None
        prior = sorted((bar for bar in history if bar.day < target), key=lambda bar: bar.day)
        days = sorted(day for day, value in self._calendar.items() if value.is_open and day < target)
        if not prior or not days or prior[-1].day != days[-1]:
            return None
        volume = decimal_value(quote['volume'])
        if volume == 0:
            return DailyPacket(stock, StockState('suspended', 'ST' in quote['name'].upper(), '退' in quote['name']), ())
        if volume != int(volume):
            raise DomainError('DATA_INCOMPLETE', '新浪成交量不是整数股数。', 503)
        factors = await self.factors(symbol)
        current_factor = effective(factors, target)
        previous_factor = effective(factors, prior[-1].day)
        if current_factor is not None and previous_factor is not None and decimal_value(quote['previousClose']) != prior[-1].close and current_factor == previous_factor:
            raise SourceDeferred(datetime.now(UTC) + timedelta(minutes=10),
                f'新浪{stock.code}除权参考价已变化，累计复权因子尚未就绪。', reason='BOARD_DATA_NOT_READY')
        # 历史累计因子发生修订时，回查整个保留窗口；不能只修150日而让更早图表口径错位。
        def same_factor(value: Decimal | None, stored: Decimal | None) -> bool:
            # 数据库统一保存8位小数；按存储精度比较，不能把正常舍入识别为历史因子修订。
            return value == stored if value is None or stored is None else value.quantize(Decimal('.00000001'), rounding=ROUND_HALF_UP) == stored.quantize(Decimal('.00000001'), rounding=ROUND_HALF_UP)
        if any(not same_factor(effective(factors, bar.day), bar.factor) for bar in prior):
            self.history_revision = True
            return None
        close = decimal_value(quote['close'])
        try:
            bar = Bar(target, *(decimal_value(quote[key]) for key in ('open', 'high', 'low', 'close')),
                decimal_value(quote['amount']), int(volume), self.market_data.turnover(stock, target, close),
                self.target_cap(stock, target, close), current_factor)
        except (ValueError, ArithmeticError) as error:
            raise DomainError('SOURCE_PRICE_INVALID', f'新浪{stock.code}在{target}的报价数值不合法，已保留此前进度。', 503) from error
        return DailyPacket(stock, StockState('trading', 'ST' in quote['name'].upper(), '退' in quote['name']),
            (bar,), 'ADJUSTMENT_UNAVAILABLE' if current_factor is None else None)

    def packet_path(self, symbol: str, target: date) -> Path:
        return self.cache / 'sina-packets' / str(target) / (symbol + '.json')

    async def daily(self, stock: Stock, target: date, history: tuple[Bar, ...]) -> DailyPacket:
        """保存同源原始日线；累计因子由公共计算层锚定为前复权，不重复调整价格。"""
        symbol = ('sh' if stock.exchange == 'SSE' else 'sz') + stock.code
        self.history_revision = False
        path = self.packet_path(symbol, target)
        if path.exists() and not self.force_read:
            saved = json.loads(path.read_text())
            dates = sorted(day for day, entry in self._calendar.items() if entry.is_open and day <= target)
            floor = self.history_policy.initial_dates(dates, target)[0]
            rows = saved['bars']
            complete = saved['retentionDays'] == 0 or saved['retentionDays'] >= self.history_policy.initial_days
            if complete or not rows or rows[0][0] <= str(floor) or stock.listed_on and stock.listed_on >= floor:
                bars = tuple(Bar(date.fromisoformat(row[0]), *(decimal_value(v) if v is not None else None for v in row[1:6]),
                    row[6], *(decimal_value(v) if v is not None else None for v in row[7:])) for row in rows if row[0] >= str(floor))
                return self.select_packet(stock, target, bars, StockState(**saved['state']), saved.get('excludedReason'))
        if not is_background_sync() and (packet := await self.incremental_packet(stock, target, history)) is not None:
            return packet
        dates = sorted(day for day, entry in self._calendar.items() if entry.is_open and day <= target)
        if self.history_policy.max_days and len(dates) < self.history_policy.max_days:
            await self.history_calendar(target)
            dates = sorted(day for day, entry in self._calendar.items() if entry.is_open and day <= target)
        floor = self.history_policy.cutoff(dates, target)
        rows = await self.raw_daily(symbol)
        selected = [row for row in rows if (floor is None or str(row['date'])[:10] >= str(floor))
            and str(row['date'])[:10] <= str(target) and decimal_value(row['volume']) > 0]
        current = next((row for row in selected if str(row['date'])[:10] == str(target)), None)
        quote = self._quotes.get(symbol)
        # 任务恢复后从共享HTTP缓存恢复这一只报价，不依赖进程内状态。
        if quote is None:
            await self.prepare_quotes((stock,))
            quote = self._quotes.get(symbol)
        if current is None and (not quote or quote['date'] != str(target) or decimal_value(quote['volume']) != 0):
            raise SourceDeferred(datetime.now(UTC) + timedelta(minutes=10),
                f'新浪{stock.code}的{target}行情暂未就绪，已保存此前进度。', reason='BOARD_DATA_NOT_READY')
        factors = await self.factors(symbol)
        raw, _ = await self.document('https://stock.finance.sina.com.cn/stock/api/jsonp.php/var%20KKE_ShareAmount_' + symbol +
            '=/StockService.getAmountBySymbol?_=20&symbol=' + symbol)
        shares_data = assignment(raw)
        shares = dated_values(shares_data or [], 'date', 'amount', 10000)
        bars_list = []
        for row in selected:
            day = date.fromisoformat(str(row['date'])[:10])
            volume = decimal_value(row['volume'])
            if volume != int(volume):
                raise DomainError('DATA_INCOMPLETE', '新浪成交量不是整数股数。', 503)
            float_shares = effective(shares, day)
            close = decimal_value(row['close'])
            cap = self.target_cap(stock, day, close) if day == target else None
            try:
                bars_list.append(Bar(day, *(decimal_value(row[key]) for key in ('open', 'high', 'low', 'close')),
                    decimal_value(row['amount']), int(volume), volume / float_shares * 100 if float_shares else None,
                    cap, effective(factors, day)))
            except (ValueError, ArithmeticError) as error:
                raise DomainError('SOURCE_PRICE_INVALID', f'新浪{stock.code}在{day}的行情数值不合法，已保存此前进度；未修改源价格。', 503) from error
        # 保留完整的最大配置窗口供后续回补；范围外原文仍由HTTP缓存留存，不改写历史证据。
        # 缺失因子的股票只保留原始资料并排除策略，不能伪造因子1或阻断全市场。
        excluded = 'ADJUSTMENT_UNAVAILABLE' if current and (not bars_list or bars_list[-1].factor is None) else None
        verified_name = quote['name'] if quote and quote['date'] == str(target) else None
        state = StockState('trading' if current else None,
            'ST' in verified_name.upper() if verified_name else None, '退' in verified_name if verified_name else None)
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = {'state': {'trade_status': state.trade_status, 'is_st': state.is_st, 'is_delisting': state.is_delisting},
            'retentionDays': self.history_policy.max_days,
            'excludedReason': excluded,
            'bars': [[str(b.day), *(str(v) if v is not None else None for v in (b.open, b.high, b.low, b.close, b.amount)),
                      b.volume, *(str(v) if v is not None else None for v in (b.turnover, None, b.factor))] for b in bars_list]}
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(saved, ensure_ascii=False))
        temporary.replace(path)
        return self.select_packet(stock, target, tuple(bars_list), state, excluded)

    def select_packet(self, stock: Stock, target: date, bars: tuple[Bar, ...], state: StockState, excluded: str | None) -> DailyPacket:
        """按本次冻结的历史窗口选择数据，补充已核验的同日元资料，不改变原始价格。"""
        dates = sorted(day for day, entry in self._calendar.items() if entry.is_open and day <= target)
        selected = self.history_policy.initial_dates(dates, target)
        floor = min(selected)
        selected_bars = tuple(bar for bar in bars if (self.history_revision or floor <= bar.day) and bar.day <= target)
        selected_bars = tuple(replace(bar, total_market_cap=self.target_cap(stock, target, bar.close),
            turnover=self.market_data.turnover(stock, target, bar.close) or bar.turnover)
            if bar.day == target else bar for bar in selected_bars)
        symbol = ('sh' if stock.exchange == 'SSE' else 'sz') + stock.code
        quote = self._quotes.get(symbol)
        if quote and quote['date'] == str(target):
            state = replace(state, is_st='ST' in quote['name'].upper(), is_delisting='退' in quote['name'])
        return DailyPacket(stock, state, selected_bars, excluded)
