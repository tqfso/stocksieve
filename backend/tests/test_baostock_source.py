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
