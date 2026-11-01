"""本文件验证格式误判纠正必须绑定原文、原价样本及该次冷却，绝不清空其他限额。"""

import hashlib
import json
import time
import zlib
import random
from os import utime
from pathlib import Path

import pytest

from stocksieve.source_policy import SourcePolicy
from stocksieve.source_repair import reconcile_short_receive_deadline, reconcile_tencent


def evidence(tmp_path: Path, extra_cooldown: float = 0) -> SourcePolicy:
    body = json.dumps({'code': 0, 'data': {'sh688981': {'day': [['2026-10-30', '10', '10', '10', '10', '100']]}}})
    raw, rejected = tmp_path / 'raw.txt', tmp_path / 'bad.rejected'
    raw.write_text(body); rejected.write_text(body)
    instant = time.time() - 100
    utime(rejected, (instant, instant))
    digest = hashlib.sha256(body.encode()).hexdigest()
    records = [{'id': 'sample', 'source': 'tencent', 'status': 'sample_received', 'evidence': raw.name, 'sha256': digest},
               {'id': 'sample_qfq', 'source': 'tencent', 'status': 'unverified_response', 'rejectedEvidence': rejected.name, 'rejectedSha256': digest}]
    (tmp_path / 'report-test.json').write_text(json.dumps({'results': records}))
    (tmp_path / 'state.json').write_text(json.dumps({'daily': {'2026-10-31': {'tencent': 9}}, 'next_at': instant + 10,
        'cooldown': {'tencent': instant + 86400 + extra_cooldown, 'sina': instant + 86400}}))
    return SourcePolicy(tmp_path)


def test_verified_local_format_error_can_be_corrected_without_resetting_counts(tmp_path: Path) -> None:
    policy = evidence(tmp_path)
    before = json.loads((tmp_path / 'state.json').read_text())
    assert reconcile_tencent(policy)['changed'] is True
    after = json.loads((tmp_path / 'state.json').read_text())
    assert after['daily'] == before['daily'] and after['next_at'] == before['next_at']
    assert after['cooldown']['sina'] == before['cooldown']['sina']
    assert after['cooldown']['tencent'] == 0 and len(after['corrections']) == 1


def test_later_or_unrelated_cooldown_cannot_be_cleared(tmp_path: Path) -> None:
    policy = evidence(tmp_path, 1000)
    before = (tmp_path / 'state.json').read_bytes()
    with pytest.raises(RuntimeError):
        reconcile_tencent(policy)
    assert (tmp_path / 'state.json').read_bytes() == before


def test_only_old_short_deadline_on_verified_success_stream_can_be_corrected(tmp_path: Path) -> None:
    stream = b'0\1success\1query_daily_history_k_AStock\1anonymous\1' + random.Random(42).randbytes(20000)
    compressed = zlib.compress(stream)
    partial = ('00.9.10\1' + '99\1' + f'{len(compressed):010d}').encode() + compressed[:int(len(compressed) * .9)]
    cache = tmp_path / 'cache'; cache.mkdir()
    (cache / 'last-failure-body.bin').write_bytes(partial)
    failed = time.time() - 100
    report = {'at': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(failed)) + '+00:00',
              'method': 'query_daily_history_k_AStock', 'type': 'TimeoutError'}
    (cache / 'last-failure.json').write_text(json.dumps(report))
    state = {'daily': {'2026-11-01': {'baostock': 8}}, 'next_at': failed + 10, 'cooldown': {'baostock': failed + 3600}}
    (tmp_path / 'state.json').write_text(json.dumps(state))
    policy = SourcePolicy(tmp_path)
    assert reconcile_short_receive_deadline(policy, cache)['changed'] is True
    result = json.loads((tmp_path / 'state.json').read_text())
    assert result['daily'] == state['daily'] and result['next_at'] == state['next_at']
    assert result['cooldown']['baostock'] == 0
    report['receiveDeadlineSeconds'] = 120
    (cache / 'last-failure.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError):
        reconcile_short_receive_deadline(policy, cache)
