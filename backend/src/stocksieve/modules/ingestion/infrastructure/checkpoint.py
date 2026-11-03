"""本文件按任务保存渠道准备阶段的断点，避免频控等待或进程重启后重复完成的批次。"""

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from stocksieve.modules.ingestion.application.progress import current_operation_id
from stocksieve.shared.errors import DomainError


class SourceCheckpoint:
    """仅固定本任务已成功的准备资料；不读取或修改访问次数、间隔与冷却。"""

    def __init__(self, directory: Path) -> None:
        operation = current_operation_id()
        self.path = directory / (hashlib.sha256(operation.encode()).hexdigest() + '.json') if operation else None
        try:
            self.data: dict[str, Any] = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}
            if not isinstance(self.data, dict):
                raise TypeError('断点应为对象')
        except (OSError, ValueError, TypeError) as error:
            raise DomainError('SOURCE_CACHE_INVALID', '渠道准备断点无法读取，已停止更新。', 503) from error

    def save(self) -> None:
        """在上报进度及主动让位之前原子保存，续跑只处理尚未完成的批次。"""
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.' + uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(self.data, ensure_ascii=False))
            temporary.replace(self.path)
