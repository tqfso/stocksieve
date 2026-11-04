"""本文件仅为隔离测试提供受控模型响应，业务运行环境不加载此文件。"""

import asyncio
from typing import Any

from stocksieve.modules.analysis.application.service import Analysis, AnalysisTools
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker


class FixtureAnalysisRuntime:
    """固定报告契约及资料引用，避免回归测试调用真实模型或生成投资判断。"""

    def __init__(self, model: str = 'fixture-v1') -> None:
        self.model = model

    def identity(self) -> dict[str, Any]:
        return {'provider': 'fixture', 'model': self.model, 'protocol': 'chat_completions'}

    async def execute(self, tools: AnalysisTools) -> tuple[dict[str, Any], dict[str, Any]]:
        text = tools.record['stock']['code'] + '\n单位：元\n营业收入1,200,000,000.00 -10.00%\n归属于上市公司股东的净利润150,000,000.00 9.01%\n经营活动产生的现金流量净额10,000,000.00 -7.87%\n报告期末普通股股东总数88,254户'
        tools.save('fixture_evidence', 'research', '受控证券：2026年三季度报告',
                   {'text': text, 'pages': [{'page': 1, 'text': text}], 'pageCount': 1},
                   url='https://www.sse.com.cn/fixture/disclosure')
        chapters = [('company', '公司做什么'), ('advantages', '有什么优势'), ('risks', '主要风险'),
                    ('holding', '已有持仓怎么办'), ('entry', '现在适合买入吗'), ('sell', '何时考虑卖出')]
        facts = []
        for metric, value, unit, basis, quote, yoy in [
            ('shareholder_count', 88254, '户', 'all', '报告期末普通股股东总数88,254户', None),
            ('revenue', 1200000000, '元', 'consolidated', '营业收入1,200,000,000.00 -10.00%', -10),
            ('net_profit', 150000000, '元', 'consolidated', '归属于上市公司股东的净利润150,000,000.00 9.01%', 9.01),
            ('operating_cashflow', 10000000, '元', 'consolidated', '经营活动产生的现金流量净额10,000,000.00 -7.87%', -7.87)]:
            facts.append({'metric': metric, 'value': value, 'unit': unit, 'basis': basis,
                          'periodEnd': '2026-09-30', 'sourceKey': 'fixture_evidence', 'page': 1, 'evidence': quote, 'yoy': yoy})
        keys = ['fixture_evidence', *[key for key in ('snapshot', 'financial', 'chips', 'general_trade')
                                    if tools.seen.get(key, {}).get('status') == 'available']]
        return ({'overview': {'decision': {'action': 'wait', 'label': '受控测试', 'reason': '测试响应不提供投资判断。'},
                            'holding': {'label': '测试持有结论', 'reason': '只验证报告契约和数据流。'},
                            'opportunities': [], 'risks': ['受控响应仅用于隔离测试。'], 'watch': ['关注下一次可核对的披露。']},
                'summary': '受控模型响应，用于验证页面、引用和导出，不提供投资判断。',
                'sections': [{'id': key, 'title': title, 'headline': '受控章节', 'text': '本章用于验证报告结构、资料引用及文件输出。',
                              'sourceKeys': ['fixture_evidence', 'snapshot']} for key, title in chapters],
                'sourceKeys': keys, 'facts': facts, 'missingInformation': ['隔离测试不查询真实公开资料。']},
                {'inputTokens': 10, 'outputTokens': 10, 'toolCalls': tools.calls})


def generate(context: Any, *, runtime: Any = None, key: str = 'unified-report-test') -> dict[str, Any]:
    """通过真实项目HTTP接口受理报告，再由测试进程驱动worker完成。"""
    runtime = runtime or FixtureAnalysisRuntime()
    context.api.runtime = runtime
    stock = context.source.universe_stocks[0].stock_id
    status, body, _ = context.request('POST', f'/api/v1/stocks/{stock}/analyses', {}, key=key)
    assert status == 202, body
    worker = Worker(context.api.tasks, Sync(context.api.tasks, context.api.registry), Analysis(context.api.tasks, runtime))
    assert asyncio.run(worker.run_once('analysis'))
    result = context.request('GET', '/api/v1/analyses/' + body['data']['id'])[1]['data']
    assert result['status'] == 'succeeded', result
    return result
