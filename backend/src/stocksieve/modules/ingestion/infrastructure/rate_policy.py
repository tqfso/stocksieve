"""本文件让正式同步与早期探测共用请求账本、间隔和冷却，不清空已有访问记录。"""

import fcntl
import json
import math
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.shared.errors import DomainError
from stocksieve.shared.time import SHANGHAI


class SourcePolicy:
    """正式阶段使用配置的每日预算；探测工具仍使用更小预算，共用同一账本与锁。"""

    def __init__(self, directory: Path, interval: float = 10, daily_limit: int = 400) -> None:
        if not math.isfinite(interval) or interval < 10 or daily_limit < 1:
            raise ValueError('请求间隔和预算无效。')
        directory.mkdir(parents=True, exist_ok=True)
        self.directory, self.interval, self.daily_limit = directory, interval, daily_limit

    @contextmanager
    def transaction(self, *, blocking: bool = False) -> Iterator[dict[str, Any]]:
        with (self.directory / 'run.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise SourceDeferred(datetime.now(timezone.utc) + timedelta(seconds=2), '数据渠道账本正在使用。') from None
            path = self.directory / 'state.json'
            try:
                state = json.loads(path.read_text()) if path.exists() else {}
                if not isinstance(state, dict):
                    raise ValueError('账本格式错误')
            except (ValueError, OSError) as error:
                raise DomainError('SOURCE_STATE_INVALID', '渠道访问账本损坏，已停止请求。', 503) from error
            yield state
            temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(path)

    def reserve(self, vendor: str, now: float | None = None) -> None:
        instant = time.time() if now is None else now
        day = datetime.fromtimestamp(instant, SHANGHAI).date()
        with self.transaction() as state:
            counts = state.setdefault('daily', {}).setdefault(day.isoformat(), {})
            until = max(state.get('next_at', 0), state.get('cooldown', {}).get(vendor, 0))
            if counts.get(vendor, 0) >= self.daily_limit or sum(counts.values()) >= self.daily_limit:
                until = max(until, datetime.combine(day + timedelta(days=1), datetime.min.time(), SHANGHAI).timestamp())
            if until > instant:
                raise SourceDeferred(datetime.fromtimestamp(until, timezone.utc), '遵守渠道间隔、预算或冷却，等待下次执行。')
            counts[vendor] = counts.get(vendor, 0) + 1
            state['next_at'] = instant + self.interval

    def block(self, vendor: str, seconds: float, now: float | None = None) -> None:
        instant = time.time() if now is None else now
        with self.transaction(blocking=True) as state:
            values = state.setdefault('cooldown', {})
            values[vendor] = max(values.get(vendor, 0), instant + seconds)

    def retry_partial_stream(self, vendor: str, key: str) -> datetime | None:
        """正常成功流中断最多短重试3次；计数持久化，不能通过重启或新建任务清零。"""
        instant = time.time()
        with self.transaction(blocking=True) as state:
            retries = state.setdefault('partialStreamRetries', {})
            count = retries.get(key, 0)
            if count >= 3:
                return None
            retries[key] = count + 1
            until = max(state.setdefault('cooldown', {}).get(vendor, 0), instant + min(120, 30 * 2 ** count))
            state['cooldown'][vendor] = until
        return datetime.fromtimestamp(until, timezone.utc)

    def complete_stream(self, key: str) -> None:
        with self.transaction(blocking=True) as state:
            state.setdefault('partialStreamRetries', {}).pop(key, None)
