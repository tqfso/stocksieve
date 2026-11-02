"""本文件验证统一Compose迁移的身份检查、私有配置与同卷数据库恢复，单元测试不操作Docker。"""

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

FILE = Path(__file__).resolve().parents[1] / 'deploy/docker/stack.py'
SPEC = importlib.util.spec_from_file_location('stocksieve_stack_deployment_test', FILE)
stack = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stack
SPEC.loader.exec_module(stack)


def postgres_info(project: str = 'stocksieve-postgres') -> dict[str, Any]:
    return {'Config': {'Image': 'postgres:15-alpine', 'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': 'postgres'},
                       'Env': ['POSTGRES_USER=user', 'POSTGRES_DB=db', 'POSTGRES_PASSWORD=test-only']},
            'Mounts': [{'Destination': '/var/lib/postgresql/data', 'Type': 'volume', 'Name': stack.PG_VOLUME}],
            'HostConfig': {'PortBindings': {'5432/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '5432'}]}}}


VALUES = {'STOCKSIEVE_POSTGRES_USER': 'user', 'STOCKSIEVE_POSTGRES_DB': 'db', 'STOCKSIEVE_POSTGRES_PASSWORD': 'test-only', 'STOCKSIEVE_POSTGRES_PORT': '5432'}


def test_merge_env_preserves_private_credentials_and_other_settings(tmp_path: Path) -> None:
    legacy = tmp_path / 'deploy/postgres'
    legacy.mkdir(parents=True)
    (legacy / '.env').write_text(''.join(f'{key}={value}\n' for key, value in VALUES.items()))
    target = tmp_path / '.env'
    target.write_text('STOCKSIEVE_AI_MODE=mock\nSTOCKSIEVE_HISTORY_TRADING_DAYS=250\n')
    stack.prepare_env(tmp_path)
    values = stack.read_env(target)
    assert values['STOCKSIEVE_POSTGRES_PASSWORD'] == 'test-only'
    assert values['STOCKSIEVE_AI_MODE'] == 'mock' and values['STOCKSIEVE_HISTORY_TRADING_DAYS'] == '250'
    assert values['STOCKSIEVE_HISTORY_MAX_TRADING_DAYS'] == '0'
    assert values['STOCKSIEVE_API_PORT'] == '24048'
    assert target.stat().st_mode & 0o777 == 0o600
    original = target.read_text()
    (legacy / '.env').write_text('STOCKSIEVE_POSTGRES_PASSWORD=different\n')
    with pytest.raises(RuntimeError):
        stack.prepare_env(tmp_path)
    assert target.read_text() == original


def test_only_known_container_image_volume_and_credentials_are_accepted() -> None:
    assert stack.validate_postgres(postgres_info(), VALUES) == 'stocksieve-postgres'
    for key in ('project', 'image', 'volume', 'password'):
        info = deepcopy(postgres_info())
        if key == 'project': info['Config']['Labels']['com.docker.compose.project'] = 'other'
        if key == 'image': info['Config']['Image'] = 'postgres:17'
        if key == 'volume': info['Mounts'][0]['Name'] = 'other-data'
        if key == 'password': info['Config']['Env'][-1] = 'POSTGRES_PASSWORD=other'
        with pytest.raises(RuntimeError):
            stack.validate_postgres(info, VALUES)


def test_rollback_stops_new_database_before_restoring_old_container(monkeypatch: Any) -> None:
    calls: list[list[str]] = []
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ['docker', 'container', 'inspect']:
            return subprocess.CompletedProcess(args, 0, json.dumps([postgres_info(stack.PROJECT)]), '')
        return subprocess.CompletedProcess(args, 0, '', '')
    service = stack.Stack(runner, VALUES)
    service.archive = 'stocksieve-postgres-pre-merge-test'
    service.apps_stopped = True
    service.rollback()
    stop = calls.index(['docker', 'stop', '--time', '60', stack.POSTGRES])
    rename = next(index for index, call in enumerate(calls) if call[:2] == ['docker', 'rename'])
    start = calls.index(['docker', 'start', stack.POSTGRES])
    assert stop < rename < start
    assert not any('--volumes' in call or '-v' in call or call[:3] == ['docker', 'volume', 'rm'] for call in calls)
    assert '--no-deps' in calls[-1]


def test_migration_backs_up_then_reuses_volume_and_never_reinitializes_existing_database(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(stack, 'ROOT', tmp_path)
    calls: list[list[str]] = []
    counts = {name: index + 1 for index, name in enumerate(stack.TABLES)}
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        value = ''
        if args[-1].startswith('SELECT json_build_object'):
            value = json.dumps(counts)
        elif args[-1].startswith('SELECT count(*) FROM public.tasks'):
            value = '0'
        elif args[-1].startswith('SELECT count(*) FROM pg_catalog'):
            value = '17'
        elif 'pg_dump' in args[-1]:
            kwargs['stdout'].write(b'backup-test-only' * 200)
        elif 'pg_restore' in args:
            value = 'TABLE public users\nTABLE public daily_bars\n'
        return subprocess.CompletedProcess(args, 0, value, '')
    service = stack.Stack(runner, VALUES)
    service.start_database(True)
    dump = next(index for index, call in enumerate(calls) if 'pg_dump' in call[-1])
    stop = calls.index(['docker', 'stop', '--time', '60', stack.POSTGRES])
    rename = next(index for index, call in enumerate(calls) if call[:2] == ['docker', 'rename'])
    create = next(index for index, call in enumerate(calls) if 'up' in call and call[-1] == 'postgres')
    assert dump < stop < rename < create and service.baseline == counts
    assert service.archive and not any(call[:3] == ['docker', 'container', 'rm'] for call in calls)
    assert not any('psql -X -q' in ' '.join(call) for call in calls)
    backup = next((tmp_path / '.state/backups').glob('*.dump'))
    assert backup.stat().st_mode & 0o777 == 0o600
    service.finish()
    assert calls[-1][:3] == ['docker', 'container', 'rm'] and '-v' not in calls[-1]


def test_explicit_reset_backs_up_before_removing_only_project_volumes(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(stack, 'ROOT', tmp_path)
    monkeypatch.setattr(stack.os, 'chown', lambda *args: None)
    state = tmp_path / '.state'
    state.mkdir()
    (state / 'source-cache').mkdir()
    (state / 'initial-admin.json').write_text('private-test-only')
    (state / 'old-pipeline.json').write_text('{}')
    (tmp_path / '.env').write_text('private-test-only')
    ledger = tmp_path / '.source-probe'
    ledger.mkdir()
    (ledger / 'state.json').write_text('{"daily":{"2026-11-01":{"baostock":99}}}')
    calls: list[list[str]] = []
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '0' if args[-1].startswith('SELECT count(*)') else '', '')
    service = stack.Stack(runner, VALUES)
    service.prepare = lambda: False
    service.inspect = lambda kind, name: postgres_info(stack.PROJECT)
    service.sql = lambda sql: 'postgres\ndb'
    service.counts = lambda: {'stocks': 5223}
    def backup() -> Path:
        calls.append(['verified-backup'])
        path = state / 'backups' / 'before-reset.dump'
        path.parent.mkdir()
        path.write_bytes(b'test-only')
        return path
    service.backup = backup
    service.reset()
    removals = [call for call in calls if call[:3] == ['docker', 'volume', 'rm']]
    assert removals == [['docker', 'volume', 'rm', stack.PG_VOLUME], ['docker', 'volume', 'rm', stack.STATE_VOLUME]]
    assert calls.index(['verified-backup']) < next(index for index, call in enumerate(calls) if 'down' in call) < calls.index(removals[0])
    assert (ledger / 'state.json').read_text().endswith('99}}}')
    assert (state / 'initial-admin.json').read_text() == 'private-test-only'
    assert not (state / 'old-pipeline.json').exists()
    archive = next((state / 'backups').glob('reset-*'))
    assert (archive / 'old-pipeline.json').exists() and (state / 'source-cache').is_dir()
    assert (archive / 'server.env').stat().st_mode & 0o777 == 0o600


def test_explicit_reset_refuses_database_volume_shared_with_another_database(monkeypatch: Any) -> None:
    calls: list[list[str]] = []
    service = stack.Stack(lambda args, **kwargs: calls.append(args), VALUES)
    service.prepare = lambda: False
    service.inspect = lambda kind, name: postgres_info(stack.PROJECT)
    service.sql = lambda sql: 'postgres\ndb\nanother_project'
    with pytest.raises(RuntimeError, match='其他数据库'):
        service.reset()
    assert not calls
