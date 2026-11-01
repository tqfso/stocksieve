"""本文件定义检索结果、检索服务和智能体运行端口，不绑定模型服务商或框架。"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from stocksieve.modules.analysis.application.service import AnalysisTools


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str


class SearchProvider(Protocol):
    async def search(self, query: str) -> list[SearchHit]: ...


class AgentRuntime(Protocol):
    def identity(self) -> dict[str, Any] | None: ...
    async def execute(self, tools: 'AnalysisTools') -> tuple[dict[str, Any], dict[str, Any]]: ...
