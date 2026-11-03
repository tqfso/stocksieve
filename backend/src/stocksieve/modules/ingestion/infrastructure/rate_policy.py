"""本文件通过共享账本协调所有进程的请求预算、间隔和冷却。"""

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
from stocksieve.modules.ingestion.application.progress import is_background_sync
from stocksieve.shared.errors import DomainError
from stocksieve.shared.time import SHANGHAI


class SourcePolicy:
    """日常更新和历史回补共用预算、锁和真实冷却。"""

    def __init__(self, directory: Path, interval: float = 0.2, daily_limit: int = 20000) -> None:
        if not math.isfinite(interval) or interval < 0.2 or daily_limit < 1:
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

    @property
    def foreground_reserve(self) -> int:
        """为逐股日常更新预留80%预算；回补使用其余部分，全部实际请求仍计数。"""
        return max(1, self.daily_limit * 4 // 5)

    def reserve(self, vendor: str, now: float | None = None) -> None:
        instant = time.time() if now is None else now
        day = datetime.fromtimestamp(instant, SHANGHAI).date()
        with self.transaction() as state:
            counts = state.setdefault('daily', {}).setdefault(day.isoformat(), {})
            until = max(state.get('next_at', 0), state.get('vendor_next_at', {}).get(vendor, 0), state.get('cooldown', {}).get(vendor, 0))
            # 回补最多使用扣除预留后的额度，日常更新仍可使用完整预算；已有次数始终计入。
            reserved = self.foreground_reserve if is_background_sync() else 0
            budget = self.daily_limit - reserved
            if counts.get(vendor, 0) >= self.daily_limit or sum(counts.values()) >= budget:
                until = max(until, datetime.combine(day + timedelta(days=1), datetime.min.time(), SHANGHAI).timestamp())
            if until > instant:
                reason = 'SOURCE_ACCESS_WAIT'
                if state.get('cooldown', {}).get(vendor, 0) > instant:
                    message = '数据源暂处访问冷却，尚不能完成行情检查。'
                elif sum(counts.values()) >= budget:
                    message = '当日取数额度已用尽或已为日常更新预留，等待次日额度。'
                else:
                    message = '等待数据源请求间隔后继续。'
                    reason = 'SOURCE_REQUEST_INTERVAL'
                raise SourceDeferred(datetime.fromtimestamp(until, timezone.utc), message, reason=reason)
            counts[vendor] = counts.get(vendor, 0) + 1
            state['next_at'] = instant + self.interval
            # 百度分类沿用已验证的每秒一批，不随新浪行情提速而提高其请求频率。
            state.setdefault('vendor_next_at', {})[vendor] = instant + max(self.interval, 1 if vendor == 'baidu' else self.interval)

    def block(self, vendor: str, seconds: float, now: float | None = None) -> None:
        instant = time.time() if now is None else now
        with self.transaction(blocking=True) as state:
            values = state.setdefault('cooldown', {})
            values[vendor] = max(values.get(vendor, 0), instant + seconds)
