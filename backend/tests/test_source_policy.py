"""本文件验证正式限频继承探测次数与冷却、跨进程锁和损坏状态保护。"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.shared.errors import DomainError


def test_formal_budget_preserves_used_counts_and_existing_cooldowns(tmp_path: Path) -> None:
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
    state = {'daily': {'2026-10-31': {'baostock': 8, 'sina': 9}}, 'next_at': now - 1, 'cooldown': {'sina': now + 86400}}
    (tmp_path / 'state.json').write_text(json.dumps(state))
    policy = SourcePolicy(tmp_path, 10, 400)
    policy.reserve('baostock', now)
    with pytest.raises(SourceDeferred):
        SourcePolicy(tmp_path, 10, 400).reserve('baostock', now + 1)
    with pytest.raises(SourceDeferred):
        policy.reserve('sina', now + 20)
    saved = json.loads((tmp_path / 'state.json').read_text())
    assert saved['daily']['2026-10-31'] == {'baostock': 9, 'sina': 9}
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


def test_partial_stream_retries_are_persistent_bounded_and_keep_peer_wait(tmp_path: Path) -> None:
    policy = SourcePolicy(tmp_path)
    policy.block('baostock', 10000)
    before = json.loads((tmp_path / 'state.json').read_text())['cooldown']['baostock']
    for _ in range(3):
        until = SourcePolicy(tmp_path).retry_partial_stream('baostock', 'daily:2026-10-30')
        # datetime精度为微秒；持久账本的原始秒数仍在下方单独校验，不能因转换舍入误报。
        assert until is not None and until >= datetime.fromtimestamp(before, timezone.utc)
    assert SourcePolicy(tmp_path).retry_partial_stream('baostock', 'daily:2026-10-30') is None
    policy.complete_stream('daily:2026-10-30')
    assert SourcePolicy(tmp_path).retry_partial_stream('baostock', 'daily:2026-10-30') is not None
    assert json.loads((tmp_path / 'state.json').read_text())['cooldown']['baostock'] >= before
