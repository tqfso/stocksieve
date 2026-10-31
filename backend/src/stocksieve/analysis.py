"""本文件执行分析技能的资料工具、计算与报告落库，固定输入并校验引用来源。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urlsplit

from sqlalchemy import select, update

from . import tables as t
from .analysis_math import chip_distribution, financial_metrics
from .database import utc
from .domain import Bar
from .errors import DomainError, MissingData
from .http import encode
from .report_schema import ReductionEvidence, ReportDraft
from .sources import SourceDeferred, SourceHTTP
from .sync import upsert
from .tasks import Lease, Tasks


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str


class SearchProvider(Protocol):
    async def search(self, query: str) -> list[SearchHit]: ...


class AgentRuntime(Protocol):
    def identity(self) -> dict[str, Any] | None: ...
    async def execute(self, tools: 'AnalysisTools') -> tuple[dict[str, Any], dict[str, Any]]: ...


class PageText(HTMLParser):
    """读取页面可见正文，不运行脚本，也不把网页中的指令当成系统指令。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.skip = 0
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {'script', 'style', 'noscript'}:
            self.skip += 1
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {'script', 'style', 'noscript'}:
            self.skip = max(0, self.skip - 1)
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.parts.append(data.strip())
            if self.in_title:
                self.title_parts.append(data.strip())


class AnalysisTools:
    """工具仅访问当前任务输入和批准的公开域名，所有远程读取共享供应商频控。"""

    def __init__(self, tasks: Tasks, lease: Lease, http: SourceHTTP | None = None,
                 search: SearchProvider | None = None) -> None:
        self.tasks, self.lease, self.db = tasks, lease, tasks.db
        self.http, self.search = http, search
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
        self.budget()
        self.stage('researching', 15)
        record = {key: value for key, value in self.record.items() if key != 'signalRows'}
        record['snapshot'] = self.tasks.market.snapshot_view(self.snapshot)
        record['signals'] = [self.tasks.market.signal_view(row, detail=True) for row in self.record['signalRows'].values()]
        result = self.save('snapshot', 'market', '已同步行情与策略快照', record, published_at=utc(self.snapshot['published_at']))
        self.facts['snapshot'] = record
        if self.task['kind'] == 'chips':
            try:
                distribution = chip_distribution(self.history, self.snapshot['data_date'])
                self.facts['chips'] = distribution
                self.save('chips', 'chips', '换手衰减成本估算 v1', distribution)
            except MissingData as error:
                raise DomainError('ANALYSIS_DATA_UNAVAILABLE', str(error), 409) from error
        return result

    async def financial_facts(self) -> dict[str, Any]:
        self.budget()
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
        except MissingData as error:
            return self.save('financial', 'financial', '合并财报资料', missing=str(error))
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
            hits = await self.search.search(query)
        except (SourceDeferred, DomainError):
            return self.save(key, 'research', '公开资料检索', missing='公开检索渠道暂不可用或处于频控。')
        # 搜索摘要只用于发现原文，不能直接作为财务或减持事实依据。
        return {'status': 'discovered', 'results': [{'title': hit.title, 'url': hit.url} for hit in hits[:10]]}

    async def read_public(self, url: str) -> dict[str, Any]:
        self.budget()
        key = 'page_' + hashlib.sha256(url.encode()).hexdigest()[:32]
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return {'status': 'missing', 'missing': '网页地址格式无效。'}
        allowed = ('sse.com.cn', 'szse.cn', 'bse.cn', 'cninfo.com.cn')
        if parsed.scheme != 'https' or parsed.username or parsed.password or port not in {None, 443} or not parsed.hostname or not any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in allowed):
            return {'status': 'missing', 'missing': '网页地址不属于已配置的公开披露渠道。'}
        if not self.http:
            return self.save(key, 'research', parsed.hostname, missing='公开网页读取工具尚未就绪。')
        with self.db.transaction() as connection:
            previous = connection.execute(select(t.materials).where(t.materials.c.task_id == self.lease.id, t.materials.c.material_key == key, t.materials.c.status == 'available')).mappings().first()
        if previous:
            self.seen[key] = dict(previous)
            return {'key': key, 'status': 'available', 'data': previous['content_json']}
        try:
            raw, fetched_at = await self.http.document(parsed.hostname, url)
            if raw.startswith(b'%PDF'):
                return self.save(key, 'research', parsed.hostname, missing='资料为PDF，尚未取得可核验的文本。')
            parser = PageText(); parser.feed(raw.decode('utf-8', errors='replace'))
            content = '\n'.join(parser.parts)[:100000]
            if not content.strip():
                return self.save(key, 'research', parsed.hostname, missing='网页未取得可用正文。')
            return self.save(key, 'research', ''.join(parser.title_parts)[:200] or parsed.hostname,
                             {'url': url, 'text': content}, url=url, fetched_at=fetched_at)
        except (SourceDeferred, DomainError):
            return self.save(key, 'research', parsed.hostname, missing='该公开资料暂不可读取或处于频控。')

    async def review_reductions(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """股东、状态与数量语境须来自已读原文；空结果不能证明没有减持风险。"""
        self.budget()
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

    def __init__(self, tasks: Tasks, runtime: AgentRuntime, http: SourceHTTP | None = None,
                 search: SearchProvider | None = None) -> None:
        self.tasks, self.runtime, self.http, self.search = tasks, runtime, http, search

    async def run(self, lease: Lease) -> None:
        tools = AnalysisTools(self.tasks, lease, self.http, self.search)
        if tools.task['options_json'].get('runtime') != self.runtime.identity():
            raise DomainError('ANALYSIS_CONFIG_CHANGED', '受理后模型配置发生变化，请重新发起分析。', 503)
        identity = tools.task['options_json']['runtime']
        simulated = identity.get('mode') == 'mock'
        await tools.snapshot_facts()
        if tools.task['kind'] == 'fundamental':
            await tools.financial_facts()
        tools.stage('analyzing', 40)
        raw, usage = await self.runtime.execute(tools)
        try:
            draft = ReportDraft.model_validate(raw)
        except ValueError as error:
            raise DomainError('ANALYSIS_INVALID_REPORT', '模型返回的报告格式不符合要求。', 503) from error
        available = {key: value for key, value in tools.seen.items() if value['status'] == 'available'}
        if any(key not in available for key in draft.sourceKeys):
            raise DomainError('ANALYSIS_INVALID_REPORT', '报告引用了未取得的资料。', 503)
        if not simulated and tools.task['kind'] == 'fundamental' and not tools.facts.get('financial') and not any(value['material_kind'] == 'research' for value in available.values()):
            raise DomainError('ANALYSIS_SOURCES_UNAVAILABLE', '没有足够的经营或财务资料，无法生成基本面分析。', 503)
        tools.stage('writing', 85)
        overview = draft.overview.model_dump()
        missing = list(dict.fromkeys(draft.missingInformation + [value['missing_reason'] for value in tools.seen.values() if value['status'] != 'available']))
        if tools.task['kind'] == 'fundamental' and not tools.reduction_verified:
            text = '股东减持计划及实际进展未核验。'
            missing = list(dict.fromkeys([text, *missing]))
            if overview['decision']['action'] != 'avoid':
                overview['decision'] = {'action': 'wait', 'label': '暂缓买入', 'reason': '股东减持风险尚未核实，先等待资料补齐。'}
            overview['risks'] = [text, *overview['risks']][:2]
        metrics = []
        financial = tools.facts.get('financial')
        if financial:
            for label, field in [('营业收入（TTM）', 'revenueTtm'), ('归母净利润（TTM）', 'netProfitParentTtm'), ('PE（TTM）', 'peTtm')]:
                value = financial[field]
                metrics.append({'label': label, 'value': '—' if value is None else f'{value / 100000000:.2f}亿元' if field != 'peTtm' else f'{value:.2f}倍', 'detail': financial['periodEnd']})
        if tools.task['kind'] == 'trade':
            overview['pricePlan'] = self.price_plan(tools)
            if overview['pricePlan']['waiting']:
                overview['decision'] = {'action': 'wait', 'label': '等待突破', 'reason': '当前还未确认突破，先不买入。'}
        # 引用地址与时间只采用后端保存记录，不能接受模型自己编写的URL。
        used_keys = set(draft.sourceKeys)
        if 'financial' in used_keys:
            used_keys.update(key for key, value in available.items() if value['material_kind'] == 'financial')
        if 'chips' in available:
            used_keys.add('chips')
        if tools.reduction_verified:
            used_keys.add('reduction_review')
            used_keys.update(record['sourceKey'] for record in tools.facts['reductions'])
        sources = [{'title': value['source_name'], 'url': value['source_url'], 'publishedAt': utc(value['published_at']).isoformat() if value['published_at'] else None,
                    'fetchedAt': utc(value['fetched_at']).isoformat()} for key, value in available.items() if key in used_keys]
        instant = self.tasks.clock()
        report = {'title': f"{tools.record['stock']['name']} · { {'fundamental': '机会与风险', 'chips': '筹码分布（估算）', 'trade': '买卖建议'}[tools.task['kind']]}",
                  'overview': overview, 'summary': draft.summary, 'sections': [section.model_dump() for section in draft.sections],
                  'metrics': metrics, 'sources': sources, 'dataQuality': 'partial' if missing else 'complete',
                  'missingInformation': missing, 'materialsAsOfAt': instant.isoformat(), 'simulated': simulated}
        if simulated:
            report['title'] = '【模拟】' + report['title']
            report['summary'] = '模拟分析，未调用真实模型。' + report['summary']
        if 'chips' in tools.facts:
            report['chipDistribution'] = tools.facts['chips']
        with self.tasks.db.transaction(serialized=True) as connection:
            task = self.tasks.assert_lease(connection, lease)
            connection.execute(t.results.insert().values(task_id=lease.id, payload_json=report, schema_version=1,
                provider=identity['provider'], model_name=identity['model'], prompt_version=identity.get('skillVersion', 'stock-analysis-v1'), calculation_version='v1',
                usage_json=usage, materials_as_of_at=instant, generated_at=instant))
            connection.execute(update(t.tasks).where(t.tasks.c.id == lease.id).values(status='succeeded', progress=100, finished_at=instant,
                lease_owner=None, lease_expires_at=None, options_json={**task['options_json'], 'stage': 'completed'}))

    def price_plan(self, tools: AnalysisTools) -> dict[str, Any]:
        code = tools.task['strategy_code']
        metrics = tools.record['signalRows'][code]['metrics_json']
        waiting = code in {'trend_wait', 'base_wait'}
        entry = metrics['rangeUpper'] if waiting else metrics['breakoutReference'] if code == 'short' else metrics['ma20'] if code == 'swing' else metrics['ma60']
        exit_price = metrics['rangeLower'] if waiting else metrics['breakoutReference'] if code == 'short' else metrics['pullbackLow'] if code == 'swing' else metrics['ma60']
        return {'waiting': waiting, 'entryLabel': '等待突破参考价' if waiting else '参考买入位置',
                'entryPrice': f'{entry:.2f} 元', 'exitPrice': f'{exit_price:.2f} 元' + ('（动态均价）' if code == 'long' else ''),
                'entryCondition': '突破后重新检查短线全部条件。' if waiting else '原有策略条件需继续满足，按参考位置观察价格。',
                'exitCondition': '连续5天低于各自60日均价或均线关系转弱时复查。' if code == 'long' else '收盘低于该参考价时，重新评估本次信号。',
                'considerIf': '看好当前策略所依据的趋势，也能接受对应波动时，可按参考价位和条件考虑。',
                'avoidIf': '关键条件未成立、已经失效或你不能接受对应风险时，先别买。'}
