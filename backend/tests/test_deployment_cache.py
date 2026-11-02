"""本文件验证仅复用项目备份中的成功公开行情缓存，保留当前响应和频控账本。"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

FOLDER = Path(__file__).resolve().parents[1] / 'deploy/docker'
sys.path.insert(0, str(FOLDER))
SPEC = importlib.util.spec_from_file_location('stocksieve_deployment_cache_test', FOLDER / 'deploy.py')
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


def test_only_successful_public_prices_are_reused_without_overwriting_current_cache(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(deployment, 'ROOT', tmp_path)
    monkeypatch.setattr(deployment.os, 'chown', lambda *args: None)
    archive = tmp_path / '.state/backups/reset-test'
    origin = archive / 'baostock-wire'
    origin.mkdir(parents=True)
    current = tmp_path / '.state/baostock-wire'
    current.mkdir()
    ledger = tmp_path / '.source-probe'
    ledger.mkdir()
    (ledger / 'state.json').write_text('keep-current-budget')
    reply = ' ' * 21 + '0\x01success\x01query_daily_history_k_AStock\x01public'
    for letter in ('a', 'b'):
        (origin / (letter * 64 + '.json')).write_text(json.dumps({'at': 123, 'reply': reply}))
    (origin / ('c' * 64 + '.json')).write_text(json.dumps({'reply': ' ' * 21 + '0\x01success\x01login\x01'}))
    (current / ('a' * 64 + '.json')).write_text('keep-current-response')
    deployment.reuse_price_cache(archive)
    assert (current / ('a' * 64 + '.json')).read_text() == 'keep-current-response'
    assert json.loads((current / ('b' * 64 + '.json')).read_text())['at'] == 123
    assert not (current / ('c' * 64 + '.json')).exists()
    assert (ledger / 'state.json').read_text() == 'keep-current-budget'
    with pytest.raises(RuntimeError):
        deployment.reuse_price_cache(tmp_path)
