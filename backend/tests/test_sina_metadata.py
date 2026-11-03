"""本文件验证新浪总市值万元转元、收盘日期证明和跨日资料隔离，避免混用流通市值。"""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

from stocksieve.modules.ingestion.infrastructure.providers.sina_metadata import (
    SinaMarketData,
    quote_rows,
)
from stocksieve.modules.market.domain.models import Stock

STOCK = Stock('SSE.600000', '600000', '样本', 'SSE', 'SSE_MAIN')


def sample() -> tuple[dict, dict]:
    quote = {'name': '样本', 'date': '2026-11-02', 'time': '15:00:03', 'open': '10', 'high': '11', 'low': '9',
        'close': '10.5', 'previousClose': '10', 'volume': '10000', 'amount': '105000'}
    row = {'symbol': 'sh600000', 'open': '10', 'high': '11', 'low': '9', 'trade': '10.5',
        'volume': 10000, 'amount': 105000, 'mktcap': 1000000, 'nmc': 300000, 'turnoverratio': 2.5}
    return quote, row


def test_total_cap_and_turnover_require_dated_matching_close(tmp_path: Path) -> None:
    data = SinaMarketData(tmp_path)
    quote, row = sample()
    data.remember_rows([row]); data.remember_quotes({'sh600000': quote})
    assert data.cap(STOCK, date(2026, 11, 2), D('10.5')) == D(10000000000)
    assert data.turnover(STOCK, date(2026, 11, 2), D('10.5')) == D('2.5')
    assert data.cap(STOCK, date(2026, 11, 3), D('10.5')) is None
    assert data.cap(STOCK, date(2026, 11, 2), D(10)) is None
    assert SinaMarketData(tmp_path).closed_quote('sh600000', date(2026, 11, 2))['previousClose'] == '10'


def test_intraday_or_inconsistent_directory_is_not_tagged_as_close(tmp_path: Path) -> None:
    data = SinaMarketData(tmp_path)
    quote, row = sample()
    data.remember_rows([row])
    data.remember_quotes({'sh600000': {**quote, 'time': '10:00:00'}})
    assert data.cap(STOCK, date(2026, 11, 2), D('10.5')) is None
    data.remember_rows([{**row, 'volume': 9000}]); data.remember_quotes({'sh600000': quote})
    assert data.cap(STOCK, date(2026, 11, 2), D('10.5')) is None


def test_quote_parser_uses_source_date_and_explicit_units() -> None:
    values = [''] * 32
    for i, v in {0: '样本', 1: '10', 2: '9', 3: '10.5', 4: '11', 5: '9', 8: '10000', 9: '105000', 30: '2026-11-02', 31: '15:00:03'}.items():
        values[i] = v
    raw = ('var hq_str_sh600000="' + ','.join(values) + '";').encode('gb18030')
    quote = quote_rows(raw, {'sh600000'})['sh600000']
    assert quote['date'] == '2026-11-02' and quote['volume'] == '10000' and quote['amount'] == '105000'
