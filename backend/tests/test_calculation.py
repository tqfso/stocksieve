"""本文件验证策略边界、未来数据隔离、复权、周期均线和财务/筹码计算的业务不变量。"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from stocksieve.analysis_math import chip_distribution, financial_metrics
from stocksieve.candles import candles
from stocksieve.domain import Bar, StockState, adjusted_history
from stocksieve.errors import MissingData
from stocksieve.strategies import evaluate


def series(prices, amounts=None):
    start = date(2026, 1, 1)
    result = []
    for index, price in enumerate(prices):
        price = D(str(price))
        result.append(Bar(start + timedelta(days=index), price, price + D('.02'), price - D('.02'), price,
                          D(str(amounts[index])) if amounts else D(50_000_000), 100000, D(2), D(1_000_000_000), D(1)))
    return result


def selected(bars, *, days=None, state=None):
    return evaluate(bars, days or [bar.day for bar in bars], bars[-1].day, state or StockState('trading', False, False))


def test_each_strategy_has_positive_case_and_waiting_exclusions():
    short = series([10] * 20 + [10.2], [50_000_000] * 20 + [75_000_000])
    assert 'short' in {row['strategy'] for row in selected(short).signals}
    long = series([D(10) + D(index) / 50 for index in range(150)])
    assert 'long' in {row['strategy'] for row in selected(long).signals}
    swing = series([D(10) + D(index) / 100 for index in range(144)] + [11.34, 11.33, 11.32, 11.34, 11.35, 11.50],
                   [100_000_000] * 144 + [50_000_000] * 5 + [70_000_000])
    assert 'swing' in {row['strategy'] for row in selected(swing).signals}
    trend = series([D(8) + D(index) / 65 for index in range(130)] + [9.95] * 19 + [9.98], [80_000_000] * 145 + [40_000_000] * 5)
    trend_codes = {row['strategy'] for row in selected(trend).signals}
    assert 'trend_wait' in trend_codes and not trend_codes & {'short', 'base_wait'}
    base = series([D(50) - D(index) / 3 for index in range(120)] + [10] * 30, [80_000_000] * 145 + [40_000_000] * 5)
    base_codes = {row['strategy'] for row in selected(base).signals}
    assert 'base_wait' in base_codes and not base_codes & {'short', 'swing', 'long', 'trend_wait'}


def test_short_boundaries_do_not_include_today_high_or_fill_gaps():
    records = series([10] * 20 + [10.2], [50_000_000] * 20 + [75_000_000])
    records[-1] = replace(records[-1], high=D(100))
    result = selected(records).signals[0]
    assert result['metrics']['breakoutReference'] == 10.02
    assert result['metrics']['amountMultiple'] == 1.5
    records[-1] = replace(records[-1], close=D('10.02'), low=D(10))
    assert not selected(records).signals
    records[-1] = replace(records[-1], close=D('10.2'), amount=D(74_999_999))
    assert not selected(records).signals
    complete = series([10] * 40 + [10.2], [50_000_000] * 40 + [75_000_000])
    missing = complete[:25] + complete[26:]
    assert not selected(missing, days=[bar.day for bar in complete]).signals
    early_unknown = [replace(complete[0], factor=None), *complete[1:]]
    assert 'short' in {row['strategy'] for row in selected(early_unknown).signals}


@pytest.mark.parametrize('state', [StockState('suspended', False, False), StockState('trading', True, False), StockState('trading', False, None)])
def test_common_state_exclusion(state):
    records = series([10] * 20 + [10.2], [50_000_000] * 20 + [75_000_000])
    assert not selected(records, state=state).signals


def test_front_adjustment_preserves_raw_units_and_future_is_ignored():
    records = series([100, 50])
    records[0] = replace(records[0], factor=D(1))
    records[1] = replace(records[1], factor=D(2))
    output = adjusted_history(records, records[-1].day)
    assert output[0].close == output[1].close == 50
    assert output[0].amount == records[0].amount and output[0].volume == records[0].volume
    future = replace(records[1], day=records[1].day + timedelta(days=1), close=D(50), factor=D(8))
    assert adjusted_history([*records, future], records[1].day) == output


def test_candle_periods_gaps_and_pagination():
    records = series([10 + index for index in range(25)])
    calendar = {bar.day: True for bar in records}
    data = candles(records, calendar, records[-1].day, limit=2)
    assert data['items'][-1]['ma20'] == sum(range(15, 35)) / 20
    assert data['nextBefore'] == records[-2].day.isoformat()
    missing = records[:15] + records[16:]
    assert candles(missing, calendar, records[-1].day)['items'][-1]['ma20'] is None
    weekly = candles(records, calendar, records[-1].day, 'week')
    assert weekly['items'][-1]['isComplete'] is True
    assert candles(records[:23], calendar, records[22].day, 'week')['items'][-1]['isComplete'] is False
    assert weekly['items'][0]['volume'] > records[0].volume
    year = candles(records, calendar, records[-1].day, 'year')
    assert len(year['items']) == 1 and year['items'][0]['ma5'] is None
    assert year['items'][0]['isComplete'] is False


def test_financial_ttm_uses_cumulative_difference_and_pe_never_negative():
    def report(identifier, end, revenue, profit):
        return {'id': identifier, 'period_start': date(end.year, 1, 1), 'period_end': end, 'statement_scope': 'consolidated',
                'revenue_ytd': D(revenue), 'net_profit_parent_ytd': D(profit), 'operating_cashflow_ytd': None, 'extra_json': {'currency': 'CNY'}}
    rows = [report('current', date(2026, 9, 30), 120, 15), report('annual', date(2025, 12, 31), 150, 20), report('prior', date(2025, 9, 30), 100, 10)]
    value = financial_metrics(rows, D(500))
    assert value['revenueTtm'] == 170 and value['netProfitParentTtm'] == 25 and value['peTtm'] == 20
    assert value['operatingCashflowTtm'] is None
    rows[0]['net_profit_parent_ytd'] = D(-20)
    assert financial_metrics(rows, D(500))['peTtm'] is None
    with pytest.raises(MissingData):
        financial_metrics(rows[:2], D(500))


def test_chip_distribution_normalization_and_cost_windows():
    records = series([D(10) + D(index % 15) / 10 for index in range(120)])
    distribution = chip_distribution(records, records[-1].day)
    assert abs(sum(item['ratio'] for item in distribution['bins']) - 100) < 1e-8
    assert 0 <= distribution['profitableRatio'] <= 100
    assert distribution['cost90'][0] <= distribution['cost70'][0] <= distribution['cost70'][1] <= distribution['cost90'][1]
    assert distribution['currentPrice'] == float(records[-1].close)
    assert all(item['ratio'] >= 0 for item in distribution['bins'])
    with pytest.raises(MissingData):
        chip_distribution(records[:50], records[49].day)
    with pytest.raises(MissingData):
        chip_distribution([replace(records[0], turnover=None), *records[1:]], records[-1].day)
