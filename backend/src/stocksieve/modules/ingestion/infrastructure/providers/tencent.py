"""本文件批量读取腾讯公开报价中的明确板块标签与总市值，不替换Baostock行情或推造缺失字段。"""

import hashlib
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from stocksieve.modules.ingestion.application.progress import report_progress
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.domain.models import Stock, decimal_value
from stocksieve.shared.errors import DomainError


class TencentMetadata:
    """按100只一组取公开元数据，报告交易日期必须匹配快照才采用其市值。"""

    def __init__(self, policy: SourcePolicy, cache: Path) -> None:
        self.policy, self.cache = policy, cache
        self.path = cache / 'tencent-metadata.json'
        self.values: dict[str, dict[str, str]] = {}
        self.batches: dict[str, float] = {}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                self.values = saved.get('values', saved)
                self.batches = saved.get('batches', {})
            except (ValueError, OSError):
                pass

    async def fetch(self, symbols: list[str]) -> bool:
        if not symbols or len(symbols) > 100 or any(not re.fullmatch(r'(?:sh|sz)[0-9]{6}', symbol) for symbol in symbols):
            raise ValueError('公开报价证券列表无效。')
        key = hashlib.sha256(','.join(symbols).encode()).hexdigest()
        if 0 <= time.time() - self.batches.get(key, 0) < 3600:
            return False
        client = SourceHTTP(self.policy, self.cache)
        try:
            raw, fetched = await client.document('tencent', 'https://qt.gtimg.cn/' + '?' + urlencode({'q': ','.join(symbols)}), cache_seconds=3600)
            text = raw.decode('gb18030')
            matches = re.findall(r'v_((?:sh|sz)[0-9]{6})="([^"\r\n]*)";?', text)
            if not matches:
                self.policy.block('tencent', 86400)
                raise DomainError('DATA_NOT_AVAILABLE', '公开报价元数据格式未通过校验。', 503)
            for symbol, body in matches:
                if symbol not in symbols:
                    raise DomainError('DATA_INCOMPLETE', '公开报价包含未请求的证券。', 503)
                fields = body.split('~')
                if len(fields) < 62 or fields[2] != symbol[2:]:
                    continue
                label = next((value for value in fields if value.startswith('GP-A-') or value == 'GP-A'), '')
                board = 'CHINEXT' if label.startswith('GP-A-CYB') else 'STAR' if label.startswith('GP-A-KCB') else ('SSE_MAIN' if symbol.startswith('sh') else 'SZSE_MAIN') if label == 'GP-A' else ''
                stamp = fields[30]
                if not re.fullmatch(r'[0-9]{14}', stamp):
                    continue
                try:
                    day = datetime.strptime(stamp, '%Y%m%d%H%M%S').date()
                except ValueError:
                    continue
                def positive(value: str) -> Decimal | None:
                    try:
                        result = decimal_value(value)
                        return result if result > 0 else None
                    except (ValueError, InvalidOperation):
                        return None
                cap = positive(fields[45])
                cap = cap * 100000000 if cap is not None else None
                price = positive(fields[3])
                self.values[symbol] = {'board': board, 'date': str(day), 'cap': str(cap) if cap is not None else '',
                                       'price': str(price) if price is not None else '', 'fetchedAt': fetched.isoformat()}
            self.batches[key] = fetched.timestamp()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'values': self.values, 'batches': self.batches}, ensure_ascii=False))
            temporary.replace(self.path)
            return True
        finally:
            await client.close()

    def board(self, code: str) -> str | None:
        return self.values.get(code.replace('.', ''), {}).get('board') or None

    def cap(self, stock: Stock, day: date, close: Decimal) -> Decimal | None:
        symbol = ('sh' if stock.exchange == 'SSE' else 'sz') + stock.code
        row = self.values.get(symbol, {})
        if row.get('date') != str(day) or not row.get('cap') or not row.get('price'):
            return None
        if abs(decimal_value(row['price']) - close) > Decimal('.011'):
            return None
        cap = decimal_value(row['cap'])
        return cap if cap > 0 else None

    async def prepare(self, stocks: tuple[Stock, ...]) -> None:
        symbols = sorted(('sh' if stock.exchange == 'SSE' else 'sz') + stock.code for stock in stocks)
        for index in range(0, len(symbols), 100):
            if await self.fetch(symbols[index:index + 100]):
                report_progress(f'已核对{min(index + 100, len(symbols))}/{len(symbols)}只股票的公开市值资料。', 70 + int(min(index + 100, len(symbols)) / len(symbols) * 10), True)
