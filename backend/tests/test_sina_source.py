"""本文件验证新浪价格口径、历史股本、缺失数据、就绪日期与断点缓存，不访问上游。"""

import asyncio
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from stocksieve.modules.ingestion.application.ports import CalendarDay, SourceDeferred
from stocksieve.modules.ingestion.infrastructure.providers.sina import (
    SinaSource,
    dated_values,
    effective,
)
from stocksieve.modules.ingestion.infrastructure.providers.sina_codec import assignment
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.domain.history import HistoryPolicy
from stocksieve.modules.market.domain.models import Stock, adjusted_history
from stocksieve.shared.errors import DomainError

TARGET = date(2026, 11, 2)
STOCK = Stock('SSE.600000', '600000', '浦发银行', 'SSE', 'SSE_MAIN')


def source_fixture(tmp_path: Path, *, missing_factor: bool = False) -> SinaSource:
    source = SinaSource(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache', 150, 2500)
    source._calendar = {TARGET - timedelta(days=n): CalendarDay(True) for n in range(3000)}
    source._quotes['sh600000'] = {'date': str(TARGET), 'name': '浦发银行', 'volume': '1000', 'open': '5'}

    async def daily(symbol: str) -> list[dict]:
        return [{'date': str(TARGET - timedelta(days=1)), 'open': 10, 'high': 10, 'low': 10, 'close': 10, 'amount': 10000, 'volume': 1000},
                {'date': str(TARGET), 'open': 5, 'high': 5, 'low': 5, 'close': 5, 'amount': 5000, 'volume': 1000}]

    async def document(url: str, seconds: int = 600) -> tuple[bytes, None]:
        if 'hfq.js' in url:
            rows = [] if missing_factor else [{'d': '1990-01-01', 'f': '1'}, {'d': str(TARGET), 'f': '2'}]
            return ('var x=' + json.dumps({'data': rows}) + '; // other = content').encode(), None
        return ('/* ignored = text */ var x=(' + json.dumps([{'date': '1990-01-01', 'amount': '10'},
            {'date': str(TARGET), 'amount': '20'}]) + ');').encode(), None

    source.raw_daily, source.document = daily, document
    return source


def test_jsonp_reads_value_without_executing_remote_script() -> None:
    assert assignment(b'/* comment = ignored */var x=({"data":[]});alert("never execute");') == {'data': []}
    assert assignment(b'var x=(null);') is None
    with pytest.raises(ValueError):
        assignment(b'var x={"factor":NaN};')


def test_raw_prices_cumulative_factors_and_historical_share_units(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)
    packet = asyncio.run(source.daily(STOCK, TARGET, ()))
    assert [bar.close for bar in packet.bars] == [D(10), D(5)]
    assert [bar.factor for bar in packet.bars] == [D(1), D(2)]
    assert [bar.close for bar in adjusted_history(list(packet.bars), TARGET)] == [D(5), D(5)]
    assert [bar.turnover for bar in packet.bars] == [D(1), D('.5')]
    assert packet.state.is_st is False and packet.state.is_delisting is False


def test_invalid_effective_shares_do_not_reuse_previous_value() -> None:
    rows = dated_values([{'date': '2026-10-29', 'amount': 10}, {'date': '2026-10-30', 'amount': 0},
                         {'date': '2026-11-02', 'amount': 20}], 'date', 'amount', 10000)
    assert effective(rows, date(2026, 10, 28)) is None
    assert effective(rows, date(2026, 10, 29)) == 100000
    assert effective(rows, date(2026, 10, 31)) is None
    assert effective(rows, TARGET) == 200000


def test_missing_factors_are_explicit_and_never_replaced_with_one(tmp_path: Path) -> None:
    packet = asyncio.run(source_fixture(tmp_path, missing_factor=True).daily(STOCK, TARGET, ()))
    assert all(bar.factor is None for bar in packet.bars)
    assert packet.excluded_reason == 'ADJUSTMENT_UNAVAILABLE'


def test_backfill_reuses_successful_packet_after_process_restart(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)
    first = asyncio.run(source.daily(STOCK, TARGET, ()))
    restored = SinaSource(source.policy, source.cache).for_history(HistoryPolicy(2500, 2500))
    restored._calendar = source._calendar

    async def forbidden(*args: object) -> None:
        pytest.fail('缓存回补不应重复向上游取数')

    restored.raw_daily = forbidden
    second = asyncio.run(restored.daily(STOCK, TARGET, ()))
    assert first == second


def test_recent_window_does_not_truncate_cached_history_for_later_backfill(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)
    original = source.raw_daily

    async def longer(symbol: str) -> list[dict]:
        rows = await original(symbol)
        return [{**rows[0], 'date': str(TARGET - timedelta(days=300))}, *rows]

    source.raw_daily = longer
    recent = asyncio.run(source.daily(STOCK, TARGET, ()))
    assert len(recent.bars) == 2
    archive = source.for_history(HistoryPolicy(2500, 2500))
    full = asyncio.run(archive.daily(STOCK, TARGET, ()))
    assert len(full.bars) == 3 and full.bars[0].day == TARGET - timedelta(days=300)


def test_old_or_future_quotes_do_not_imply_suspension(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)
    source._quotes['sh600000']['date'] = '2026-10-30'
    original = source.raw_daily

    async def stale(symbol: str) -> list[dict]:
        return (await original(symbol))[:1]

    source.raw_daily = stale
    with pytest.raises(SourceDeferred) as error:
        asyncio.run(source.daily(STOCK, TARGET, ()))
    assert error.value.reason == 'BOARD_DATA_NOT_READY'
    assert not source.packet_path('sh600000', TARGET).exists()


def test_precheck_uses_actual_target_date_and_never_future_bar(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)

    async def raw(symbol: str) -> list[dict]:
        return [{'date': '2026-10-30', 'volume': 1000}, {'date': '2026-11-03', 'volume': 1000}]

    source.raw_daily = raw
    checks = asyncio.run(source.precheck(TARGET))
    assert all(row['status'] == 'pending' and row['latestDataDate'] == '2026-10-30' for row in checks)


def test_unverified_historical_name_does_not_assert_normal_st_status(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)
    source._quotes['sh600000']['date'] = '2026-11-03'
    packet = asyncio.run(source.daily(STOCK, TARGET, ()))
    assert packet.state.is_st is None and packet.state.is_delisting is None


def test_missing_adjustment_excludes_stock_but_keeps_other_market_results(context) -> None:
    context.submit('initialize'); context.run()
    original = context.source.daily

    async def partial(stock: Stock, target: date, history: tuple) -> object:
        packet = await original(stock, target, history)
        if stock == context.source.universe_stocks[0]:
            return replace(packet, bars=tuple(replace(bar, factor=None) for bar in packet.bars), excluded_reason='ADJUSTMENT_UNAVAILABLE')
        return packet

    context.source.daily = partial
    job = context.submit('daily'); context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'succeeded'
    result = context.request('GET', '/api/v1/opportunities?strategy=short')[1]['data']
    assert result['total'] == 4


def test_new_source_jobs_do_not_inherit_price_history(context) -> None:
    first = context.published()
    context.source.id = 'sina'
    context.api.registry.register(context.source)
    original = context.source.daily
    seen = []

    async def independent(stock: Stock, target: date, history: tuple) -> object:
        seen.append(history)
        return await original(stock, target, history)

    context.source.daily = independent
    status, response, _ = context.request('POST', '/api/v1/admin/sync-jobs', {'kind': 'daily', 'source': 'sina'}, key='sina-switch')
    assert status == 202, response
    context.run()
    assert all(not history for history in seen)
    old = context.request('GET', f'/api/v1/opportunities?strategy=short&snapshotId={first}')[1]['data']
    assert old['total'] == 5


def test_stock_not_ready_wait_has_deadline_and_keeps_published_snapshot(context) -> None:
    first = context.published()

    async def pending(*args: object) -> object:
        raise SourceDeferred(context.clock.now() + timedelta(minutes=10), '个股目标日未就绪', reason='BOARD_DATA_NOT_READY')

    context.source.daily = pending
    job = context.submit('daily'); context.run()
    waiting = context.api.tasks.detail(job, 'sync')
    assert waiting['status'] == 'queued' and waiting['phase'] == 'waiting_data'
    assert waiting['waitReason'] == 'BOARD_DATA_NOT_READY' and waiting['retryDeadlineAt'] is not None
    assert context.request('GET', '/api/v1/market/status')[1]['data']['publishedSnapshot']['id'] == first
    context.clock.advance(4 * 3600 + 1); context.run()
    assert context.api.tasks.detail(job, 'sync')['error']['code'] == 'DATA_READY_TIMEOUT'


def test_old_invalid_price_outside_retention_does_not_block_current_day(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)
    original = source.raw_daily

    async def with_old_error(symbol: str) -> list[dict]:
        # 新浪600690的真实历史异常形状：1994年开盘6.06高于最高6.00。
        return [{'date': '1994-04-04', 'open': 6.06, 'high': 6, 'low': 5.61, 'close': 5.7,
                 'volume': 353400, 'amount': 2043196}, *(await original(symbol))]

    source.raw_daily = with_old_error
    packet = asyncio.run(source.daily(STOCK, TARGET, ()))
    assert len(packet.bars) == 2 and packet.bars[-1].day == TARGET
    saved = json.loads(source.packet_path('sh600000', TARGET).read_text())
    assert all(row[0] != '1994-04-04' for row in saved['bars'])


def test_invalid_price_inside_retention_is_reported_without_inventing_prices(tmp_path: Path) -> None:
    source = source_fixture(tmp_path)
    original = source.raw_daily

    async def with_recent_error(symbol: str) -> list[dict]:
        rows = await original(symbol)
        rows[-1]['high'] = 4
        return rows

    source.raw_daily = with_recent_error
    with pytest.raises(DomainError) as error:
        asyncio.run(source.daily(STOCK, TARGET, ()))
    assert error.value.code == 'SOURCE_PRICE_INVALID'
    assert STOCK.code in error.value.message and str(TARGET) in error.value.message
    assert not source.packet_path('sh600000', TARGET).exists()
