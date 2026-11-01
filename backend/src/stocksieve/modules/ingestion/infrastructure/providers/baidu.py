"""本文件批量读取百度股市通明确提供的概念归属，按任务恢复并累积覆盖，保留原行业。"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

from stocksieve.modules.ingestion.application.ports import CatalogOnlySource
from stocksieve.modules.ingestion.application.progress import (
    current_operation_id,
    report_progress,
)
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.domain.models import Stock
from stocksieve.shared.errors import DomainError


def parse_concepts(value: Any, requested: set[str]) -> dict[str, list[dict[str, str]] | None]:
    """只采用明确标为概念的归属，行业、地域不混入；未返回的股票保持未知。"""
    if not isinstance(value, dict) or str(value.get('ResultCode')) != '0' or not isinstance(value.get('Result'), dict) or not value['Result']:
        raise DomainError('DATA_NOT_AVAILABLE', '百度未返回可核验的题材归属。', 503)
    data = value['Result']
    if set(data) - requested:
        raise DomainError('DATA_INCOMPLETE', '题材响应包含未请求的证券。', 503)
    result: dict[str, list[dict[str, str]] | None] = {code: None for code in requested}
    for code, groups in data.items():
        if groups is None:
            continue
        if not isinstance(groups, list):
            raise DomainError('DATA_INCOMPLETE', '证券题材类别结构无效。', 503)
        concepts: dict[str, dict[str, str]] = {}
        known = False
        for group in groups:
            if not isinstance(group, dict) or group.get('name') != '概念':
                continue
            if not isinstance(group.get('list'), list):
                raise DomainError('DATA_INCOMPLETE', '题材列表结构无效。', 503)
            known = True
            for item in group['list']:
                name = item.get('name')
                identifiers = parse_qs(item.get('xcx_query', '')).get('code', [])
                if len(identifiers) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', identifiers[0]) or not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
                    raise DomainError('DATA_INCOMPLETE', '题材稳定编码或名称缺失。', 503)
                identifier = identifiers[0]
                if identifier in concepts and concepts[identifier]['name'] != name.strip():
                    raise DomainError('DATA_INCOMPLETE', '同一题材编码名称不一致。', 503)
                concepts[identifier] = {'id': 'baidu_' + identifier, 'name': name.strip(), 'sourceCode': identifier}
        if known:
            result[code] = list(concepts.values())
    return result


class BaiduConcepts:
    """供应商每批最多20只；每个任务最多准备20批，成功归属保留原获取时间并逐步补齐。"""

    def __init__(self, policy: SourcePolicy, cache: Path, batch_limit: int = 20) -> None:
        if not 1 <= batch_limit <= 100:
            raise ValueError('题材批次数需在1至100之间。')
        self.policy, self.cache, self.batch_limit = policy, cache, batch_limit
        self.path = cache / 'baidu-concepts.json'

    def save(self, state: dict[str, Any]) -> None:
        """每批完成后原子保存；等待和重启不重复取已成功股票。"""
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False))
        temporary.replace(self.path)

    async def read(self, stocks: tuple[Stock, ...]) -> dict[str, Any]:
        # 1. 保留已有归属；首次补齐优先未访问股票，覆盖后再按需刷新过期资料。
        self.cache.mkdir(parents=True, exist_ok=True)
        try:
            state = json.loads(self.path.read_text()) if self.path.exists() else {'records': {}, 'batches': {}}
        except (ValueError, OSError):
            raise DomainError('SOURCE_CACHE_INVALID', '题材采集断点损坏，已停止更新。', 503) from None
        records = state['records']
        codes = list(dict.fromkeys(stock.code for stock in stocks))
        if len(codes) != len(stocks):
            raise DomainError('DATA_INCOMPLETE', '证券代码存在歧义，不能关联题材。', 503)
        missing = [code for code in codes if code not in records]
        pending = missing or [code for code in codes if time.time() - records[code].get('checkedAt', records[code]['at']) >= 86400]
        operation = current_operation_id() or uuid4().hex
        batch = state['batches'].setdefault(operation, {'used': 0, 'finished': False})
        client = SourceHTTP(self.policy, self.cache)
        try:
            # 2. 按输入优先级取数；实际请求统一经过共享额度与间隔控制。
            if not batch['finished']:
                for index in range(0, len(pending), 20):
                    if batch['used'] >= self.batch_limit:
                        break
                    selected = pending[index:index + 20]
                    query = {'stock': json.dumps([{'code': code, 'market': 'ab', 'type': 'stock'} for code in selected], separators=(',', ':')),
                             'finClientType': 'pc'}
                    raw, fetched = await client.document('baidu', 'https://finance.pae.baidu.com/api/getrelatedblock?' + urlencode(query))
                    try:
                        value = json.loads(raw)
                    except ValueError:
                        raise DomainError('DATA_NOT_AVAILABLE', '题材响应不是有效JSON。', 503) from None
                    if not isinstance(value, dict) or str(value.get('ResultCode')) != '0':
                        message = str(value.get('ResultMsg', '')) if isinstance(value, dict) else ''
                        limited = any(term in message.lower() for term in ('频繁', '限流', 'too many', 'rate limit'))
                        self.policy.block('baidu', 86400 if limited else 3600)
                        raise DomainError('SOURCE_RATE_LIMITED' if limited else 'SOURCE_UNAVAILABLE', '题材接口未成功返回，已暂停后续请求。', 503)
                    parsed = parse_concepts(value, set(selected))
                    for code, concepts in parsed.items():
                        old = records.get(code)
                        if concepts is None and old and old.get('concepts') is not None:
                            records[code] = {**old, 'checkedAt': fetched.timestamp()}
                        else:
                            records[code] = {'concepts': concepts, 'at': fetched.timestamp(), 'checkedAt': fetched.timestamp()}
                    batch['used'] += 1
                    self.save(state)
                    count = sum(code in records and records[code]['concepts'] is not None for code in codes)
                    report_progress(f'已取得{count}/{len(codes)}只股票的题材归属，本批{batch["used"]}/{self.batch_limit}组。', min(90, int(batch['used'] / self.batch_limit * 90)), True)
                batch['finished'] = True
                self.save(state)
            # 3. 仅发布已明确取得的归属，未覆盖股票保持未知，行业由业务层保留。
            known = {stock.stock_id: records[stock.code] for stock in stocks if stock.code in records and records[stock.code]['concepts'] is not None}
            names = {item['id'] for record in known.values() for item in record['concepts']}
            if not known or not names:
                raise DomainError('DATA_NOT_AVAILABLE', '尚未取得有效题材归属，本轮不更新分类。', 503)
            summary = {'knownStocks': len(known), 'totalStocks': len(stocks), 'knownConcepts': len(names)}
            report_progress(f'题材已覆盖{len(known)}/{len(stocks)}只股票、{len(names)}个概念；继续更新可补齐其余股票。', 95)
            return {'stocks': {stock_id: {'concepts': record['concepts']} for stock_id, record in known.items()}, 'fields': ['concepts'],
                    'provenance': {'concepts': 'baidu'}, 'conceptCoverage': 'complete' if len(known) == len(stocks) else 'partial',
                    'conceptCollection': summary, 'observedAt': datetime.fromtimestamp(max(record['at'] for record in known.values()), timezone.utc).isoformat()}
        finally:
            await client.close()


class BaiduCatalogSource(CatalogOnlySource):
    """独立可选的百度题材渠道，更新题材并保留原有行业。"""

    id = 'baidu_concepts'
    name = '百度股市通题材'
    boards = frozenset({'SSE_MAIN', 'SZSE_MAIN', 'CHINEXT', 'STAR'})

    def __init__(self, provider: BaiduConcepts) -> None:
        self.provider = provider

    async def catalog(self, stocks: tuple[Stock, ...]) -> dict[str, Any]:
        return await self.provider.read(stocks)
