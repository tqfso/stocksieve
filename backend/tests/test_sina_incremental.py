"""本文件验证新浪日常增量只读取官方因子，历史缺口和修订回查，旧来源不混入新快照。"""

import asyncio
import json
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from stocksieve.modules.ingestion.application.ports import CalendarDay, SourceDeferred
from stocksieve.modules.ingestion.infrastructure.providers.sina import SinaSource
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.domain.models import Bar, Stock

TARGET = date(2026, 11, 3)
STOCK = Stock('SSE.600000', '600000', '样本', 'SSE', 'SSE_MAIN', date(1999, 11, 10))


def prepared(tmp_path: Path) -> tuple[SinaSource, tuple[Bar, ...], list[str]]:
    source = SinaSource(SourcePolicy(tmp_path / 'rate'), tmp_path / 'cache', 150, 2500)
    source._calendar = {TARGET - timedelta(days=i): CalendarDay(True) for i in range(3000)}
    history = tuple(Bar(TARGET - timedelta(days=i), D(10), D(11), D(9), D(10), D(10000), 1000, D(2), None, D(1)) for i in range(150, 0, -1))
    quote = {'name': '样本', 'date': str(TARGET), 'time': '15:00:03', 'open': '10', 'high': '11', 'low': '9',
        'close': '10.5', 'previousClose': '10', 'volume': '1000', 'amount': '10500'}
    source.market_data.remember_rows([{'symbol': 'sh600000', 'open': '10', 'high': '11', 'low': '9', 'trade': '10.5',
        'volume': 1000, 'amount': 10500, 'mktcap': 1000000, 'nmc': 500000, 'turnoverratio': 2.5}])
    source.market_data.remember_quotes({'sh600000': quote})
    calls = []

    async def document(url: str, seconds: int = 600) -> tuple[bytes, None]:
        calls.append(url)
        assert url.endswith('/hfq.js'), '连续更新不应下载历史日线或历史股本'
        return json.dumps({'data': [{'d': '1990-01-01', 'f': '1'}]}).encode(), None

    source.document = document
    return source, history, calls


def test_contiguous_update_reads_only_factors_and_uses_dated_native_metadata(tmp_path: Path) -> None:
    source, history, calls = prepared(tmp_path)
    packet = asyncio.run(source.daily(STOCK, TARGET, history))
    assert len(calls) == 1 and len(packet.bars) == 1
    bar = packet.bars[0]
    assert bar.day == TARGET and bar.close == D('10.5') and bar.factor == 1
    assert bar.total_market_cap == D(10000000000) and bar.turnover == D('2.5')


def test_sina_native_works_without_tencent_calls(tmp_path: Path) -> None:
    source, history, _ = prepared(tmp_path)
    class Forbidden:
        def cap(self, *args: object) -> None:
            pytest.fail('新浪新任务不得使用腾讯市值')
    source.metadata = Forbidden()
    assert asyncio.run(source.daily(STOCK, TARGET, history)).bars[-1].total_market_cap == D(10000000000)


def test_missing_date_or_intraday_quote_requires_history_instead_of_guessing(tmp_path: Path) -> None:
    source, history, calls = prepared(tmp_path)
    assert asyncio.run(source.incremental_packet(STOCK, TARGET, history[:-1])) is None
    source._quotes['sh600000']['time'] = '14:59:59'
    assert asyncio.run(source.incremental_packet(STOCK, TARGET, history)) is None
    assert calls == []


def test_corporate_action_waits_until_official_factor_is_updated(tmp_path: Path) -> None:
    source, history, _ = prepared(tmp_path)
    source._quotes['sh600000']['previousClose'] = '5'
    with pytest.raises(SourceDeferred) as error:
        asyncio.run(source.incremental_packet(STOCK, TARGET, history))
    assert error.value.reason == 'BOARD_DATA_NOT_READY'


def test_new_ex_rights_factor_is_used_without_rewriting_old_raw_prices(tmp_path: Path) -> None:
    source, history, _ = prepared(tmp_path)
    source._quotes['sh600000'].update(previousClose='5', close='5.2', open='5', high='6', low='4')
    async def factors(symbol: str) -> list[tuple[date, D]]:
        return [(date(1990, 1, 1), D(1)), (TARGET, D(2))]
    source.factors = factors
    packet = asyncio.run(source.incremental_packet(STOCK, TARGET, history))
    assert packet is not None and packet.bars[-1].factor == 2 and packet.bars[-1].close == D('5.2')
    assert history[-1].close == 10 and history[-1].factor == 1


def test_historical_factor_revision_requests_full_retained_window(tmp_path: Path) -> None:
    source, history, _ = prepared(tmp_path)
    async def factors(symbol: str) -> list[tuple[date, D]]:
        return [(date(1990, 1, 1), D(3))]
    source.factors = factors
    assert asyncio.run(source.incremental_packet(STOCK, TARGET, history)) is None
    assert source.history_revision


def test_database_decimal_rounding_does_not_trigger_history_reload(tmp_path: Path) -> None:
    from dataclasses import replace
    source, history, _ = prepared(tmp_path)
    factor = D('17.3916341988312000')
    history = tuple(replace(bar, factor=factor.quantize(D('.000000000001'))) for bar in history)
    async def factors(symbol: str) -> list[tuple[date, D]]:
        return [(date(1990, 1, 1), factor)]
    source.factors = factors
    assert asyncio.run(source.incremental_packet(STOCK, TARGET, history)) is not None
    assert not source.history_revision


def test_legacy_snapshot_keeps_its_declared_cap_source(tmp_path: Path) -> None:
    source, _, _ = prepared(tmp_path)
    class Legacy:
        def cap(self, *args: object) -> D:
            return D(9000000000)
    source.metadata = Legacy()
    legacy = source.for_snapshot({'totalMarketCap': 'tencent'})
    assert legacy.legacy_cap and legacy.target_cap(STOCK, TARGET, D('10.5')) == D(9000000000)
    assert source.target_cap(STOCK, TARGET, D('10.5')) == D(10000000000)
