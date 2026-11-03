"""本文件以LangGraph编排三个受控工具和股票分析技能，按显式配置连接真实模型。"""

import asyncio
import hashlib
import importlib.util
import json
from typing import Any
from urllib.parse import urlsplit

from stocksieve.modules.analysis.application.reports import ReportDraft
from stocksieve.modules.analysis.application.service import AnalysisTools
from stocksieve.shared.errors import DomainError
from stocksieve.shared.serialization import encode
from stocksieve.shared.settings import ROOT, Settings

SKILL = ROOT / 'skills/stock-analysis/SKILL.md'


class LangGraphRuntime:
    """沿用运行端口，以显式图节点限制工具集合，避免框架自动加入文件和命令工具。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def identity(self) -> dict[str, Any] | None:
        if not self.settings.ai_api_key or not self.settings.ai_model:
            return None
        if any(importlib.util.find_spec(name) is None for name in ('langgraph', 'langchain_openai')):
            return None
        url = self.settings.ai_base_url or 'https://api.openai.com/v1'
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DomainError('ANALYSIS_CONFIGURATION_INVALID', '模型服务地址配置无效。', 503)
        # 固定标识沿用已受理真实任务的格式，不再提供模式选择。
        return {'provider': parsed.hostname[:32], 'model': self.settings.ai_model, 'mode': 'live',
                'protocol': self.settings.ai_protocol, 'baseUrl': url, 'toolsSupported': self.settings.ai_tools_supported,
                'skillVersion': 'skill-' + hashlib.sha256(SKILL.read_bytes()).hexdigest()[:16],
                'toolVersion': 'research-finance-v1', 'maxSteps': self.settings.ai_max_steps,
                'maxOutputTokens': self.settings.ai_max_output_tokens, 'reasoningEffort': self.settings.ai_reasoning_effort}

    async def execute(self, tools: AnalysisTools) -> tuple[dict[str, Any], dict[str, Any]]:
        identity = self.identity()
        if identity is None:
            raise DomainError('ANALYSIS_UNAVAILABLE', '模型配置或运行依赖尚未就绪。', 503)
        skill = SKILL.read_text()
        if identity['skillVersion'] != 'skill-' + hashlib.sha256(skill.encode()).hexdigest()[:16]:
            raise DomainError('ANALYSIS_CONFIG_CHANGED', '受理后股票分析技能发生变化，请重新发起。', 503)
        from langchain_core.tools import StructuredTool
        from langchain_openai import ChatOpenAI
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode

        options: dict[str, Any] = {'model': self.settings.ai_model, 'api_key': self.settings.ai_api_key,
            'base_url': identity['baseUrl'], 'max_retries': 1, 'timeout': 90,
            'max_tokens': self.settings.ai_max_output_tokens,
            'use_responses_api': self.settings.ai_protocol == 'responses', 'store': False}
        if self.settings.ai_reasoning_effort:
            options['reasoning_effort'] = self.settings.ai_reasoning_effort
        model = ChatOpenAI(**options)
        functions = [
            StructuredTool.from_function(coroutine=tools.search_and_download, name='search_and_download',
                description='检索下载当前股票公告。query用六位代码加一个主题，例如600519 三季度报告；url仅用于下载已发现的披露原文。返回materialKey，标题不可当作证据。'),
            StructuredTool.from_function(coroutine=tools.read_document, name='read_document',
                description='按material_key阅读已下载HTML/PDF；keyword定位正文，page从1开始。返回资料键、页码和原文片段，只引用实际读到的内容。'),
            StructuredTool.from_function(coroutine=tools.calculate_finance, name='calculate_finance',
                description='固定金融公式。operation为risk_reward/growth/valuation/expected_return/scenarios；inputs字段见技能，source_keys为已读资料或本地资料键。禁止编造胜率。'),
        ]
        # 输入由后端准备；模型无需另行调用行情、数据库或服务器文件工具。
        facts = dict(tools.facts)
        if facts.get('chips'):
            facts['chips'] = {key: value for key, value in facts['chips'].items() if key != 'bins'}
        request = {'stockId': tools.task['stock_id'], 'snapshotId': tools.task['input_snapshot_id'],
                   'dataDate': tools.task['target_date'], 'materialsAsOfAt': tools.tasks.clock(), 'facts': facts,
                   'availableMaterials': [{'key': key, 'status': value['status'], 'title': value['source_name'],
                       'missing': value['missing_reason']} for key, value in tools.seen.items()],
                   'reportSchema': ReportDraft.model_json_schema()}
        messages: list[Any] = [{'role': 'system', 'content': skill}, {'role': 'user', 'content': encode(request)}]
        try:
            if self.settings.ai_tools_supported:
                bound = model.bind_tools(functions, parallel_tool_calls=False)

                async def generate(state: MessagesState) -> dict[str, Any]:
                    return {'messages': [await bound.ainvoke(state['messages'])]}

                def route(state: MessagesState) -> str:
                    return 'tools' if state['messages'][-1].tool_calls else END

                graph = StateGraph(MessagesState)
                graph.add_node('model', generate)
                graph.add_node('tools', ToolNode(functions, handle_tool_errors=False))
                graph.add_edge(START, 'model')
                graph.add_conditional_edges('model', route, ['tools', END])
                graph.add_edge('tools', 'model')
                output = await asyncio.wait_for(graph.compile().ainvoke({'messages': messages},
                    config={'recursion_limit': self.settings.ai_max_steps * 2 + 4}), timeout=self.settings.ai_timeout_seconds)
                history = output['messages']
            else:
                # 无工具协议的服务使用同一资料与技能，由后端先取最近定期报告再调用模型。
                async def prepared() -> Any:
                    discovered = await tools.search_and_download(tools.record['stock']['code'] + ' 三季度报告')
                    for document in discovered.get('documents', [])[:2]:
                        if document.get('materialKey'):
                            excerpt = await tools.read_document(document['materialKey'], keyword='主要财务')
                            messages.append({'role': 'user', 'content': encode(excerpt)})
                    messages.append({'role': 'user', 'content': '不支持交互工具，直接根据已提供资料完成JSON；未知内容保留缺失。'})
                    return await model.ainvoke(messages)
                history = [await asyncio.wait_for(prepared(), timeout=self.settings.ai_timeout_seconds)]
        except TimeoutError as error:
            raise DomainError('ANALYSIS_TIMEOUT', '分析超过处理时限，请稍后重试。', 503) from error
        except DomainError:
            raise
        except Exception as error:
            raise DomainError('ANALYSIS_PROVIDER_FAILED', '模型调用或工具执行未完成，请稍后重试或检查服务配置。', 503) from error
        content = history[-1].content
        if isinstance(content, list):
            content = ''.join(item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') in {'text', 'output_text'})
        try:
            text = content.strip()
            if text.startswith('```json') and text.endswith('```'):
                text = text[7:-3].strip()
            report = json.loads(text)
        except (ValueError, TypeError, AttributeError) as error:
            raise DomainError('ANALYSIS_INVALID_REPORT', '模型未返回有效JSON报告。', 503) from error
        usage = {'inputTokens': 0, 'outputTokens': 0, 'toolCalls': tools.calls}
        for message in history:
            values = getattr(message, 'usage_metadata', None) or {}
            usage['inputTokens'] += values.get('input_tokens', 0)
            usage['outputTokens'] += values.get('output_tokens', 0)
        return report, usage
