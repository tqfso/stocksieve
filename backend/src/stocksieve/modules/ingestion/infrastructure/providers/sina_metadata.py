"""本文件解析新浪批量报价并核对总市值、换手率的单位和日期，保存已确认的收盘资料。"""

import json
import re
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from stocksieve.modules.market.domain.models import Stock, decimal_value


def optional_decimal(value: object) -> Decimal | None:
    """未知、无穷和负值保留为空，不用零代替缺失资料。"""
    try:
        number = decimal_value(value)
        return number if number >= 0 else None
    except (ValueError, ArithmeticError):
        return None


def quote_rows(raw: bytes, symbols: set[str]) -> dict[str, dict[str, str]]:
    """日线单位为股、元，日期和时刻必须来自报价字段，不能取服务器当天。"""
    result = {}
    for symbol, body in re.findall(r'var hq_str_((?:sh|sz)[0-9]{6})="([^"\r\n]*)";', raw.decode('gb18030')):
        fields = body.split(',')
        if symbol not in symbols or len(fields) < 32:
            continue
        date.fromisoformat(fields[30])
        time.fromisoformat(fields[31])
        result[symbol] = dict(zip(('name', 'open', 'previousClose', 'close', 'high', 'low', 'volume', 'amount', 'date', 'time'),
            (fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[8], fields[9], fields[30], fields[31]), strict=True))
    return result


class SinaMarketData:
    """只在完整日期报价与目录价格、成交量额一致后采用市值；总市值万元转元。"""

    def __init__(self, cache: Path) -> None:
        self.path = cache / 'sina-market-observations.json'
        self.closed_path = cache / 'sina-closed-quotes.json'
        self.rows: dict[str, dict[str, Any]] = {}
        self.quotes: dict[str, dict[str, str]] = {}
        self.observed: dict[str, dict[str, dict[str, Any]]] = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.closed: dict[str, dict[str, dict[str, str]]] = json.loads(self.closed_path.read_text()) if self.closed_path.exists() else {}

    def remember_rows(self, rows: list[dict[str, Any]]) -> None:
        self.rows.update({row['symbol']: row for row in rows})

    def remember_quotes(self, quotes: dict[str, dict[str, str]]) -> None:
        self.quotes.update(quotes)
        self.record_closed(quotes)
        self.capture()

    def record_closed(self, quotes: dict[str, dict[str, str]]) -> None:
        """收盘报价自身具有明确日期；市值缺失时仍能用它更新价格，市值继续保持未知。"""
        changed = False
        for symbol, quote in quotes.items():
            if not quote.get('time') or time.fromisoformat(quote['time']) < time(15):
                continue
            day = self.closed.setdefault(quote['date'], {})
            if day.get(symbol) != quote:
                day[symbol] = dict(quote)
                changed = True
        if changed:
            self.closed = {day: self.closed[day] for day in sorted(self.closed)[-3:]}
            self.closed_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.closed_path.with_suffix('.' + uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(self.closed, ensure_ascii=False))
            temporary.replace(self.closed_path)

    def capture(self) -> None:
        changed = False
        for symbol, quote in self.quotes.items():
            row = self.rows.get(symbol)
            if not row or not quote.get('time') or time.fromisoformat(quote['time']) < time(15):
                continue
            volume = optional_decimal(quote.get('volume'))
            if not volume or volume != optional_decimal(row.get('volume')):
                continue
            matches = [('open', 'open'), ('high', 'high'), ('low', 'low'), ('close', 'trade')]
            if any(optional_decimal(quote.get(a)) is None or optional_decimal(row.get(b)) is None or
                   abs(decimal_value(quote[a]) - decimal_value(row[b])) > Decimal('.011') for a, b in matches):
                continue
            amount, listed_amount = optional_decimal(quote.get('amount')), optional_decimal(row.get('amount'))
            if amount is None or listed_amount is None or abs(amount - listed_amount) > 1:
                continue
            cap = optional_decimal(row.get('mktcap'))
            turnover = optional_decimal(row.get('turnoverratio'))
            values = {'close': quote['close'], 'volume': quote['volume'], 'amount': quote['amount'],
                'cap': str(cap * 10000) if cap else None, 'turnover': str(turnover) if turnover is not None else None, 'quote': dict(quote)}
            day = self.observed.setdefault(quote['date'], {})
            if day.get(symbol) != values:
                day[symbol] = values
                changed = True
        if changed:
            self.observed = {day: self.observed[day] for day in sorted(self.observed)[-3:]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.' + uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(self.observed, ensure_ascii=False))
            temporary.replace(self.path)

    def value(self, stock: Stock, day: date, close: Decimal, field: str) -> Decimal | None:
        symbol = ('sh' if stock.exchange == 'SSE' else 'sz') + stock.code
        row = self.observed.get(str(day), {}).get(symbol)
        if not row or abs(decimal_value(row['close']) - close) > Decimal('.011') or row[field] is None:
            return None
        return decimal_value(row[field])

    def cap(self, stock: Stock, day: date, close: Decimal) -> Decimal | None:
        return self.value(stock, day, close, 'cap')

    def turnover(self, stock: Stock, day: date, close: Decimal) -> Decimal | None:
        return self.value(stock, day, close, 'turnover')

    def closed_quote(self, symbol: str, day: date) -> dict[str, str]:
        return self.closed.get(str(day), {}).get(symbol) or self.observed.get(str(day), {}).get(symbol, {}).get('quote', {})
