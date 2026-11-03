"""本文件定义检索结果、检索服务和智能体运行端口，不绑定模型服务商或框架。"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from stocksieve.modules.analysis.application.service import AnalysisTools


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    published_at: str | None = None


class SearchProvider(Protocol):
    async def search(self, query: str) -> list[SearchHit]: ...


class DocumentParser(Protocol):
    """把原文转换为带页码的文本，应用层不依赖具体PDF库。"""

    def parse(self, raw: bytes) -> dict[str, Any]: ...


class AgentRuntime(Protocol):
    def identity(self) -> dict[str, Any] | None: ...
    async def execute(self, tools: 'AnalysisTools') -> tuple[dict[str, Any], dict[str, Any]]: ...
