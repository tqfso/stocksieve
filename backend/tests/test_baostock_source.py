"""本文件验证相对复权、跨快照对齐、停牌和明确的新增板块标签，不访问上游。"""

import asyncio
import json
import sqlite3
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
from stocksieve.modules.ingestion.infrastructure.providers.baostock import (
    BaostockSource,
    security,
)
from stocksieve.modules.market.domain.models import Bar, adjusted_history
from stocksieve.shared.errors import DomainError


def make_source(tmp_path: Path) -> BaostockSource:
    return BaostockSource(SimpleNamespace(policy=SimpleNamespace(directory=tmp_path)), tmp_path / 'market.sqlite')


def quote(day: str, close: str, preclose: str, state: str = '1') -> dict[str, str]:
    value = D(close)
    return {'date': day, 'code': 'sh.600000', 'open': close, 'high': str(value + 1), 'low': str(value - 1), 'close': close, 'preclose': preclose,
            'volume': '100', 'amount': '1000', 'turn': '1', 'tradestatus': state, 'isST': '0', 'adjustflag': '3'}


def test_relative_factors_preserve_split_and_align_with_previous_snapshot(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    stock = security('sh.600000', '测试股票')
    rows = [quote('2026-10-29', '10', '10'), quote('2026-10-30', '5', '5')]
    with sqlite3.connect(source.cache) as connection:
        connection.executemany('INSERT INTO raw VALUES (?,?,?)', [('sh.600000', row['date'], json.dumps(row)) for row in rows])
    first = asyncio.run(source.daily(stock, date(2026, 10, 30), ()))
    assert [bar.factor for bar in first.bars] == [D(1), D(2)]
    assert [bar.close for bar in adjusted_history(list(first.bars), date(2026, 10, 30))] == [D(5), D(5)]
    previous = Bar(date(2026, 10, 29), D(10), D(11), D(9), D(10), D(1000), 100, D(1), None, D(3))
    next_packet = asyncio.run(source.daily(stock, date(2026, 10, 30), (previous,)))
    assert len(next_packet.bars) == 1 and next_packet.bars[0].factor == D(6)
    assert next_packet.state.trade_status == 'trading'


def test_unknown_security_prefix_needs_explicit_upstream_board(tmp_path: Path) -> None:
    with pytest.raises(DomainError):
        security('sz.302132', '测试股票')
    stock = security('sz.302132', '测试股票', board_override='CHINEXT')
    assert stock.board == 'CHINEXT' and stock.source_code == 'sz.302132'


def test_suspension_does_not_invent_a_candle(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    row = {**quote('2026-10-30', '0', '0', '0'), 'high': '0', 'low': '0'}
    with sqlite3.connect(source.cache) as connection:
        connection.execute('INSERT INTO raw VALUES (?,?,?)', ('sh.600000', row['date'], json.dumps(row)))
    packet = asyncio.run(source.daily(security('sh.600000', '测试股票'), date(2026, 10, 30), ()))
    assert packet.bars == () and packet.state.trade_status == 'suspended'


def test_missing_suspended_volume_and_amount_remain_unknown(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    row = {**quote('2026-10-30', '10', '10', '0'), 'amount': '', 'volume': ''}
    with sqlite3.connect(source.cache) as connection:
        connection.execute('INSERT INTO raw VALUES (?,?,?)', ('sh.600000', row['date'], json.dumps(row)))
    packet = asyncio.run(source.daily(security('sh.600000', '测试股票'), date(2026, 10, 30), ()))
    assert packet.bars[0].amount is None and packet.bars[0].volume is None
    assert packet.state.trade_status == 'suspended'


def test_history_requires_only_boards_already_listed_at_that_date(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    source.directory = lambda: [
        {'type': '1', 'code': code, 'code_name': code, 'ipoDate': listed, 'outDate': ''}
        for code, listed in [('sh.600000', '1999-11-10'), ('sz.000001', '1991-04-03'),
                             ('sz.300001', '2009-10-30'), ('sh.688001', '2019-07-22')]]
    rows = [{**quote('2016-10-31', '10', '10'), 'code': code}
            for code in ('sh.600000', 'sz.000001', 'sz.300001')]
    source.wire.sdk = SimpleNamespace(query_daily_history_k_AStock=lambda day: rows)
    source.read = lambda result: result
    assert source.expected_boards(date(2016, 10, 31)) == {'SSE_MAIN', 'SZSE_MAIN', 'CHINEXT'}
    assert len(source.load_day(date(2016, 10, 31))) == 3
    rows[:] = [{**row, 'date': '2026-10-30'} for row in rows]
    with pytest.raises(DomainError):
        source.load_day(date(2026, 10, 30))


def test_historical_backfill_keeps_verified_market_cap_when_live_quote_is_unavailable(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    source.metadata = SimpleNamespace(cap=lambda *args: None)
    row = quote('2026-10-30', '10', '10')
    with sqlite3.connect(source.cache) as connection:
        connection.execute('INSERT INTO raw VALUES (?,?,?)', ('sh.600000', row['date'], json.dumps(row)))
    prior = Bar(date(2026, 10, 30), D(10), D(11), D(9), D(10), D(1000), 100, D(1), D(1000000000), D(1))
    packet = asyncio.run(source.daily(security('sh.600000', '测试股票'), date(2026, 10, 30), (prior,)))
    assert packet.bars == ()


def readiness_source(tmp_path: Path) -> tuple[BaostockSource, list[dict[str, str]], list[date]]:
    """每板块准备正常交易证券，支持模拟分批到齐，完全不访问真实厂商。"""
    source = make_source(tmp_path)
    codes = ('sh.600000', 'sh.600001', 'sz.000001', 'sz.300001', 'sh.688001')
    stocks = tuple(security(code, code) for code in codes)
    source.stocks = lambda target: stocks
    source.expected_boards = lambda target: source.boards
    rows: list[dict[str, str]] = []
    discarded: list[date] = []
    source.wire.sdk = SimpleNamespace(query_daily_history_k_AStock=lambda day: rows)
    source.wire.discard_daily = discarded.append
    source.read = lambda result: result
    return source, rows, discarded


def test_empty_and_previous_day_data_wait_without_being_cached(tmp_path: Path) -> None:
    source, rows, discarded = readiness_source(tmp_path)
    target = date(2026, 11, 2)
    checks = asyncio.run(source.precheck(target))
    assert all(item['status'] == 'pending' and item['latestDataDate'] is None for item in checks)
    rows[:] = [{**quote('2026-10-30', '10', '10'), 'code': code} for code in ('sh.600000', 'sh.600001', 'sz.000001', 'sz.300001', 'sh.688001')]
    checks = asyncio.run(source.precheck(target))
    assert all(item['status'] == 'pending' and item['latestDataDate'] == '2026-10-30' for item in checks)
    with sqlite3.connect(source.cache) as connection:
        assert connection.execute('SELECT count(*) FROM days').fetchone()[0] == 0
    assert len(discarded) == 2
    rows[:] = [{**row, 'date': str(target)} for row in rows]
    assert all(item['status'] == 'ready' for item in asyncio.run(source.precheck(target)))
    with sqlite3.connect(source.cache) as connection:
        assert connection.execute('SELECT count(*) FROM days').fetchone()[0] == 1


def test_one_sample_per_board_does_not_hide_missing_securities(tmp_path: Path) -> None:
    source, rows, _ = readiness_source(tmp_path)
    target = date(2026, 11, 2)
    rows[:] = [{**quote(str(target), '10', '10'), 'code': code} for code in ('sh.600000', 'sz.000001', 'sz.300001', 'sh.688001')]
    checks = {row['board']: row for row in asyncio.run(source.precheck(target))}
    assert checks['SSE_MAIN']['status'] == 'pending' and '1只' in checks['SSE_MAIN']['message']
    assert all(checks[board]['status'] == 'ready' for board in ('SZSE_MAIN', 'CHINEXT', 'STAR'))
    rows.append({**quote(str(target), '10', '10'), 'code': 'sh.600001'})
    assert all(row['status'] == 'ready' for row in asyncio.run(source.precheck(target)))


def test_suspension_is_coverage_but_missing_trade_prices_are_not_ready(tmp_path: Path) -> None:
    source, rows, _ = readiness_source(tmp_path)
    target = date(2026, 11, 2)
    rows[:] = [{**quote(str(target), '10', '10'), 'code': code} for code in ('sh.600000', 'sh.600001', 'sz.000001', 'sz.300001', 'sh.688001')]
    rows[-1] = {**rows[-1], 'close': ''}
    assert next(item for item in asyncio.run(source.precheck(target)) if item['board'] == 'STAR')['status'] == 'pending'
    rows[-1]['tradestatus'] = '0'
    assert all(item['status'] == 'ready' for item in asyncio.run(source.precheck(target)))


def test_bad_future_response_remains_data_error_instead_of_ready(tmp_path: Path) -> None:
    source, rows, _ = readiness_source(tmp_path)
    rows.append(quote('2026-11-03', '10', '10'))
    with pytest.raises(DomainError) as error:
        asyncio.run(source.precheck(date(2026, 11, 2)))
    assert error.value.code == 'DATA_INCOMPLETE'
