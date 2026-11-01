"""本文件仅纠正有完整原始证据的本地格式误判，不解除真实HTTP限流或网络失败冷却。"""

import hashlib
import json
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy


def reconcile_tencent(policy: SourcePolicy) -> dict[str, Any]:
    """仅当成功原价与被误判的原价回退逐条相同，且冷却时间绑定该响应时纠正。"""
    candidates = []
    for file in policy.directory.glob('report-*.json'):
        report = json.loads(file.read_text())
        if report.get('mode') == 'offline':
            continue
        records = report.get('results', [])
        for row in records:
            if row.get('source') != 'tencent' or row.get('status') != 'unverified_response' or not row.get('id', '').endswith('_qfq'):
                continue
            original = next((item for item in records if item.get('id') == row['id'][:-4] and item.get('status') == 'sample_received'), None)
            if original and row.get('rejectedEvidence'):
                candidates.append((file, row, original))
    if not candidates:
        raise RuntimeError('没有符合条件的格式误判记录，不修改冷却。')
    file, rejected, original = candidates[-1]
    paths = [policy.directory / rejected['rejectedEvidence'], policy.directory / original['evidence']]
    for path in paths:
        if path.parent != policy.directory or path.is_symlink():
            raise RuntimeError('证据路径不合法。')
    contents = [path.read_bytes() for path in paths]
    if hashlib.sha256(contents[0]).hexdigest() != rejected['rejectedSha256'] or hashlib.sha256(contents[1]).hexdigest() != original['sha256']:
        raise RuntimeError('原始证据校验未通过，不修改冷却。')
    bad, good = (json.loads(value) for value in contents)
    if bad.get('code') != 0 or good.get('code') != 0 or set(bad.get('data', {})) != set(good.get('data', {})):
        raise RuntimeError('响应不是正常成功数据，不修改冷却。')
    for key, entry in bad['data'].items():
        if entry.get('qfqday') or not entry.get('day') or entry['day'] != good['data'][key].get('day'):
            raise RuntimeError('不满足已确认的原价回退格式，不修改冷却。')
    with policy.transaction(blocking=True) as state:
        current = state.get('cooldown', {}).get('tencent', 0)
        if current <= time.time():
            return {'changed': False, 'reason': '冷却已经结束'}
        if abs(current - (paths[0].stat().st_mtime + 86400)) > 2:
            raise RuntimeError('现有冷却与该格式误判不匹配，可能有后续限制，不修改。')
        state['cooldown']['tencent'] = 0
        state.setdefault('corrections', []).append({'source': 'tencent', 'reason': 'verified_raw_fallback_parser_error',
            'at': datetime.now(timezone.utc).isoformat(), 'report': file.name,
            'evidenceSha256': rejected['rejectedSha256'], 'previousCooldown': current})
    return {'changed': True, 'source': 'tencent', 'reason': '已保存原文证明是正常原价回退；仅纠正本地误判，访问次数及间隔不变。'}


def reconcile_short_receive_deadline(policy: SourcePolicy, cache: Path) -> dict[str, Any]:
    """仅纠正旧版20秒上限中断正常成功大包的暂停；新版本真实超时及其他限制不可解除。"""
    diagnostic = json.loads((cache / 'last-failure.json').read_text())
    content = (cache / 'last-failure-body.bin').read_bytes()
    if diagnostic.get('type') != 'TimeoutError' or diagnostic.get('receiveDeadlineSeconds', 20) > 20 or diagnostic.get('method') != 'query_daily_history_k_AStock':
        raise RuntimeError('不符合已确认的旧接收期限问题，不修改冷却。')
    header = content[:21].decode().split('\1')
    length = int(header[2])
    if length <= 0 or not .75 <= (len(content) - 21) / length < 1:
        raise RuntimeError('未取得足够的正常响应流证据，不修改冷却。')
    decoder = zlib.decompressobj()
    fragment = decoder.decompress(content[21:], 8 * 1024 * 1024)
    if decoder.eof or not fragment.startswith(b'0\1success\1query_daily_history_k_AStock\1anonymous\1'):
        raise RuntimeError('不是待接收完成的正常匿名日线响应，不修改冷却。')
    failure_time = datetime.fromisoformat(diagnostic['at']).timestamp()
    with policy.transaction(blocking=True) as state:
        current = state.get('cooldown', {}).get('baostock', 0)
        if current <= time.time():
            return {'changed': False, 'reason': '暂停已结束'}
        if abs(current - (failure_time + 3600)) > 2:
            raise RuntimeError('现有暂停与这份旧超时证据不匹配，不修改。')
        state['cooldown']['baostock'] = 0
        state.setdefault('corrections', []).append({'source': 'baostock', 'reason': 'old_20_second_deadline_during_successful_stream',
            'at': datetime.now(timezone.utc).isoformat(), 'evidenceSha256': hashlib.sha256(content).hexdigest(),
            'previousCooldown': current, 'receivedBytes': len(content), 'declaredBodyBytes': length})
    return {'changed': True, 'source': 'baostock', 'reason': '旧20秒接收期限过短，成功数据流被本地中断；仅纠正该次暂停，全部次数和间隔保留。'}


def reconcile_partial_stream_backoff(policy: SourcePolicy, cache: Path) -> dict[str, Any]:
    """将仅由本地普通流中断产生的一小时退避改为新有界策略，不解除拒绝访问或无响应。"""
    diagnostic = json.loads((cache / 'last-failure.json').read_text())
    content = (cache / 'last-failure-body.bin').read_bytes()
    if diagnostic.get('type') != 'TimeoutError' or diagnostic.get('method') != 'query_daily_history_k_AStock' or not diagnostic.get('requestedDates'):
        raise RuntimeError('不是待恢复的日线成功流，不修改。')
    header = content[:21].decode().split('\1')
    size = int(header[2])
    decoder = zlib.decompressobj()
    prefix = decoder.decompress(content[21:], 4096)
    if size <= len(content) - 21 or not prefix.startswith(b'0\1success\1query_daily_history_k_AStock\1anonymous\1'):
        raise RuntimeError('没有正常成功流中断的证据，不修改。')
    instant = datetime.fromisoformat(diagnostic['at']).timestamp()
    key = diagnostic['method'] + ':' + diagnostic['requestedDates'][-1]
    with policy.transaction(blocking=True) as state:
        current = state.get('cooldown', {}).get('baostock', 0)
        if current <= time.time():
            return {'changed': False, 'reason': '本地等待已结束'}
        if abs(current - (instant + 3600)) > 2 or state.get('partialStreamRetries', {}).get(key, 0) >= 3:
            raise RuntimeError('暂停记录不对应本地流中断，或短重试已用完，不修改。')
        state['cooldown']['baostock'] = 0
        state.setdefault('partialStreamRetries', {})[key] = 1
        state.setdefault('corrections', []).append({'source': 'baostock', 'reason': 'bounded_partial_stream_retry_policy',
            'at': datetime.now(timezone.utc).isoformat(), 'evidenceSha256': hashlib.sha256(content).hexdigest(),
            'previousCooldown': current, 'requestKey': key})
    return {'changed': True, 'source': 'baostock', 'reason': '正常只读成功流中断采用有界短退避；次数、最小间隔和真实访问限制保留。'}
