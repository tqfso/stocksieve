"""本文件按七个问题生成模拟报告，引用实际计算，不虚构联网事实或模型消耗。"""

from typing import Any

from stocksieve.modules.analysis.application.service import AnalysisTools


class MockAnalysisRuntime:
    """模拟经营判断保持待核验；技术观察只采用本次冻结的计算结果。"""

    def identity(self) -> dict[str, Any]:
        return {'mode': 'mock', 'provider': 'local-mock', 'model': 'report-template-v4', 'skillVersion': 'unified-report-v4'}

    async def execute(self, tools: AnalysisTools) -> tuple[dict[str, Any], dict[str, Any]]:
        technical = tools.facts['generalTrade']
        plan = technical['plan']
        chips = tools.facts.get('chips')
        chip_text = f"当前估算平均成本为{chips['averageCost']:.2f}元，现价及以下成本占比约{chips['profitableRatio']:.1f}%；仅作成本结构参考。" if chips else '筹码资料不足，不能评估成本结构。'
        rows = [
            ('company', '公司基本资料', tools.record['stock']['name'] + '（' + tools.record['stock']['code'] + '）', '公司类型、主营业务及股东户数尚未核验。成长、周期或期权型公司的判断需要连续业绩、业务及行业证据，不能仅凭题材标签分类。期权型指潜力尚未兑现、不确定性高的公司。', ['snapshot']),
            ('advantages', '优势、壁垒与市场关注', '竞争优势与当前市场关注方向待核验', '已同步行业和题材仅提供研究线索，不代表已证实的壁垒或资金炒作方向。后续需核验公司业务、行业竞争和近期公开资料。', ['snapshot']),
            ('risks', '问题与风险', '股东户数及集中趋势、业绩和事件风险待核验', '股东户数未采集，本期、上期户数及变化比例未知，暂不能判断持股是否趋于集中或分散。未联网不代表没有诉讼、处罚或减持。公司与行业PE尚不能完整对比；券商目标价及其日期、机构亦未取得。' + chip_text, ['chips'] if chips else []),
            ('holding', '是否建议持有', '资料不足，暂不作持有判断', '持有判断需要核验原有投资逻辑、盈利质量和重大风险。当前只展示模拟分析，不能把技术趋势或入选规则等同于适合继续持有。', []),
            ('buy', '适合买入的条件与区间', plan['entryPrice'], plan['entryCondition'] + ' ' + plan['considerIf'] + ' 此为程序计算的技术观察，基本面及事件风险核验前不形成实际买入建议。', ['general_trade']),
            ('avoid', '什么情况下不适合买入', '关键风险未核验，先不参与', plan['avoidIf'] + ' 如果只因热门题材、券商目标价或低PE就买入，当前资料不能支持这一决定。', ['general_trade']),
            ('sell', '买入后什么条件下卖出', '逻辑失效或触发风险条件时重新评估', plan['exitCondition'] + ' 风险复查参考：' + plan['exitPrice'] + '。后续若业绩或业务兑现逻辑被证伪，或出现重大诉讼、减持等风险，应重新评估；不编造止盈目标。', ['general_trade']),
        ]
        return {'overview': {'decision': {'action': 'wait', 'label': '先核验风险', 'reason': '公司与行业资料尚未核验，先看技术观察，暂不形成真实投资判断。'},
                    'holding': {'label': '暂不判断持有', 'reason': '业绩、经营与重大风险缺少核验证据。'},
                    'opportunities': [], 'risks': ['股东户数、诉讼、减持和经营风险尚未核验。']},
                'summary': '【模拟】本报告读取已同步行情、均线及可用筹码和财务资料；七项分析文字由模板生成，未调用真实模型或联网检索。',
                'sections': [{'id': key, 'title': title, 'headline': headline, 'text': text, 'sourceKeys': sources} for key, title, headline, text, sources in rows],
                'sourceKeys': [key for key in ('snapshot', 'financial', 'chips', 'general_trade') if tools.seen.get(key, {}).get('status') == 'available'],
                'missingInformation': ['模拟模式：未调用真实模型或联网检索。', '公司分类、优势与壁垒、业绩、股东户数及集中趋势、诉讼、减持、券商目标价与行业PE待核验。']}, {'inputTokens': 0, 'outputTokens': 0, 'toolCalls': tools.calls}
