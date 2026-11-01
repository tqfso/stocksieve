"""本文件通过真实HTTP执行初始化、分类和收盘同步验收，凭据与令牌不写终端。"""

import asyncio
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp


class PipelineFailure(RuntimeError):
    """仅保留公开错误码与业务信息，不保留认证响应或请求口令。"""

    def __init__(self, message: str, code: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.code, self.details = code, details


async def bootstrap(base: str, credentials_file: Path, state_file: Path, source: str, target: date | None = None, retry_failed: bool = False, skip_initialize: bool = False, only_kind: str | None = None) -> None:
    credentials = json.loads(credentials_file.read_text())
    state = json.loads(state_file.read_text()) if state_file.exists() else {'source': source, 'jobs': {}, 'keys': {}}
    if state.get('source') != source:
        raise RuntimeError('现有验收状态属于其他渠道，请使用独立状态文件。')
    state_file.parent.mkdir(parents=True, exist_ok=True)
    def save() -> None:
        temporary = state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(state_file)
    token: str | None = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=False) as http:
        async def call(method: str, path: str, body: Any = None, key: str | None = None) -> Any:
            headers = {'X-Request-ID': 'accept_' + uuid4().hex}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            if key:
                headers['Idempotency-Key'] = key
            async with http.request(method, base.rstrip('/') + '/api/v1' + path, json=body, headers=headers, allow_redirects=False) as response:
                if response.status == 204:
                    return None
                value = await response.json()
                if response.status >= 400:
                    error = value.get('error', {})
                    raise PipelineFailure(f"HTTP验收{path}未成功：{error.get('message', response.status)}", error.get('code', ''), error.get('details', {}))
                return value['data']
        try:
            login = await call('POST', '/auth/login', {'username': credentials['username'], 'password': credentials['password']})
            token = login['accessToken']
            kinds = (only_kind,) if only_kind else ('catalog', 'daily') if skip_initialize else ('initialize', 'catalog', 'daily')
            for kind in kinds:
                if kind == 'daily':
                    status = await call('GET', '/market/status')
                    day = str(target) if target else state.get('date') or status['latestTradingDate']
                    if not day:
                        raise RuntimeError('无法确认最近已收盘交易日。')
                    state['date'] = day
                job = await call('GET', '/admin/sync-jobs/' + state['jobs'][kind]) if kind in state['jobs'] else None
                if job and job['status'] == 'failed':
                    if not retry_failed:
                        raise RuntimeError('既有任务失败，请核对后显式重试：' + job['error']['message'])
                    job = None
                    state['keys'].pop(kind, None)
                if job is None:
                    key = state['keys'].setdefault(kind, 'bootstrap_' + uuid4().hex)
                    save()
                    payload = {'kind': kind, 'source': source, **({'dataDate': state['date']} if kind == 'daily' else {})}
                    deadline = time.monotonic() + 6 * 3600
                    while True:
                        try:
                            job = await call('POST', '/admin/sync-jobs', payload, key)
                            break
                        except PipelineFailure as busy:
                            if busy.code != 'SYNC_IN_PROGRESS' or time.monotonic() > deadline:
                                raise
                            print(json.dumps({'kind': kind, 'waitingForExistingJob': busy.details.get('jobId')}, ensure_ascii=False), flush=True)
                            await asyncio.sleep(5)
                    state['jobs'][kind] = job['id']
                    save()
                previous = None
                failures = 0
                deadline = time.monotonic() + 6 * 3600
                while job['status'] in {'queued', 'running'}:
                    marker = (job['status'], job['progress'], job.get('nextRetryAt'))
                    if marker != previous:
                        print(json.dumps({'kind': kind, 'id': job['id'], 'status': job['status'], 'progress': job['progress'], 'nextRetryAt': job.get('nextRetryAt')}, ensure_ascii=False), flush=True)
                        previous = marker
                    if time.monotonic() > deadline:
                        raise RuntimeError('本次等待超过6小时，服务器任务保留，可稍后继续验收。')
                    delay = 5.0
                    if job.get('nextRetryAt'):
                        delay = min(30.0, max(5.0, datetime.fromisoformat(job['nextRetryAt']).timestamp() - time.time()))
                    await asyncio.sleep(delay)
                    try:
                        job = await call('GET', '/admin/sync-jobs/' + job['id'])
                        failures = 0
                    except (aiohttp.ClientError, TimeoutError):
                        failures += 1
                        if failures >= 3:
                            raise RuntimeError('HTTP查询暂时中断；服务器任务保留，重跑会恢复原任务。') from None
                print(json.dumps({'kind': kind, 'id': job['id'], 'status': job['status'], 'result': job.get('result'), 'error': job.get('error')}, ensure_ascii=False), flush=True)
                if job['status'] != 'succeeded':
                    raise RuntimeError('真实数据任务未完成：' + job['error']['message'])
            status = await call('GET', '/market/status')
            snapshot = status['publishedSnapshot']
            if not snapshot:
                raise RuntimeError('尚无已发布真实快照，验收不能通过。')
            result = await call('GET', '/opportunities?strategy=short&snapshotId=' + snapshot['id'])
            print(json.dumps({'realPipelinePassed': True, 'snapshot': snapshot, 'coverage': status['coverage'], 'strategyCounts': result['summary']['strategyCounts']}, ensure_ascii=False), flush=True)
        finally:
            if token:
                try:
                    await call('POST', '/auth/logout')
                except (aiohttp.ClientError, TimeoutError, RuntimeError):
                    print('验收会话退出暂未确认，请按会话有效期处理。', flush=True)
