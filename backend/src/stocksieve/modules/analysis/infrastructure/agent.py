"""本文件以LangGraph编排三个受控工具和股票分析技能，按显式配置连接真实模型。"""

import asyncio
import hashlib
import importlib.util
import json
import time
from typing import Any
from urllib.parse import urlsplit

from stocksieve.modules.analysis.application.evidence import verify_facts
from stocksieve.modules.analysis.application.facts import (
    complete_financial_facts,
    shareholder_facts,
)
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
        self.last_failure: dict[str, Any] = {}

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
        public_ready = any(value['material_kind'] == 'research' and value['status'] == 'available' for value in tools.seen.values())
        if not public_ready and (tools.search is None or tools.research_retry_at is not None):
            # 来源确实不可用时只保留计算能力，避免反复检索拖累已有数据分析。
            functions = [item for item in functions if item.name == 'calculate_finance']
        # 输入由后端准备；模型无需另行调用行情、数据库或服务器文件工具。
        facts = dict(tools.facts)
        if facts.get('chips'):
            facts['chips'] = {key: value for key, value in facts['chips'].items() if key != 'bins'}
        request = {'stockId': tools.task['stock_id'], 'snapshotId': tools.task['input_snapshot_id'],
                   'dataDate': tools.task['target_date'], 'materialsAsOfAt': tools.tasks.clock(), 'facts': facts,
                   'availableMaterials': [{'key': key, 'status': value['status'], 'title': value['source_name'],
                       'missing': value['missing_reason']} for key, value in tools.seen.items()],
                   'reportSchema': ReportDraft.model_json_schema(), 'availableTools': [item.name for item in functions]}
        messages: list[Any] = [{'role': 'system', 'content': skill}, {'role': 'user', 'content': encode(request)}]
        started = time.monotonic()
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
            self.last_failure = {'type': type(error).__name__, 'status': getattr(error, 'status_code', None),
                                 'causeType': type(error.__cause__).__name__ if error.__cause__ else None}
            raise DomainError('ANALYSIS_PROVIDER_FAILED', '报告暂未生成，请稍后重试；若持续失败请联系管理员。', 503) from error
        repairs = 0
        for attempt in range(2):
            try:
                content = history[-1].content
                if isinstance(content, list):
                    content = ''.join(item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') in {'text', 'output_text'})
                text = content.strip()
                if text.startswith('```json') and text.endswith('```'):
                    text = text[7:-3].strip()
                report = json.loads(text)
                draft = ReportDraft.model_validate(report)
                verify_facts(draft.facts, tools.seen, tools.tasks.clock().date())
                break
            except (ValueError, TypeError, AttributeError, DomainError) as error:
                if attempt:
                    raise DomainError('ANALYSIS_INVALID_REPORT', '报告结构或事实摘录未通过校验，请稍后重试。', 503) from error
                # 只允许一次最终纠正，复用已读证据，不重新联网或无限增加模型调用。
                remaining = self.settings.ai_timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise DomainError('ANALYSIS_TIMEOUT', '分析超过处理时限。', 503) from error
                context = history if self.settings.ai_tools_supported else [*messages, *history]
                diagnostic = error.message if isinstance(error, DomainError) else str(error)[:1200]
                correction = {'role': 'user', 'content': '最终JSON未通过校验：' + diagnostic + '。仅纠正完整JSON，六章正文各不超过240字，摘要不超过200字。facts只复制已读原文的原始值、单位与对应累计期间。无法可靠核对的事实须从facts和正文同时删除；不要调用新工具，不要编造。'}
                try:
                    repaired = await asyncio.wait_for(model.ainvoke([*context, correction]), timeout=remaining)
                except TimeoutError as failed:
                    raise DomainError('ANALYSIS_TIMEOUT', '报告纠正超过处理时限。', 503) from failed
                except Exception as failed:
                    raise DomainError('ANALYSIS_PROVIDER_FAILED', '模型未完成报告纠正。', 503) from failed
                history.append(repaired)
                repairs += 1
        # 派生出新TTM或户数变化时，带着程序结果精简定稿，避免正文仍把已知数据写成缺失。
        verified = verify_facts(draft.facts, tools.seen, tools.tasks.clock().date())
        derived = {'verifiedFacts': verified}
        financial_ready = complete_financial_facts(tools.record, derived)
        holders = shareholder_facts(derived)
        reviews = 0
        if financial_ready or holders['previousCount'] is not None:
            remaining = self.settings.ai_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise DomainError('ANALYSIS_TIMEOUT', '报告整理超过处理时限。', 503)
            context = history if self.settings.ai_tools_supported else [*messages, *history]
            prompt = ('后端已核对事实并完成派生计算：' + encode({'financial': derived.get('financial'), 'shareholders': holders})
                + '。请据此精简定稿，只返回overview、summary、sections、missingInformation四个JSON字段。不要重写facts、sourceKeys或其他字段；保留实际引用。'
                  '已计算出的指标不能再写成缺失，行业估值缺失仍需保留。摘要最多200字，六章正文各最多240字，不重复列出数字与限制。')
            try:
                final = await asyncio.wait_for(model.ainvoke([*context, {'role': 'user', 'content': prompt}]), timeout=remaining)
                content = final.content
                if isinstance(content, list):
                    content = ''.join(item.get('text', '') for item in content if isinstance(item, dict))
                text = content.strip()
                if text.startswith('```json') and text.endswith('```'):
                    text = text[7:-3].strip()
                patch = json.loads(text)
                report = {**report, **{key: patch[key] for key in ('overview', 'summary', 'sections', 'missingInformation')}}
                ReportDraft.model_validate(report)
                history.append(final)
                reviews = 1
            except TimeoutError as error:
                raise DomainError('ANALYSIS_TIMEOUT', '报告整理超过处理时限。', 503) from error
            except Exception as error:
                raise DomainError('ANALYSIS_INVALID_REPORT', '报告整理未完成，请稍后重试。', 503) from error
        usage = {'inputTokens': 0, 'outputTokens': 0, 'toolCalls': tools.calls, 'repairs': repairs, 'reviews': reviews}
        for message in history:
            values = getattr(message, 'usage_metadata', None) or {}
            usage['inputTokens'] += values.get('input_tokens', 0)
            usage['outputTokens'] += values.get('output_tokens', 0)
        return report, usage
