"""本文件用本地模型验证已安装SDK的技能图和双协议配置，不请求真实模型服务。"""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from stocksieve.agent import DeepAgentsRuntime
from stocksieve.config import Settings

PAYLOAD = {'overview': {'decision': {'action': 'wait', 'label': '先等待', 'reason': '这是本地SDK结构测试。'}, 'opportunities': [], 'risks': ['需进一步核实。']},
           'summary': '本地结构测试，不代表真实分析。', 'sections': [{'title': '依据', 'text': '只验证图工作流和返回解析。'}], 'sourceKeys': ['snapshot'], 'missingInformation': []}


class LocalChat(BaseChatModel):
    """通过已安装的模型抽象运行图，不创建任何网络客户端。"""

    use_responses_api: bool = False
    received_messages: ClassVar[list[Any]] = []
    protocol_flags: ClassVar[list[bool]] = []

    @property
    def _llm_type(self) -> str:
        return 'stocksieve-test-only'

    def bind_tools(self, tools: Any, **kwargs: Any) -> 'LocalChat':
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.received_messages.extend(messages)
        self.protocol_flags.append(self.use_responses_api)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps(PAYLOAD)))])


class Tools:
    calls = 0
    task = {'stock_id': 'SSE.600000', 'kind': 'chips', 'strategy_code': None,
            'input_snapshot_id': 'test-snapshot', 'target_date': date(2026, 10, 30)}
    record = {'stock': {'code': '600000', 'name': '测试股票'}}
    facts = {'snapshot': {'scope': 'test-only'}}
    seen: dict[str, Any] = {}

    async def snapshot_facts(self) -> dict[str, Any]:
        return {'key': 'snapshot', 'status': 'available', 'data': self.facts['snapshot']}

    async def financial_facts(self) -> dict[str, Any]:
        return {'status': 'missing', 'missing': '本地测试不提供财报。'}

    async def search_public(self, query: str) -> dict[str, Any]:
        return {'status': 'missing', 'missing': '本地测试不联网。'}

    async def read_public(self, url: str) -> dict[str, Any]:
        return {'status': 'missing', 'missing': '本地测试不联网。'}

    async def review_reductions(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        return {'status': 'missing', 'missing': '没有原文证据。'}


@pytest.mark.parametrize('protocol', ['chat_completions', 'responses'])
@pytest.mark.parametrize('tool_support', [True, False])
def test_installed_sdk_graph_and_protocol_settings(tmp_path, monkeypatch, protocol, tool_support):
    import langchain_openai
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', LocalChat)
    LocalChat.received_messages = []
    LocalChat.protocol_flags = []
    settings = Settings(database_url='postgresql+psycopg://unused/unused', state_dir=tmp_path,
                        ai_model='local-test', ai_api_key='not-used-for-network', ai_base_url='https://model.invalid/v1',
                        ai_protocol=protocol, ai_tools_supported=tool_support)
    runtime = DeepAgentsRuntime(settings)
    assert runtime.identity()['protocol'] == protocol
    tools = Tools()
    tools.tasks = SimpleNamespace(settings=settings)
    report, usage = asyncio.run(runtime.execute(tools))
    assert report == PAYLOAD
    assert isinstance(usage, dict)
    assert LocalChat.protocol_flags == [protocol == 'responses']
    assert any('计划上限不等于实际减持' in str(message.content) for message in LocalChat.received_messages)
    if tool_support:
        pinned = [message for message in LocalChat.received_messages
                  if message.additional_kwargs.get('lc_source') == 'pinned_skill']
        assert len(pinned) == 1
        assert pinned[0].additional_kwargs['skill']['path'] == '/skills/stock-analysis/SKILL.md'
