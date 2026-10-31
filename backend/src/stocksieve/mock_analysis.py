"""本文件生成明确标记的模拟分析文本，复用实际计算结果，不访问模型或联网服务。"""

from typing import Any

from .analysis import AnalysisTools


class MockAnalysisRuntime:
    """用于当前阶段接口联调；模拟内容不冒充真实模型判断或已核验的经营事实。"""

    def identity(self) -> dict[str, Any]:
        return {'mode': 'mock', 'provider': 'local-mock', 'model': 'template-v1', 'skillVersion': 'mock-analysis-v1'}

    async def execute(self, tools: AnalysisTools) -> tuple[dict[str, Any], dict[str, Any]]:
        kind = tools.task['kind']
        # 1. 文本按类型展示，数值和图表继续由分析服务从冻结输入补齐。
        reasons = {
            'fundamental': '本次为模拟分析，经营情况与股东减持风险尚未核验。',
            'chips': '本次为模拟分析，成本分布仅为历史量价估算。',
            'trade': '本次为模拟分析，先观察策略参考价与条件。',
        }
        explanations = {
            'fundamental': '已同步的行情可供后续分析参考；未核验的经营机遇、公告和减持情况不作结论。已有财务指标按实际资料展示，缺失时不补造。',
            'chips': '图形采用后端基于当前快照计算的成本分布，不能当作真实股东持仓；当前解释文字由模板生成。',
            'trade': '买入与退出参考来自所选快照中已入选的策略，条件未确认时继续等待；当前解释文字由模板生成。',
        }
        keys = [key for key in ('snapshot', 'financial', 'chips')
                if tools.seen.get(key, {}).get('status') == 'available']
        # 2. 模拟缺失项和用量明确保存，不生成公告链接、检索结果或模型消耗。
        return {
            'overview': {'decision': {'action': 'wait', 'label': '先等待', 'reason': reasons[kind]},
                         'opportunities': [], 'risks': ['本次为模拟分析，事件风险尚未核验。']},
            'summary': explanations[kind],
            'sections': [{'title': '模拟说明', 'text': explanations[kind]}],
            'sourceKeys': keys,
            'missingInformation': ['模拟模式：未调用真实模型或联网检索。'],
        }, {'inputTokens': 0, 'outputTokens': 0, 'toolCalls': tools.calls}
