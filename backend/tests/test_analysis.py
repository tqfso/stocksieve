"""本文件通过HTTP验证报告契约、来源校验、实际财报计算与导出引用，不请求外部服务。"""

from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any
from zipfile import ZipFile

from pypdf import PdfReader
from stocksieve.infrastructure.persistence import schema as t
from stocksieve.modules.analysis.infrastructure.mock_runtime import MockAnalysisRuntime

from .test_mock_analysis import generate


class EvidenceRuntime(MockAnalysisRuntime):
    """受控资料验证链接在页面与文件中都来自实际保存内容。"""

    async def execute(self, tools: Any) -> Any:
        tools.save('announcement', 'research', '测试公告原文', {'text': '测试公司公开资料'}, url='https://www.sse.com.cn/fixture/disclosure')
        result, usage = await super().execute(tools)
        result['sourceKeys'].append('announcement')
        result['sections'][2]['sourceKeys'].append('announcement')
        return result, usage


def test_citations_are_preserved_as_word_and_pdf_hyperlinks(context: Any) -> None:
    context.published()
    task = generate(context, runtime=EvidenceRuntime())
    report = task['report']
    url = 'https://www.sse.com.cn/fixture/disclosure'
    citation = next(item for item in report['sources'] if item['url'] == url)
    assert citation['id'] in report['sections'][2]['sourceIds']
    base = '/api/v1/analyses/' + task['id'] + '/export?format='
    word = context.request('GET', base + 'docx')[1]
    with ZipFile(BytesIO(word)) as archive:
        assert url in archive.read('word/_rels/document.xml.rels').decode()
        assert 'w:hyperlink' in archive.read('word/document.xml').decode()
    pdf = PdfReader(BytesIO(context.request('GET', base + 'pdf')[1]))
    links = [str(annotation.get_object()['/A']['/URI']) for page in pdf.pages for annotation in page.get('/Annots', []) if '/A' in annotation.get_object()]
    assert url in links


def test_saved_financials_produce_ttm_pe_but_do_not_invent_industry_pe(context: Any) -> None:
    context.published()
    stock = context.source.universe_stocks[0].stock_id
    with context.api.db.transaction() as connection:
        for end, revenue, profit in [(date(2026, 9, 30), 1200000000, 150000000), (date(2025, 12, 31), 1500000000, 200000000), (date(2025, 9, 30), 1000000000, 100000000)]:
            connection.execute(t.financials.insert().values(stock_id=stock, period_start=date(end.year, 1, 1), period_end=end,
                statement_scope='consolidated', revenue_ytd=revenue, net_profit_parent_ytd=profit,
                extra_json={'currency': 'CNY'}, source='测试财报', source_url='https://www.sse.com.cn/fixture/financial',
                published_at=datetime(2026, 10, 29, tzinfo=timezone.utc), fetched_at=context.clock.now(),
                content_hash='a' * 64, raw_payload_json={'revenue': revenue, 'profit': profit}))
    report = generate(context)['report']
    assert report['valuation']['companyPe'] == 40
    assert report['valuation']['industryPe'] is None and report['valuation']['status'] == 'unavailable'
    assert any(item['label'] == '公司 PE（TTM）' and item['value'] == '40.00 倍' for item in report['metrics'])
    assert any(item['url'] == 'https://www.sse.com.cn/fixture/financial' for item in report['sources'])


def test_unavailable_citation_and_incomplete_report_are_rejected(context: Any) -> None:
    import asyncio

    from stocksieve.modules.analysis.application.service import Analysis
    from stocksieve.modules.ingestion.application.service import Sync
    from stocksieve.modules.jobs.application.worker import Worker

    class InvalidRuntime(MockAnalysisRuntime):
        """模拟模型违规输出，仍须经过相同的报告校验。"""

        invalid = 'source'

        async def execute(self, tools: Any) -> Any:
            result, usage = await super().execute(tools)
            if self.invalid == 'source':
                result['sections'][0]['sourceKeys'] = ['invented-material']
            else:
                result['sections'] = result['sections'][:6]
            return result, usage

    context.published()
    runtime = InvalidRuntime()
    context.api.runtime = runtime
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, runtime))
    stock = context.source.universe_stocks[0].stock_id
    for invalid in ('source', 'sections'):
        runtime.invalid = invalid
        task = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key='invalid-report-' + invalid)[1]['data']
        assert asyncio.run(worker.run_once('analysis'))
        result = context.request('GET', '/api/v1/analyses/' + task['id'])[1]['data']
        assert result['status'] == 'failed' and result['report'] is None
        assert result['error']['code'] == 'ANALYSIS_INVALID_REPORT'
