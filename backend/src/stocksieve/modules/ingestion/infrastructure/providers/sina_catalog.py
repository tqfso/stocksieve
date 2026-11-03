"""本文件读取新浪明确的板块、上市日期及申万一级行业，按任务保存分页断点。"""

import json
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from stocksieve.modules.ingestion.application.progress import report_progress
from stocksieve.modules.ingestion.infrastructure.checkpoint import SourceCheckpoint
from stocksieve.modules.ingestion.infrastructure.providers.sina_codec import assignment
from stocksieve.modules.ingestion.infrastructure.providers.sina_metadata import BASE
from stocksieve.shared.errors import DomainError

Document = Callable[[str, int], Awaitable[tuple[bytes, datetime]]]
PREFIXES = {'600': 'SSE_MAIN', '601': 'SSE_MAIN', '603': 'SSE_MAIN', '605': 'SSE_MAIN', '688': 'STAR',
    '689': 'STAR', '000': 'SZSE_MAIN', '001': 'SZSE_MAIN', '002': 'SZSE_MAIN', '003': 'SZSE_MAIN',
    '300': 'CHINEXT', '301': 'CHINEXT'}


class CompanyCells(HTMLParser):
    """仅提取表格单元格文本，不执行页面脚本；上市日期按明确标签读取。"""

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[str] = []
        self.current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == 'td':
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == 'td' and self.current is not None:
            self.cells.append(''.join(self.current).strip())
            self.current = None


def listing_date(raw: bytes) -> date:
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('gb18030')
    parser = CompanyCells()
    parser.feed(text)
    for index, cell in enumerate(parser.cells[:-1]):
        if cell.rstrip('：: ').strip() == '上市日期':
            match = re.search(r'\b\d{4}-\d{2}-\d{2}\b', parser.cells[index + 1])
            if match:
                return date.fromisoformat(match[0])
    raise DomainError('DATA_INCOMPLETE', '新浪公司资料未明确提供上市日期。', 503)


class SinaCatalog:
    """目录和行业复用同一受控HTTP会话，成功的分页和单股基础资料均可跨重启恢复。"""

    def __init__(self, cache: Path, document: Document) -> None:
        self.cache, self.document = cache, document
        self.path = cache / 'sina-foundation.json'
        self.records: dict[str, dict[str, str]] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(self.records, ensure_ascii=False))
        temporary.replace(self.path)

    async def members(self, node: str, checkpoint: SourceCheckpoint) -> list[dict[str, Any]]:
        """每个节点固定总数，逐页检查唯一性与条数；未成功的页不推进游标。"""
        if not re.fullmatch(r'[a-zA-Z0-9_]+', node):
            raise DomainError('DATA_INCOMPLETE', '新浪分类节点无效。', 503)
        states = checkpoint.data.setdefault('nodes', {})
        if node not in states:
            raw, _ = await self.document(BASE + 'Market_Center.getHQNodeStockCount?' + urlencode({'node': node}), 86400)
            total = int(assignment(raw))
            if not 0 <= total <= 100000:
                raise DomainError('DATA_INCOMPLETE', '新浪分类成员总数异常。', 503)
            states[node] = {'total': total, 'rows': []}
            checkpoint.save()
        state = states[node]
        rows = state['rows']
        seen = {row['symbol'] for row in rows}
        for offset in range(len(rows), state['total'], 80):
            url = BASE + 'Market_Center.getHQNodeData?' + urlencode({'page': offset // 80 + 1, 'num': 80,
                'sort': 'symbol', 'asc': 1, 'node': node, 'symbol': '', '_s_r_a': 'page'})
            raw, _ = await self.document(url, 86400)
            page = assignment(raw)
            if not isinstance(page, list) or len(page) != min(80, state['total'] - offset):
                raise DomainError('DATA_INCOMPLETE', f'新浪分类{node}分页不完整。', 503)
            for row in page:
                symbol = row.get('symbol', '')
                if not re.fullmatch(r'(sh|sz|bj)[0-9]{6}', symbol) or symbol in seen:
                    raise DomainError('DATA_INCOMPLETE', '新浪分类成员重复或证券代码无效。', 503)
                seen.add(symbol)
            rows.extend({'symbol': row['symbol']} for row in page)
            checkpoint.save()
        return rows

    async def board(self, symbol: str) -> str:
        if prefix := PREFIXES.get(symbol[2:5]):
            return prefix
        checkpoint = SourceCheckpoint(self.cache / 'sina-board-checkpoints')
        for node, board in [('cyb', 'CHINEXT'), ('kcb', 'STAR'), ('sh_a', 'SSE_MAIN'), ('sz_a', 'SZSE_MAIN')]:
            rows = await self.members(node, checkpoint)
            if any(row['symbol'] == symbol for row in rows):
                return board
        raise DomainError('DATA_INCOMPLETE', f'新浪未确认证券{symbol}的上市板块。', 503)

    async def listed_on(self, symbol: str, known: date | None = None) -> date:
        if known:
            return known
        if value := self.records.get(symbol, {}).get('listedOn'):
            return date.fromisoformat(value)
        raw, _ = await self.document('https://money.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/' + symbol[2:] + '.phtml', 365 * 86400)
        result = listing_date(raw)
        self.records[symbol] = {**self.records.get(symbol, {}), 'listedOn': str(result)}
        self.save()
        report_progress(f'已核对新浪证券{symbol}的明确上市日期：{result}。', 18, True)
        return result
