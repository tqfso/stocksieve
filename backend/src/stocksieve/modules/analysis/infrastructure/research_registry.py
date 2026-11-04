"""本文件按市场和优先级注册研究资料来源，有足够命中即停止补源。"""

from stocksieve.modules.analysis.application.ports import SearchHit, SearchProvider
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.shared.errors import DomainError


class ResearchRegistry:
    """交易所优先；来源失败或未命中才补充，避免重复检索和下载同份公告。"""

    def __init__(self) -> None:
        self.sources: dict[str, tuple[SearchProvider, frozenset[str]]] = {}

    def register(self, source_id: str, source: SearchProvider, exchanges: frozenset[str]) -> None:
        if source_id in self.sources or not exchanges:
            raise ValueError('研究来源重复或覆盖范围为空')
        self.sources[source_id] = source, exchanges

    async def search(self, query: str, exchange: str | None = None) -> list[SearchHit]:
        cached: list[SearchHit] = []
        waits: list[SourceDeferred] = []
        for source, exchanges in self.sources.values():
            if exchange and exchange not in exchanges:
                continue
            try:
                hits = await source.search(query, exchange=exchange)
            except SourceDeferred as wait:
                waits.append(wait)
                continue
            except DomainError:
                continue
            if hits and not any(hit.cache_warning for hit in hits):
                return hits
            if hits and (not cached or max(hit.index_fetched_at or '' for hit in hits) > max(hit.index_fetched_at or '' for hit in cached)):
                cached = hits
        if cached:
            return cached
        if waits:
            raise min(waits, key=lambda wait: wait.until)
        return []
