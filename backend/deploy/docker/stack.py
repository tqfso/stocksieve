"""本文件管理统一Compose，提供保留数据的迁移和显式备份后清空重建流程。"""

import json
import os
import secrets
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / 'compose.yaml'
PROJECT = 'stocksieve-backend'
POSTGRES = 'stocksieve-postgres'
PG_VOLUME = 'stocksieve-postgres-data'
STATE_VOLUME = 'stocksieve-backend-state'
TABLES = ('users', 'stocks', 'tasks', 'data_snapshots', 'daily_bars', 'strategy_signals', 'analysis_results')


class Runner(Protocol):
    """部署命令端口便于测试接管与回滚顺序，不在单元测试中操作Docker。"""

    def __call__(self, args: list[str], *, capture: bool = False, env: dict[str, str] | None = None,
                 stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> subprocess.CompletedProcess[str]: ...


def read_env(path: Path) -> dict[str, str]:
    """读取本项目键值配置，不向终端回显配置内容。"""
    values = {}
    if path.is_symlink() or path.exists() and not path.is_file():
        raise RuntimeError('配置必须是普通文件。')
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"\'')
    return values


def prepare_env(root: Path = ROOT) -> dict[str, str]:
    """将旧数据库配置合入唯一私有env，已有口令和其他应用配置保持原值。"""
    target = root / '.env'
    existing = read_env(target)
    legacy = read_env(root / 'deploy/postgres/.env')
    additions = {}
    for key, value in legacy.items():
        if not key.startswith('STOCKSIEVE_POSTGRES_'):
            continue
        if key in existing and existing[key] and existing[key] != value:
            raise RuntimeError('统一env与旧数据库配置不一致，未覆盖已有配置。')
        if key not in existing or not existing[key]:
            additions[key] = value
    merged = {**existing, **additions}
    for key, value in {'STOCKSIEVE_POSTGRES_USER': 'stocksieve_admin', 'STOCKSIEVE_POSTGRES_DB': 'stocksieve',
                       'STOCKSIEVE_POSTGRES_PORT': '5432', 'STOCKSIEVE_API_PORT': '24048', 'STOCKSIEVE_HISTORY_TRADING_DAYS': '150',
                       'STOCKSIEVE_HISTORY_MAX_TRADING_DAYS': '0'}.items():
        if key not in merged:
            additions[key] = value
    if not merged.get('STOCKSIEVE_POSTGRES_PASSWORD'):
        if legacy:
            raise RuntimeError('旧数据库配置缺少口令，未生成替代口令。')
        additions['STOCKSIEVE_POSTGRES_PASSWORD'] = secrets.token_hex(32)
    if additions:
        lines = target.read_text().splitlines() if target.exists() else ['# 本文件为当前服务器私有配置，不提交Git。']
        # 空值项原位补齐，避免同一键出现两次。
        lines = [line for line in lines if line.split('=', 1)[0].strip() not in additions or '=' not in line]
        content = '\n'.join(lines).rstrip() + '\n' + ''.join(f'{key}={value}\n' for key, value in additions.items())
        temporary = target.with_name('.env.prepare.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
        temporary.replace(target)
    target.chmod(0o600)
    return {**read_env(target), **os.environ}


def validate_postgres(info: dict[str, Any], values: dict[str, str]) -> str:
    """只接续已知项目、镜像和数据卷；检查失败时不停止任何容器。"""
    labels = info['Config'].get('Labels') or {}
    project = labels.get('com.docker.compose.project')
    if project not in {'stocksieve-postgres', PROJECT} or labels.get('com.docker.compose.service') != 'postgres' or info['Config']['Image'] != 'postgres:15-alpine':
        raise RuntimeError('同名数据库容器不属于已知部署，未接管。')
    mounts = [entry for entry in info.get('Mounts', []) if entry['Destination'] == '/var/lib/postgresql/data']
    if len(mounts) != 1 or mounts[0].get('Type') != 'volume' or mounts[0].get('Name') != PG_VOLUME:
        raise RuntimeError('现有数据库数据卷与预期不一致，未接管。')
    current = dict(item.split('=', 1) for item in info['Config'].get('Env', []) if '=' in item)
    for old, new in [('POSTGRES_USER', 'STOCKSIEVE_POSTGRES_USER'), ('POSTGRES_DB', 'STOCKSIEVE_POSTGRES_DB'), ('POSTGRES_PASSWORD', 'STOCKSIEVE_POSTGRES_PASSWORD')]:
        if current.get(old) != values.get(new):
            raise RuntimeError('数据库容器与统一env的认证配置不一致，未接管。')
    bindings = info.get('HostConfig', {}).get('PortBindings', {}).get('5432/tcp') or []
    if not any(item.get('HostPort') == values.get('STOCKSIEVE_POSTGRES_PORT', '5432') for item in bindings):
        raise RuntimeError('数据库端口与统一配置不一致，未接管。')
    return project


class Stack:
    """备份后顺序迁移；旧容器先停止并改名，成功后才移除，失败可恢复原容器。"""

    def __init__(self, runner: Runner, env: dict[str, str]) -> None:
        self.run, self.env = runner, env
        self.compose = ['docker', 'compose', '--project-directory', str(ROOT), '--env-file', str(ROOT / '.env'), '-f', str(COMPOSE)]
        self.archive: str | None = None
        self.apps_stopped = False
        self.baseline: dict[str, int] | None = None

    def inspect(self, kind: str, name: str) -> dict[str, Any] | None:
        try:
            return json.loads(self.run(['docker', kind, 'inspect', name], capture=True).stdout)[0]
        except subprocess.CalledProcessError as error:
            if error.returncode == 1:
                return None
            raise

    def prepare(self) -> bool:
        """校验现有资源并准备外部卷；Compose不会删除这两个持久化卷。"""
        if value := self.env.get('STOCKSIEVE_DATABASE_URL'):
            url = urlsplit(value)
            if url.hostname not in {'127.0.0.1', 'localhost'} or (url.port or 5432) != int(self.env.get('STOCKSIEVE_POSTGRES_PORT', '5432')) or unquote(url.path.lstrip('/')) != self.env.get('STOCKSIEVE_POSTGRES_DB', 'stocksieve'):
                raise RuntimeError('显式数据库URL与统一Compose的PostgreSQL不一致，未迁移。')
        self.run(['docker', 'info'], capture=True)
        self.run(['docker', 'image', 'inspect', 'postgres:15-alpine'], capture=True)
        info = self.inspect('container', POSTGRES)
        project = validate_postgres(info, self.env) if info else None
        pg_volume = self.inspect('volume', PG_VOLUME)
        if pg_volume and not info:
            raise RuntimeError('发现孤立的已有数据库卷，请先核对恢复，未自动接续。')
        for name in (PG_VOLUME, STATE_VOLUME):
            if self.inspect('volume', name) is None:
                self.run(['docker', 'volume', 'create', '--label', 'stocksieve.role=persistent', name], capture=True)
        self.run([*self.compose, 'config', '--quiet'], env=self.env)
        return project == 'stocksieve-postgres'

    def sql(self, sql: str) -> str:
        return self.run(['docker', 'exec', POSTGRES, 'sh', '-eu', '-c',
            'exec psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "$1"', 'sh', sql], capture=True).stdout.strip()

    def counts(self) -> dict[str, int]:
        fields = [f"'{name}',(SELECT count(*) FROM public.{name})" for name in TABLES]
        return json.loads(self.sql('SELECT json_build_object(' + ','.join(fields) + ')'))

    def backup(self) -> Path:
        """备份权限600，验证可读后再开始数据库容器迁移；不输出SQL数据。"""
        folder = ROOT / '.state/backups'
        if folder.is_symlink() or folder.parent.is_symlink():
            raise RuntimeError('备份目录不能是符号链接。')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ('compose-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.dump')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                self.run(['docker', 'exec', POSTGRES, 'sh', '-eu', '-c', 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'], stdout=stream)
            with path.open('rb') as stream:
                toc = self.run(['docker', 'exec', '-i', POSTGRES, 'pg_restore', '--list'], capture=True, stdin=stream).stdout
            if path.stat().st_size < 1024 or 'TABLE public daily_bars' not in toc or 'TABLE public users' not in toc:
                raise RuntimeError('数据库备份目录校验失败。')
        except Exception:
            raise RuntimeError('数据库备份未完成，未开始迁移。') from None
        print(f'已保存并校验数据库备份：{path}（权限600）')
        return path

    def reset(self) -> None:
        """仅供明确要求清空重建时调用：核对项目归属，备份后删除本项目两只卷。"""
        # 1. 清理前核对容器、数据库和卷的独占归属，不能波及同机其他项目。
        self.prepare()
        info = self.inspect('container', POSTGRES)
        if info is None or validate_postgres(info, self.env) != PROJECT:
            raise RuntimeError('清空重建只支持已完成统一Compose部署的本项目数据库。')
        databases = set(self.sql('SELECT datname FROM pg_database WHERE NOT datistemplate').splitlines())
        if databases - {'postgres', self.env['STOCKSIEVE_POSTGRES_DB']}:
            raise RuntimeError('PostgreSQL卷还包含其他数据库，未执行清理。')
        other_tables = self.run(['docker', 'exec', POSTGRES, 'sh', '-eu', '-c',
            'exec psql -X -At -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "$1"', 'sh',
            "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"], capture=True).stdout.strip()
        if other_tables != '0':
            raise RuntimeError('默认postgres数据库包含其他表，未执行清理。')
        for volume in (PG_VOLUME, STATE_VOLUME):
            containers = self.run(['docker', 'ps', '-aq', '--filter', f'volume={volume}'], capture=True).stdout.splitlines()
            for identifier in containers:
                consumer = self.inspect('container', identifier)
                labels = (consumer or {}).get('Config', {}).get('Labels') or {}
                if labels.get('com.docker.compose.project') != PROJECT or labels.get('com.docker.compose.service') not in {'postgres', 'api', 'worker', 'scheduler'}:
                    raise RuntimeError('持久化卷被其他容器引用，未执行清理。')
        state = ROOT / '.state'
        if state.is_symlink() or any(path.is_symlink() for path in state.iterdir()):
            raise RuntimeError('状态目录包含符号链接，未执行清理。')
        # 2. 停止应用写入；保存已校验的数据库备份和原配置，管理员凭据仍可复用。
        self.run([*self.compose, 'stop', 'api', 'worker', 'scheduler'], env=self.env)
        self.apps_stopped = True
        baseline = self.counts()
        backup = self.backup()
        archive = state / 'backups' / ('reset-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
        archive.mkdir(mode=0o700)
        shutil.copy2(ROOT / '.env', archive / 'server.env')
        (archive / 'server.env').chmod(0o600)
        # 3. 移除本项目容器和数据卷；源频控账本独立保留，不能因重建重置额度或冷却。
        self.run([*self.compose, 'down', '--timeout', '60'], env=self.env)
        self.apps_stopped = False
        for volume in (PG_VOLUME, STATE_VOLUME):
            self.run(['docker', 'volume', 'rm', volume])
        for path in list(state.iterdir()):
            if path.name not in {'backups', 'initial-admin.json'}:
                path.rename(archive / path.name)
        for name in ('baostock-wire', 'source-cache'):
            path = state / name
            path.mkdir()
            os.chown(path, 10001, 10001)
        manifest = archive / 'manifest.json'
        manifest.write_text(json.dumps({'databaseBackup': str(backup), 'previousCounts': baseline,
            'removedVolumes': [PG_VOLUME, STATE_VOLUME], 'rateLedgerPreserved': True}, ensure_ascii=False, indent=2) + '\n')
        manifest.chmod(0o600)
        print(f'本项目容器和数据卷已清理；恢复资料位于：{archive}。频控账本与管理员私有凭据保留。')

    def start_database(self, migrate: bool, *, database_only: bool = False) -> None:
        # 1. 旧项目迁移前固定数据状态，保留旧容器作为失败恢复入口。
        if migrate:
            if database_only:
                raise RuntimeError('已有旧部署，请使用完整部署命令合并数据库与后端。')
            if int(self.sql("SELECT count(*) FROM public.tasks WHERE status='running'")):
                raise RuntimeError('后台任务正在执行，请等待完成后部署，未停止服务。')
            self.run([*self.compose, 'stop', 'api', 'worker', 'scheduler'], env=self.env)
            self.apps_stopped = True
            self.baseline = self.counts()
            self.backup()
            self.run(['docker', 'stop', '--time', '60', POSTGRES])
            archived = POSTGRES + '-pre-merge-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            self.run(['docker', 'rename', POSTGRES, archived])
            self.archive = archived
        # 2. 统一Compose挂载同一卷；业务SQL仅在完全空的目标库执行。
        self.run([*self.compose, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '90', 'postgres'], env=self.env)
        count = int(self.sql("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'"))
        if count == 0:
            with (ROOT / 'sql/init.sql').open('rb') as stream:
                self.run(['docker', 'exec', '-i', POSTGRES, 'sh', '-eu', '-c', 'exec psql -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'], stdin=stream)
        elif count != 17:
            raise RuntimeError('现有数据库表数不符合本项目结构，未重新初始化。')
        if self.baseline is not None and self.counts() != self.baseline:
            raise RuntimeError('迁移前后关键业务行数不一致，已停止应用启动。')

    def finish(self) -> None:
        if self.archive:
            self.run(['docker', 'container', 'rm', self.archive])
            self.archive = None
        legacy = ROOT / 'deploy/postgres/.env'
        if legacy.exists():
            folder = ROOT / '.state/backups'
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ('legacy-postgres-env-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
            legacy.replace(target)
            target.chmod(0o600)
            print('旧数据库env已保存到私有备份目录，后续仅使用backend/.env。')

    def rollback(self) -> None:
        """迁移失败时停止新容器后恢复旧容器，绝不同时启动两个同卷数据库。"""
        if self.archive:
            info = self.inspect('container', POSTGRES)
            if info:
                if (info['Config'].get('Labels') or {}).get('com.docker.compose.project') != PROJECT:
                    raise RuntimeError('回滚发现非本项目同名容器，未修改。')
                self.run(['docker', 'stop', '--time', '60', POSTGRES])
                self.run(['docker', 'container', 'rm', POSTGRES])
            self.run(['docker', 'rename', self.archive, POSTGRES])
            self.archive = None
            self.run(['docker', 'start', POSTGRES])
        if self.apps_stopped:
            self.run([*self.compose, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'api', 'worker', 'scheduler'], env=self.env)
            self.apps_stopped = False
