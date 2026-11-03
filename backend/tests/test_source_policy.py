"""本文件验证正式限频继承探测次数与冷却、跨进程锁和损坏状态保护。"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.application.progress import progress_sink
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.shared.errors import DomainError


def test_formal_budget_preserves_used_counts_and_existing_cooldowns(tmp_path: Path) -> None:
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
    state = {'daily': {'2026-10-31': {'sina': 8, 'baidu': 9}}, 'next_at': now - 1, 'cooldown': {'baidu': now + 86400}}
    (tmp_path / 'state.json').write_text(json.dumps(state))
    policy = SourcePolicy(tmp_path, 10, 400)
    policy.reserve('sina', now)
    with pytest.raises(SourceDeferred):
        SourcePolicy(tmp_path, 10, 400).reserve('sina', now + 1)
    with pytest.raises(SourceDeferred):
        policy.reserve('baidu', now + 20)
    saved = json.loads((tmp_path / 'state.json').read_text())
    assert saved['daily']['2026-10-31'] == {'sina': 9, 'baidu': 9}
    assert saved['cooldown'] == state['cooldown']


def test_global_budget_and_corruption_never_silently_reset(tmp_path: Path) -> None:
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
    policy = SourcePolicy(tmp_path, 10, 2)
    policy.reserve('a', now); policy.reserve('b', now + 10)
    with pytest.raises(SourceDeferred):
        policy.reserve('c', now + 20)
    (tmp_path / 'state.json').write_text('invalid')
    with pytest.raises(DomainError):
        policy.reserve('a', now + 86400)


def test_one_second_shared_interval_and_daily_budget_reserved_for_regular_updates(tmp_path: Path) -> None:
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
    policy = SourcePolicy(tmp_path, 1, 8)
    with progress_sink(lambda *args: None, background=True):
        for offset in range(2):
            policy.reserve('sina', now + offset)
        with pytest.raises(SourceDeferred):
            policy.reserve('sina', now + 2)
    for offset in range(2, 7):
        policy.reserve('sina', now + offset)
    with pytest.raises(SourceDeferred):
        policy.reserve('baidu', now + 6.5)
    policy.reserve('baidu', now + 7)
    with pytest.raises(SourceDeferred):
        policy.reserve('sina', now + 8)
    saved = json.loads((tmp_path / 'state.json').read_text())
    assert sum(saved['daily']['2026-10-31'].values()) == 8
