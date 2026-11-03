"""本文件通过真实HTTP和隔离PostgreSQL验证三工具分析、文档证据及受控模型往返。"""

import asyncio
import json
from dataclasses import replace
from io import BytesIO
from typing import Any

import pytest
from aiohttp import web
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from stocksieve.interfaces.http.api import API
from stocksieve.modules.analysis.application.ports import SearchHit
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.domain.finance import calculate
from stocksieve.modules.analysis.infrastructure.agent import create_runtime
from stocksieve.modules.analysis.infrastructure.mock_runtime import MockAnalysisRuntime
from stocksieve.modules.analysis.infrastructure.reader import DocumentReader
from stocksieve.modules.analysis.infrastructure.research import CninfoSearch
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker

from .conftest import HTTPServer


class Research:
    """提供可核验的当前证券原文，隔离回归不访问真实资料渠道。"""

    def __init__(self, code: str) -> None:
        self.code = code
        self.url = 'https://static.cninfo.com.cn/finalpage/2026-10-29/fixture.PDF'

    async def search(self, query: str) -> list[SearchHit]:
        return [SearchHit('测试证券定期报告', self.url, '2026-10-29T00:00:00+08:00')]

    async def document(self, vendor: str, url: str, cache_seconds: int = 86400) -> Any:
        from datetime import UTC, datetime
        if 'fulltextSearch' in url:
            return json.dumps({'announcements': [
                {'secCode': self.code, 'announcementTitle': '<em>测试</em>2026年三季度报告', 'adjunctUrl': 'finalpage/2026-10-29/fixture.PDF', 'announcementTime': 1793232000000},
                {'secCode': '999999', 'adjunctUrl': 'finalpage/2026-10-29/other.PDF'},
                {'secCode': self.code, 'adjunctUrl': 'https://untrusted.invalid/other.pdf'},
            ]}).encode(), datetime.now(UTC)
        return f'<html><title>测试季报</title><body>证券代码：{self.code}。营业收入增长，经营风险仍需观察。</body></html>'.encode(), datetime.now(UTC)


def test_finance_formulas_reject_invalid_inputs_and_distinguish_probability() -> None:
    result = calculate('risk_reward', {'entry': 10, 'stop': 9, 'target': 12, 'costRatePercent': 1})['result']
    assert result['profitPerShare'] == 1.9 and result['lossPerShare'] == 1.1
    assert result['riskRewardRatio'] == pytest.approx(1.9 / 1.1)
    assert result['breakEvenWinRatePercent'] == pytest.approx(1.1 / 3 * 100)
    assert calculate('growth', {'current': 120, 'previous': 100})['result']['growthPercent'] == 20
    assert calculate('valuation', {'marketCap': 1000, 'netProfitTtm': 100})['result']['companyPeTtm'] == 10
    assert calculate('scenarios', {'entry': 10, 'targets': [8, 12]})['result']['scenarios'][0]['returnPercent'] == -20
    expected = calculate('expected_return', {'winProbability': .5, 'profit': 2, 'loss': 1, 'cost': .1, 'assumption': '条件情景'})
    assert expected['result']['expectedReturn'] == .4 and '不是' in expected['limitation']
    for values in ({'entry': True, 'stop': .5, 'target': 2}, {'entry': 10, 'stop': 10, 'target': 12}, {'entry': 'NaN', 'stop': 9, 'target': 12}):
        with pytest.raises(ValueError):
            calculate('risk_reward', values)


def test_reader_extracts_pdf_pages_and_search_filters_securities() -> None:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    for text in ('600000 revenue page one', '600000 risk page two'):
        pdf.drawString(40, 700, text)
        pdf.showPage()
    pdf.save()
    result = DocumentReader().parse(buffer.getvalue())
    assert result['pageCount'] == 2 and 'risk' in result['pages'][1]['text']
    html = DocumentReader().parse(b'<title>report</title><script>secret()</script><p>600000 revenue</p>')
    assert 'secret' not in html['text'] and html['title'] == 'report'
    hits = asyncio.run(CninfoSearch(Research('600000')).search('600000 三季度报告'))
    assert len(hits) == 1 and hits[0].url.startswith('https://static.cninfo.com.cn/')
    assert asyncio.run(CninfoSearch(Research('600000')).search('600000 2026年第三季度报告')) == hits
    assert not asyncio.run(CninfoSearch(Research('600000')).search('600000 2025年第三季度报告'))


@pytest.mark.parametrize('protocol', ['chat_completions', 'responses'])
def test_live_graph_uses_three_tools_and_preserves_evidence(context: Any, protocol: str) -> None:
    context.published()
    code = context.source.universe_stocks[0].code
    research = Research(code)
    model_calls: list[dict[str, Any]] = []
    material = ''

    async def model(request: web.Request) -> web.Response:
        nonlocal material
        body = await request.json()
        model_calls.append(body)
        exposed = body['tools']
        names = {tool['function']['name'] if 'function' in tool else tool['name'] for tool in exposed}
        assert names == {'search_and_download', 'read_document', 'calculate_finance'}
        # 从真正的模型协议请求中读取工具返回，下一步才能使用资料键。
        encoded = json.dumps(body, ensure_ascii=False)
        if len(model_calls) == 1:
            name, args = 'search_and_download', {'query': code + ' 三季度报告'}
        elif len(model_calls) == 2:
            import re
            material = re.search(r'page_[a-f0-9]{32}', encoded).group()
            name, args = 'read_document', {'material_key': material, 'keyword': '营业收入'}
        elif len(model_calls) == 3:
            name, args = 'calculate_finance', {'operation': 'risk_reward', 'inputs': {'entry': 10, 'stop': 9, 'target': 12, 'assumption': '条件情景'}, 'source_keys': ['general_trade']}
        else:
            sections = [{'id': key, 'title': title, 'headline': '依据原文和计算观察', 'text': '已读定期报告第1页，仍需核验事件风险。', 'sourceKeys': [material]}
                        for key, title in zip(('company', 'advantages', 'risks', 'holding', 'buy', 'avoid', 'sell'), ('公司基本资料', '优势', '风险', '持有', '买入', '不买', '卖出'), strict=True)]
            report = {'overview': {'decision': {'action': 'wait', 'label': '等待', 'reason': '事件风险仍需核验'}, 'holding': {'label': '观察', 'reason': '需要更多证据'}, 'opportunities': [], 'risks': ['事件风险待核验']},
                      'summary': '真实协议受控报告', 'sections': sections, 'sourceKeys': [material, 'general_trade'], 'missingInformation': ['行业PE未取得']}
            content = json.dumps(report, ensure_ascii=False)
            if protocol == 'responses':
                return web.json_response({'id': 'resp_fixture', 'object': 'response', 'created_at': 1793332800, 'model': 'fixture', 'status': 'completed',
                    'output': [{'type': 'message', 'id': 'msg_fixture', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': content, 'annotations': []}]}],
                    'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150}})
            return web.json_response({'id': 'chat_fixture', 'object': 'chat.completion', 'model': 'fixture', 'created': 1793332800,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}})
        call_id = 'call_' + str(len(model_calls))
        if protocol == 'responses':
            return web.json_response({'id': 'resp_' + call_id, 'object': 'response', 'created_at': 1793332800, 'model': 'fixture', 'status': 'completed',
                'output': [{'type': 'function_call', 'id': 'fc_' + call_id, 'call_id': call_id, 'name': name, 'arguments': json.dumps(args), 'status': 'completed'}],
                'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}})
        return web.json_response({'id': 'chat_' + call_id, 'object': 'chat.completion', 'model': 'fixture', 'created': 1793332800,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'finish_reason': 'tool_calls'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}})

    app = web.Application()
    app.router.add_post('/v1/chat/completions', model)
    app.router.add_post('/v1/responses', model)
    app.freeze()
    server = HTTPServer(app)
    try:
        settings = replace(context.api.settings, ai_mode='live', ai_api_key='fixture-private-key', ai_model='fixture', ai_protocol=protocol, ai_base_url=server.base_url + '/v1')
        runtime = create_runtime(settings)
        api = API(context.api.db, settings, context.api.registry, runtime, context.clock.now)
        context.api.runtime = runtime
        path = f'/api/v1/stocks/{context.source.universe_stocks[0].stock_id}/analyses'
        status, body, _ = context.request('POST', path, {}, key='live-tools-' + protocol)
        assert status == 202
        worker = Worker(api.tasks, Sync(api.tasks, api.registry), Analysis(api.tasks, runtime, research, research, DocumentReader()))
        assert asyncio.run(worker.run_once('analysis'))
        result = context.request('GET', '/api/v1/analyses/' + body['data']['id'])[1]['data']
        assert result['status'] == 'succeeded', result
        assert not result['simulated'] and not result['report']['simulated']
        assert len(model_calls) == 4
        source = next(item for item in result['report']['sources'] if item['url'] == research.url)
        assert source['publishedAt'] and source['id'] in result['report']['sections'][0]['sourceIds']
        status, raw, _ = context.request('GET', '/api/v1/analyses/' + result['id'] + '/export?format=pdf')
        assert status == 200 and '真实协议受控报告' in ''.join(page.extract_text() for page in PdfReader(BytesIO(raw)).pages)
        assert 'fixture-private-key' not in json.dumps(result)
    finally:
        server.close()


def test_live_report_requires_citation_of_public_evidence(context: Any) -> None:
    """取得资料却未在报告引用时必须拒绝，不能只凭联网成功标记研究完成。"""
    context.published()
    settings = replace(context.api.settings, ai_mode='live', ai_api_key='fixture', ai_model='fixture')
    identity = create_runtime(settings).identity()

    class MissingCitation(MockAnalysisRuntime):
        def identity(self) -> Any:
            return identity

        async def execute(self, tools: Any) -> Any:
            tools.save('uncited', 'research', '取得但未引用的公告', {'text': '受控原文'}, url='https://www.sse.com.cn/fixture')
            return await super().execute(tools)

    runtime = MissingCitation()
    api = API(context.api.db, settings, context.api.registry, runtime, context.clock.now)
    context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    status, body, _ = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key='missing-public-citation')
    assert status == 202
    worker = Worker(api.tasks, Sync(api.tasks, api.registry), Analysis(api.tasks, runtime))
    assert asyncio.run(worker.run_once('analysis'))
    task = context.request('GET', '/api/v1/analyses/' + body['data']['id'])[1]['data']
    assert task['status'] == 'failed' and task['error']['code'] == 'ANALYSIS_INVALID_REPORT'


def test_real_provider_and_public_documents(context: Any, request: Any) -> None:
    if not request.config.getoption('--live-model'):
        pytest.skip('真实模型与公开资料验收需显式--live-model。')
    from datetime import UTC, datetime

    from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
    from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
    from stocksieve.shared.settings import Settings

    # 使用真实证券身份和受控测试行情；正式行情另在部署后复验。
    context.clock.value = datetime.now(UTC)
    context.token = context.api.auth.login('admin', 'testpass123')['accessToken']
    first = context.source.universe_stocks[0]
    context.source.universe_stocks = (replace(first, name='浦发银行'), *context.source.universe_stocks[1:])
    context.published()
    private = Settings.from_env()
    settings = replace(context.api.settings, ai_mode='live', ai_base_url=private.ai_base_url,
        ai_api_key=private.ai_api_key, ai_model=private.ai_model, ai_protocol=private.ai_protocol,
        ai_reasoning_effort=private.ai_reasoning_effort, ai_max_steps=20)
    runtime = create_runtime(settings)
    api = API(context.api.db, settings, context.api.registry, runtime, context.clock.now)
    context.api.runtime = runtime
    path = f'/api/v1/stocks/{first.stock_id}/analyses'
    status, body, _ = context.request('POST', path, {}, key='real-provider-check')
    assert status == 202

    async def run() -> None:
        http = SourceHTTP(SourcePolicy(private.rate_dir, private.request_interval, private.source_daily_limit), private.state_dir / 'source-cache')
        try:
            worker = Worker(api.tasks, Sync(api.tasks, api.registry), Analysis(api.tasks, runtime, http, CninfoSearch(http), DocumentReader()))
            assert await worker.run_once('analysis')
        finally:
            await http.close()

    asyncio.run(run())
    result = context.request('GET', '/api/v1/analyses/' + body['data']['id'])[1]['data']
    assert result['status'] == 'succeeded', result.get('error')
    assert not result['report']['simulated']
    assert any(item['url'] and item['url'].startswith('https://static.cninfo.com.cn/') for item in result['report']['sources'])
    print(json.dumps({'realModel': settings.ai_model, 'sections': len(result['report']['sections']), 'sourceCount': len(result['report']['sources']), 'simulated': result['simulated']}, ensure_ascii=False))
