"""本文件执行分析技能的资料工具、计算与报告落库，固定输入并校验引用来源。"""

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, update

import stocksieve.infrastructure.persistence.schema as t
from stocksieve.infrastructure.persistence.database import utc
from stocksieve.infrastructure.persistence.operations import upsert
from stocksieve.modules.analysis.application.evidence import verify_facts
from stocksieve.modules.analysis.application.facts import (
    complete_financial_facts,
    financial_gap,
    report_facts,
    shareholder_facts,
)
from stocksieve.modules.analysis.application.ports import (
    AgentRuntime,
    DocumentParser,
    SearchProvider,
)
from stocksieve.modules.analysis.application.reports import (
    ReductionEvidence,
    ReportDraft,
)
from stocksieve.modules.analysis.domain.calculations import (
    chip_distribution,
    financial_metrics,
)
from stocksieve.modules.analysis.domain.finance import calculate
from stocksieve.modules.analysis.domain.trade import general_trade
from stocksieve.modules.ingestion.application.ports import (
    PublicHTTPClient,
    SourceDeferred,
)
from stocksieve.modules.jobs.application.service import Tasks
from stocksieve.modules.jobs.domain.models import Lease
from stocksieve.modules.market.domain.models import StockState
from stocksieve.shared.errors import DomainError, MissingData
from stocksieve.shared.serialization import encode
from stocksieve.shared.time import SHANGHAI


class AnalysisTools:
    """工具仅访问当前任务输入和批准的公开域名，所有远程读取共享供应商频控。"""

    def __init__(self, tasks: Tasks, lease: Lease, http: PublicHTTPClient | None = None,
                 search: SearchProvider | None = None, reader: DocumentParser | None = None) -> None:
        self.tasks, self.lease, self.db = tasks, lease, tasks.db
        self.http, self.search = http, search
        self.reader = reader
        self.downloaded: dict[str, dict[str, Any]] = {}
        self.read_pages: dict[str, set[int]] = {}
        self.research_retry_at: datetime | None = None
        self.calls = 0
        self.seen: dict[str, dict[str, Any]] = {}
        with self.db.transaction() as connection:
            self.task = dict(tasks.assert_lease(connection, lease))
            self.snapshot = tasks.market.snapshot(connection, self.task['input_snapshot_id'])
            self.record = next(row for row in tasks.market.records(connection, self.snapshot) if row['stock']['stockId'] == self.task['stock_id'])
            self.history = tasks.market.history(connection, self.snapshot, self.task['stock_id'])
        self.facts: dict[str, Any] = {}
        self.reduction_verified = False

    def budget(self) -> None:
        self.calls += 1
        if self.calls > self.tasks.settings.ai_max_steps:
            raise DomainError('ANALYSIS_TOOL_LIMIT', '分析工具调用次数达到上限。', 503)

    def stage(self, value: str, progress: int) -> None:
        with self.db.transaction(serialized=True) as connection:
            row = self.tasks.assert_lease(connection, self.lease)
            connection.execute(update(t.tasks).where(t.tasks.c.id == self.lease.id).values(options_json={**row['options_json'], 'stage': value}, progress=max(row['progress'], progress)))

    def save(self, key: str, kind: str, title: str, data: Any = None, *, url: str | None = None,
             published_at: datetime | None = None, fetched_at: datetime | None = None,
             missing: str | None = None, financial_id: str | None = None) -> dict[str, Any]:
        """已取得的任务资料不可被后续尝试覆盖；引用只来自实际保存的内容。"""
        payload = json.loads(encode(data)) if data is not None else None
        with self.db.transaction(serialized=True) as connection:
            self.tasks.assert_lease(connection, self.lease)
            existing = connection.execute(select(t.materials).where(t.materials.c.task_id == self.lease.id, t.materials.c.material_key == key)).mappings().first()
            if existing and existing['status'] == 'available':
                record = dict(existing)
            else:
                record = {'task_id': self.lease.id, 'material_key': key, 'material_kind': kind, 'source_name': title,
                          'source_url': url, 'published_at': published_at, 'fetched_at': fetched_at or self.tasks.clock(),
                          'status': 'missing' if missing else 'available', 'content_json': payload,
                          'content_hash': hashlib.sha256(encode(payload).encode()).hexdigest() if payload is not None else None,
                          'missing_reason': missing, 'financial_report_id': financial_id}
                upsert(connection, t.materials, {'task_id': self.lease.id, 'material_key': key}, {name: value for name, value in record.items() if name not in {'task_id', 'material_key'}})
            self.seen[key] = record
        return {'key': key, 'status': record['status'], 'data': record['content_json'], 'missing': record['missing_reason']}

    async def snapshot_facts(self) -> dict[str, Any]:
        self.stage('researching', 15)
        record = {key: value for key, value in self.record.items() if key != 'signalRows'}
        record['snapshot'] = self.tasks.market.snapshot_view(self.snapshot)
        record['signals'] = [self.tasks.market.signal_view(row, detail=True) for row in self.record['signalRows'].values()]
        result = self.save('snapshot', 'market', '已同步行情与策略快照', record, published_at=utc(self.snapshot['published_at']))
        self.facts['snapshot'] = record
        with self.db.transaction() as connection:
            row = connection.execute(select(t.snapshot_stocks).where(t.snapshot_stocks.c.snapshot_id == self.snapshot['id'],
                t.snapshot_stocks.c.stock_id == self.task['stock_id'])).mappings().one()
        days = [datetime.fromisoformat(day).date() for day, opened in self.snapshot['calendar_json'][self.record['stock']['exchange']].items() if opened is True]
        technical = general_trade(self.history, days, self.snapshot['data_date'], StockState(row['trade_status'], row['is_st'], row['is_delisting']))
        self.facts['generalTrade'] = technical
        self.facts['averages'] = {f'ma{period}': row[f'ma{period}'] for period in (5, 10, 20, 60, 120)}
        self.save('general_trade', 'market', '通用技术观察计算 v1', technical)
        for index, missing in enumerate(technical['missing']):
            self.save(f'general_missing_{index}', 'market', '通用技术资料', missing=missing)
        try:
            distribution = chip_distribution(self.history, self.snapshot['data_date'])
            self.facts['chips'] = distribution
            self.save('chips', 'chips', '换手衰减成本估算 v1', distribution)
        except MissingData as error:
            self.save('chips', 'chips', '筹码估算资料', missing=str(error))
        return result

    async def financial_facts(self) -> dict[str, Any]:
        with self.db.transaction() as connection:
            reports = connection.execute(select(t.financials).where(t.financials.c.stock_id == self.task['stock_id'],
                t.financials.c.fetched_at <= self.tasks.clock(), t.financials.c.period_end <= self.tasks.clock().date())
                .order_by(t.financials.c.period_end.desc(), t.financials.c.published_at.desc().nullslast(), t.financials.c.fetched_at.desc())).mappings().all()
        reports = [dict(row) for row in reports if row['published_at'] is None or utc(row['published_at']) <= self.tasks.clock()]
        try:
            cap = self.record['quote']['totalMarketCap'] if self.record['quote'] else None
            metrics = financial_metrics(reports, cap)
            if metrics['revenueTtm'] is None or metrics['netProfitParentTtm'] is None:
                raise MissingData('核心累计收入或归母净利润字段缺失。')
        except MissingData:
            return self.save('financial', 'financial', 'TTM计算资料', missing='尚未取得可计算TTM的完整期间数据。')
        used = [row for row in reports if row['id'] in metrics['reportIds']]
        for row in used:
            self.save('financial_' + row['id'], 'financial', f"{row['source']} · {row['period_end']}", row['raw_payload_json'],
                      url=row['source_url'], published_at=utc(row['published_at']) if row['published_at'] else None,
                      fetched_at=utc(row['fetched_at']), financial_id=row['id'])
        self.facts['financial'] = metrics
        return self.save('financial', 'financial', 'TTM 与估值计算', metrics)

    async def search_public(self, query: str) -> dict[str, Any]:
        self.budget()
        key = 'search_' + hashlib.sha256(query.encode()).hexdigest()[:32]
        if len(query) > 200 or self.record['stock']['code'] not in query:
            return {'status': 'missing', 'missing': '检索需包含当前股票代码，且查询不超过200字。'}
        if self.search is None:
            return self.save(key, 'research', '公开资料检索', missing='尚未配置通过可用性与限频验证的免费检索渠道。')
        try:
            hits = await self.search.search(query, exchange=self.record['stock']['exchange'])
        except SourceDeferred as wait:
            self.research_retry_at = wait.until
            message = '公告资料暂时无法更新，可在' + wait.until.astimezone(SHANGHAI).strftime('%m月%d日%H:%M') + '后重试。'
            return self.save(key, 'research', '公开资料检索', {'query': query, 'retryAt': wait.until.isoformat()}, missing=message)
        except DomainError:
            return self.save(key, 'research', '公开资料检索', {'query': query}, missing='公告资料暂时无法更新，请稍后重试。')
        warnings = list(dict.fromkeys(hit.cache_warning for hit in hits if hit.cache_warning))
        if warnings:
            self.facts.setdefault('researchLimitations', []).extend(item for item in warnings if item not in self.facts.get('researchLimitations', []))
            self.save(key, 'research', '公告检索范围', {'query': query}, missing='；'.join(warnings))
        # 搜索摘要只用于发现原文，不能直接作为财务或减持事实依据。
        return {'status': 'discovered', 'limitations': warnings, 'results': [{'title': hit.title, 'url': hit.url, 'publishedAt': hit.published_at, 'indexFetchedAt': hit.index_fetched_at} for hit in hits[:8]]}

    async def download_public(self, url: str, title: str = '', published_at: str | None = None) -> dict[str, Any]:
        """下载只接受公开披露域名；资料需经过阅读与证券身份校验后才成为可引用证据。"""
        key = 'page_' + hashlib.sha256(url.encode()).hexdigest()[:32]
        try:
            parsed = urlsplit(url)
            allowed = ('sse.com.cn', 'szse.cn', 'bse.cn', 'cninfo.com.cn')
            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in {None, 443} or not parsed.hostname or not any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in allowed):
                return {'status': 'missing', 'missing': '仅支持技能中列出的公开披露原文地址。'}
        except ValueError:
            return {'status': 'missing', 'missing': '原文地址格式无效。'}
        if key in self.downloaded or self.seen.get(key, {}).get('status') == 'available':
            return {'materialKey': key, 'status': 'downloaded', 'title': title, 'url': url}
        if not self.http:
            return {'status': 'missing', 'missing': '公开资料下载尚未就绪。'}
        try:
            raw, fetched = await self.http.document(parsed.hostname, url)
            self.downloaded[key] = {'raw': raw, 'title': title, 'url': url, 'publishedAt': published_at, 'fetchedAt': fetched}
            return {'materialKey': key, 'status': 'downloaded', 'title': title, 'url': url, 'publishedAt': published_at}
        except (SourceDeferred, DomainError):
            return {'status': 'missing', 'missing': '原文下载暂不可用或处于频控，未绕过冷却。'}

    async def search_and_download(self, query: str, url: str = '') -> dict[str, Any]:
        """联网工具：检索当前证券公告并下载最多三份原文，也可下载已发现的披露地址。"""
        if url:
            self.budget()
            return await self.download_public(url)
        discovered = await self.search_public(query)
        if discovered.get('status') != 'discovered':
            return discovered
        documents = []
        limit = 1 if '季度报告' in query or '年度报告' in query else 3
        for hit in discovered.get('results', [])[:limit]:
            documents.append(await self.download_public(hit['url'], hit['title'], hit.get('publishedAt')))
        return {'status': 'discovered', 'documents': documents, 'limitations': discovered.get('limitations', []), 'otherResults': discovered.get('results', [])[limit:],
                'note': '检索标题不是事实证据；用read_document读取正文后才能引用materialKey。'}

    async def read_document(self, material_key: str, keyword: str = '', page: int = 1) -> dict[str, Any]:
        """阅读工具：提取正文并保存，按关键词或页码返回有限片段，保持页码可追溯。"""
        self.budget()
        if len(keyword) > 80 or type(page) is not int or page < 1:
            return {'status': 'missing', 'missing': '关键词最多80字，页码必须为正整数。'}
        source = self.seen.get(material_key)
        if not source or source['status'] != 'available':
            downloaded = self.downloaded.get(material_key)
            if not downloaded or not self.reader:
                return {'status': 'missing', 'missing': '请先检索下载原文；不能读取任意服务器文件。'}
            try:
                document = await asyncio.to_thread(self.reader.parse, downloaded['raw'])
                normalized = ''.join(document['text'].split())
                if self.record['stock']['code'] not in normalized and ''.join(self.record['stock']['name'].split()) not in normalized:
                    return self.save(material_key, 'research', downloaded['title'] or document['title'], missing='原文未匹配当前股票代码或名称，暂不作为公司事实依据。')
                stamp = downloaded['publishedAt'] or document.get('publishedAt')
                published = datetime.fromisoformat(stamp) if stamp else None
                self.save(material_key, 'research', downloaded['title'] or document['title'], document,
                          url=downloaded['url'], published_at=published, fetched_at=downloaded['fetchedAt'])
                source = self.seen[material_key]
            except (MissingData, ValueError) as error:
                return self.save(material_key, 'research', downloaded['title'] or '公开原文', missing=str(error))
        document = source['content_json']
        pages = document.get('pages', [{'page': 1, 'text': document.get('text', '')}])
        snippets = []
        for item in pages:
            text = item['text']
            position = text.find(keyword) if keyword else 0
            if (keyword and position >= 0) or (not keyword and page <= item['page'] < page + 2):
                start = max(0, position - 500)
                snippets.append({'page': item['page'], 'text': text[start:start + 4000]})
            if len(snippets) >= 3:
                break
        self.read_pages.setdefault(material_key, set()).update(item['page'] for item in snippets)
        return {'key': material_key, 'status': 'available', 'title': source['source_name'], 'url': source['source_url'],
                'publishedAt': source['published_at'], 'pageCount': document.get('pageCount', len(pages)),
                'truncated': document.get('truncated', False), 'snippets': snippets,
                'note': '未命中不代表不存在。将累计财务数值与报告期末股东户数连同逐字原文、页码、单位、统计期写入facts，不能只写正文；单位须使用原文单位，数值不要换算或四舍五入。'}

    async def calculate_finance(self, operation: str, inputs: dict[str, Any], source_keys: list[str] | None = None) -> dict[str, Any]:
        """计算工具：固定公式，记录输入来源和情景假设，计算结果可作为报告引用。"""
        self.budget()
        keys = source_keys or []
        if any(self.seen.get(key, {}).get('status') != 'available' for key in keys):
            return {'status': 'missing', 'missing': '计算引用了尚未取得的输入资料。'}
        try:
            if not keys and not inputs.get('assumption'):
                raise ValueError('请指定已取得的输入资料键，或明确填写assumption情景假设。')
            result = calculate(operation, inputs)
        except (ValueError, TypeError, ArithmeticError):
            return {'status': 'missing', 'missing': '计算输入无效；请按技能所列字段、单位和区间提供参数。'}
        result['sourceKeys'] = keys
        self.facts.setdefault('calculations', []).append(result)
        key = 'calculation_' + hashlib.sha256(encode(result).encode()).hexdigest()[:24]
        return self.save(key, 'calculation', '金融计算 · ' + operation, result)

    async def review_reductions(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """股东、状态与数量语境须来自已读原文；空结果不能证明没有减持风险。"""
        verified = []
        if not 1 <= len(records) <= 20:
            return self.save('reduction_review', 'reduction', '股东减持核验', missing='未取得足够的减持原文证据。')
        terms = {'planned': ('减持计划', '拟减持'), 'in_progress': ('进展', '已减持', '实施中', '时间过半', '数量过半'),
                 'completed': ('实施完毕', '减持完成', '计划完成'), 'terminated': ('终止',)}
        def normalized(value: str) -> str:
            return ''.join(value.split())
        for record in records:
            try:
                item = ReductionEvidence.model_validate(record)
                source = self.seen.get(item.sourceKey)
                content = (source or {}).get('content_json') or {}
                text = content.get('text', '') if isinstance(content, dict) else ''
                evidence = normalized(item.evidence)
                if not source or source['status'] != 'available' or not source['source_url'] or not text or evidence not in normalized(text):
                    raise ValueError('未取得匹配的原文')
                if self.record['stock']['code'] not in evidence and self.record['stock']['name'] not in evidence:
                    raise ValueError('缺少当前股票身份')
                if normalized(item.shareholder) not in evidence or '减持' not in evidence or not any(term in evidence for term in terms[item.status]):
                    raise ValueError('原文不支持股东或状态')
                if item.status == 'completed' and any(term in evidence for term in ('未完成', '尚未完成')):
                    raise ValueError('不能把未完成认定为完成')
                if item.status == 'terminated' and any(term in evidence for term in ('未终止', '尚未终止')):
                    raise ValueError('不能把未终止认定为终止')
                for snippet in (item.planText, item.actualText, item.periodText):
                    if snippet and normalized(snippet) not in evidence:
                        raise ValueError('数量或期间不在原文证据中')
                if item.actualText and (not any(term in item.actualText for term in ('已减持', '实际减持', '累计减持')) or any(term in item.actualText for term in ('拟', '计划', '不超过'))):
                    raise ValueError('实际减持不能引用计划上限')
                verified.append(item.model_dump())
            except ValueError:
                return self.save('reduction_review', 'reduction', '股东减持核验', missing='提交的减持信息未通过原文、身份和计划/实施区分校验。')
        self.reduction_verified = True
        self.facts['reductions'] = verified
        return self.save('reduction_review', 'reduction', '基于已取得公告的减持事项核验', {'records': verified, 'scope': '仅覆盖本次实际取得的原文，不保证穷尽所有事项。'})


class Analysis:
    """成功报告、资料版本和任务终态同事务提交，模型不能改写来源与计算结果。"""

    def __init__(self, tasks: Tasks, runtime: AgentRuntime, http: PublicHTTPClient | None = None,
                 search: SearchProvider | None = None, reader: DocumentParser | None = None) -> None:
        self.tasks, self.runtime, self.http, self.search = tasks, runtime, http, search
        self.reader = reader

    async def run(self, lease: Lease) -> None:
        tools = AnalysisTools(self.tasks, lease, self.http, self.search, self.reader)
        if tools.task['options_json'].get('runtime') != self.runtime.identity():
            raise DomainError('ANALYSIS_CONFIG_CHANGED', '受理后模型配置发生变化，请重新发起分析。', 503)
        identity = tools.task['options_json']['runtime']
        await tools.snapshot_facts()
        await tools.financial_facts()
        if self.search is not None and self.http is not None and self.reader is not None:
            # 公告尽量先准备；缺少公告时继续分析已有资料，不阻断整份报告。
            month = self.tasks.clock().astimezone(SHANGHAI).month
            topic = '三季度报告' if month >= 10 or month <= 3 else '年度报告' if month == 4 else '一季度报告' if month <= 7 else '半年度报告'
            prepared = await tools.search_and_download(tools.record['stock']['code'] + ' ' + topic)
            excerpts = []
            for document in prepared.get('documents', [])[:2]:
                key = document.get('materialKey')
                if key:
                    excerpt = await tools.read_document(key, keyword='主要会计')
                    if excerpt.get('status') == 'available':
                        excerpts.append(excerpt)
                        excerpts.append(await tools.read_document(key, keyword='普通股股东总数'))
                        break
            tools.facts['researchBrief'] = excerpts
        tools.facts['researchCoverage'] = {
            'publicDocumentsAvailable': any(value['material_kind'] == 'research' and value['status'] == 'available' for value in tools.seen.values()),
            'financialsAvailable': bool(tools.facts.get('financial')),
            'instruction': '优先分析已有资料。缺少公告时，依据真实行情、均线、筹码及已同步财务形成判断；不根据记忆补造公司业务、业绩或事件。'}
        tools.stage('analyzing', 40)
        raw, usage = await self.runtime.execute(tools)
        try:
            draft = ReportDraft.model_validate(raw)
        except ValueError as error:
            raise DomainError('ANALYSIS_INVALID_REPORT', '模型返回的报告格式不符合要求。', 503) from error
        tools.facts['verifiedFacts'] = verify_facts(draft.facts, tools.seen, self.tasks.clock().date())
        if complete_financial_facts(tools.record, tools.facts):
            tools.save('financial', 'financial', '已读财报TTM计算', tools.facts['financial'])
        if draft.reductions:
            await tools.review_reductions([item.model_dump() for item in draft.reductions])
        available = {key: value for key, value in tools.seen.items() if value['status'] == 'available'}
        if any(key not in available for key in draft.sourceKeys):
            raise DomainError('ANALYSIS_INVALID_REPORT', '报告引用了未取得的资料。', 503)
        public_documents = {key for key, value in available.items() if value['material_kind'] == 'research'}
        if any(key not in available for section in draft.sections for key in section.sourceKeys):
            raise DomainError('ANALYSIS_INVALID_REPORT', '报告章节引用了未取得的资料。', 503)
        cited = set(draft.sourceKeys) | {key for section in draft.sections for key in section.sourceKeys}
        if public_documents and not public_documents.intersection(cited):
            raise DomainError('ANALYSIS_INVALID_REPORT', '真实报告未引用实际阅读的公开原文。', 503)
        tools.stage('writing', 85)
        metrics, valuation = report_facts(tools.record, tools.facts, tools.snapshot['data_date'].isoformat())
        overview = draft.overview.model_dump()
        technical = tools.facts['generalTrade']
        overview['pricePlan'] = technical['plan']
        # 只限制确实缺失的资料，不用减持核验状态覆盖整份模型判断。
        shareholders = shareholder_facts(tools.facts)
        coverage = []
        critical = list(dict.fromkeys(tools.facts.get('researchLimitations', [])))
        if not public_documents:
            critical.append('本次侧重已有行情、技术条件和已同步资料，经营与重大事项尚未通过公告补齐')
        reductions = tools.facts.get('reductions', [])
        planned = {item['shareholder'] for item in reductions if item['status'] == 'planned'}
        followed = {item['shareholder'] for item in reductions if item['status'] != 'planned'}
        missing_reductions = any('减持' in item and any(word in item for word in ('未', '缺', '待')) for item in draft.missingInformation)
        if not tools.reduction_verified or planned - followed or missing_reductions:
            critical.append('减持实施结果尚未完整核验')
        if any('诉讼' in item or '处罚' in item for item in draft.missingInformation):
            critical.append('诉讼或处罚尚未专项核验')
        financial = tools.facts.get('financial') or {}
        if financial.get('netProfitParentTtm') is None:
            coverage.append(financial_gap(tools.facts))
        elif financial['netProfitParentTtm'] <= 0:
            coverage.append('最近12个月归母净利润非正，公司市盈率不适用。')
        if shareholders['latestCount'] is None:
            coverage.append('股东户数尚未取得。')
        coverage.append('行业估值资料尚未取得，暂不作行业估值比较。')
        missing = list(dict.fromkeys(critical + coverage + draft.missingInformation))
        # 引用地址与时间只采用后端保存记录，不能接受模型自己编写的URL。
        used_keys = set(draft.sourceKeys) | {key for section in draft.sections for key in section.sourceKeys}
        if 'financial' in used_keys:
            used_keys.update(key for key, value in available.items() if value['material_kind'] == 'financial')
        if 'chips' in available:
            used_keys.add('chips')
        if 'general_trade' in available:
            used_keys.add('general_trade')
        if tools.reduction_verified:
            used_keys.add('reduction_review')
            used_keys.update(record['sourceKey'] for record in tools.facts['reductions'])
        for key, value in available.items():
            if value['material_kind'] == 'calculation':
                used_keys.add(key)
                used_keys.update(value['content_json'].get('sourceKeys', []))
        used_keys.update(item['sourceKey'] for item in tools.facts['verifiedFacts'])
        ordered_keys = [key for key in available if key in used_keys]
        source_ids = {key: f'source_{index + 1}' for index, key in enumerate(ordered_keys)}
        sources = [{'id': source_ids[key], 'title': value['source_name'], 'publisher': value['source_name'],
                    'url': value['source_url'], 'publishedAt': utc(value['published_at']).isoformat() if value['published_at'] else None,
                    'fetchedAt': utc(value['fetched_at']).isoformat(),
                    'pages': sorted(tools.read_pages.get(key, set()) | {item['page'] for item in tools.facts['verifiedFacts'] if item['sourceKey'] == key})}
                   for key, value in available.items() if key in used_keys]
        instant = self.tasks.clock()
        section_titles = {'company': '已知公司资料', 'advantages': '行情与已有资料观察', 'risks': '当前风险'} if not public_documents else {}
        report = {'title': tools.record['stock']['name'] + ' · AI分析报告',
                  'stock': tools.record['stock'], 'snapshotId': tools.snapshot['id'], 'dataDate': tools.snapshot['data_date'].isoformat(),
                  'overview': overview, 'summary': draft.summary,
                  'sections': [{**section.model_dump(exclude={'sourceKeys'}), 'title': section_titles.get(section.id, section.title),
                                'sourceIds': [source_ids[key] for key in section.sourceKeys]} for section in draft.sections],
                  'metrics': metrics, 'valuation': valuation, 'shareholders': shareholders, 'sources': sources, 'dataQuality': 'partial' if missing else 'complete',
                  'missingInformation': missing, 'materialsAsOfAt': instant.isoformat(), 'simulated': False,
                  'researchStatus': 'researched' if public_documents else 'unavailable', 'reportScope': 'company' if public_documents else 'available_data',
                  'schemaVersion': 4, 'criticalNotes': critical, 'coverageNotes': coverage,
                  'verifiedFacts': [{**{name: value for name, value in item.items() if name != 'sourceKey'},
                                     'sourceId': source_ids[item['sourceKey']]} for item in tools.facts['verifiedFacts']],
                  'calculationDetails': tools.facts.get('calculations', [])}
        with self.tasks.db.transaction(serialized=True) as connection:
            task = self.tasks.assert_lease(connection, lease)
            connection.execute(t.results.insert().values(task_id=lease.id, payload_json=report, schema_version=4,
                provider=identity['provider'], model_name=identity['model'], prompt_version=identity.get('skillVersion', 'stock-analysis-v1'), calculation_version='v1',
                usage_json=usage, materials_as_of_at=instant, generated_at=instant))
            connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(status='succeeded', progress=100, finished_at=instant,
                lease_owner=None, lease_expires_at=None, options_json={**task['options_json'], 'stage': 'completed'}))
