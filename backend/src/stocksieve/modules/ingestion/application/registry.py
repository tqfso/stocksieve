"""本文件按任务能力注册渠道并验证覆盖范围，不创建任何外部客户端。"""

from typing import Any

from stocksieve.modules.ingestion.application.ports import Source
from stocksieve.modules.market.domain.models import BOARDS
from stocksieve.shared.errors import DomainError


class Registry:
    """注册只建立能力映射，启用渠道须以实测覆盖为依据，不能静默替换渠道。"""

    def __init__(self, boards: tuple[str, ...] = tuple(BOARDS)) -> None:
        self.boards = frozenset(boards)
        self.sources: dict[str, Source] = {}
        self.pending: dict[str, str] = {'sina': 'Sina', 'eastmoney': '东方财富'}

    def register(self, source: Source) -> None:
        import re
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', source.id) or not source.supported_kinds <= {'initialize', 'catalog', 'daily'}:
            raise ValueError('数据源标识或能力声明无效')
        if source.id in self.sources:
            raise ValueError('重复注册数据源')
        self.sources[source.id] = source
        self.pending.pop(source.id, None)

    def get(self, source_id: str, kind: str, boards: tuple[str, ...] | None = None) -> Source:
        source = self.sources.get(source_id)
        required = frozenset(boards) if boards is not None else self.boards
        if not source or kind not in source.supported_kinds or kind in {'initialize', 'daily'} and not required <= source.boards:
            raise DomainError('SOURCE_UNAVAILABLE', '该渠道未启用、未通过覆盖验证或不支持此任务。')
        return source

    def public(self) -> list[dict[str, Any]]:
        return [{'id': key, 'name': source.name, 'available': bool(kinds := sorted(kind for kind in source.supported_kinds if kind == 'catalog' or self.boards <= source.boards)), 'supportedSyncKinds': kinds, 'supportedBoards': sorted(source.boards)} for key, source in self.sources.items()] + [
            {'id': key, 'name': name, 'available': False, 'supportedSyncKinds': []} for key, name in self.pending.items()]
