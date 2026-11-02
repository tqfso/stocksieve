"""本文件准备本机运行材料，核实已有 Ubuntu 基础镜像并构建、启动后端容器。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import BinaryIO
from zoneinfo import TZPATH

from stack import Stack, prepare_env

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / 'compose.yaml'
DEFAULT_BASE = 'ubuntu:24.04'


def command(args: list[str], *, capture: bool = False, env: dict[str, str] | None = None,
            stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> subprocess.CompletedProcess[str]:
    """参数始终按列表传递，镜像名和路径不作为 shell 代码执行。"""
    return subprocess.run(args, cwd=ROOT, env=env, check=True, text=True, stdin=stdin,
                          stdout=stdout if stdout is not None else subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def prepare() -> dict[str, str]:
    """复制可重定位的本机语言运行时；不复制宿主机 site-packages 或配置文件。"""
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('部署材料需在当前 Linux x86_64 / Python 3.12 环境通过 uv 准备。')
    uv = shutil.which('uv')
    if uv is None:
        raise RuntimeError('未找到 uv。')
    python = Path(sys.base_prefix)
    if not (python / 'bin/python3.12').is_file() or not python.name.startswith('cpython-3.12.'):
        raise RuntimeError('需要 uv 管理的可重定位 Python 3.12，不能复制系统 Python。')
    import certifi
    output = ROOT / '.docker-build'
    output.mkdir(exist_ok=True)
    # 挂载既有公开取数账本，保留探测阶段已消耗次数和冷却，容器不能另起空账本。
    rate = ROOT / '.source-probe'
    rate.mkdir(exist_ok=True)
    if rate.is_symlink():
        raise RuntimeError('取数账本目录不能是符号链接。')
    for path in [rate, *rate.iterdir()]:
        if path.is_symlink():
            raise RuntimeError('取数账本中不能包含符号链接。')
        os.chown(path, 10001, 10001)
    # 仅共享公开源的成功响应缓存；管理员私有凭据不在这些挂载目录内。
    for name in ('baostock-wire', 'source-cache'):
        public_cache = ROOT / '.state' / name
        public_cache.mkdir(parents=True, exist_ok=True)
        for path in [public_cache, *public_cache.rglob('*')]:
            if path.is_symlink():
                raise RuntimeError('公开取数缓存不能包含符号链接。')
            os.chown(path, 10001, 10001)
    web = ROOT.parent / 'frontend/dist'
    if not (web / 'index.html').is_file():
        raise RuntimeError('请先在frontend目录执行npm run build，部署只使用已构建的前端文件。')
    web_output = output / 'web'
    if web_output.is_symlink():
        raise RuntimeError('前端构建材料目录不能是符号链接。')
    if web_output.exists():
        shutil.rmtree(web_output)
    shutil.copytree(web, web_output)
    shutil.copy2(uv, output / 'uv')
    runtime = output / 'python'
    if runtime.is_symlink():
        raise RuntimeError('构建材料目录不能是符号链接。')
    if runtime.exists():
        shutil.rmtree(runtime)
    shutil.copytree(python, runtime / python.name, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'site-packages'))
    shutil.copyfile(certifi.where(), output / 'cacert.pem')
    for name in ('Asia/Shanghai', 'UTC'):
        source = next((Path(path) / name for path in TZPATH if (Path(path) / name).is_file()), None)
        if source is None:
            raise RuntimeError(f'本机缺少已验证的时区数据：{name}')
        destination = output / 'zoneinfo' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    record = {'python': python.name, 'pythonVersion': platform.python_version(),
              'uvVersion': command([uv, '--version'], capture=True).stdout.strip(),
              'uvSha256': hashlib.sha256((output / 'uv').read_bytes()).hexdigest()}
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(f"已准备 uv、Python {record['pythonVersion']}、前端构建和公开CA。")
    return record


def check_production_dependencies() -> None:
    """构建前检查声明与锁文件，不能用容器临时安装绕过项目依赖管理。"""
    with (ROOT / 'pyproject.toml').open('rb') as file:
        project = tomllib.load(file)
    with (ROOT / 'uv.lock').open('rb') as file:
        lock = tomllib.load(file)
    names = {item['name'] for item in lock['package']}
    declarations = project['project']['dependencies']
    if not any(re.split(r'[<>=!~;\[\s]', item, 1)[0].lower().replace('_', '-') == 'psycopg' for item in declarations) or not {'psycopg', 'psycopg-binary'} <= names:
        raise RuntimeError('PostgreSQL 驱动尚未通过 uv 声明和锁定；需先补齐 psycopg[binary]，不能部署为可用服务。')


def reuse_price_cache(archive: Path) -> None:
    """仅从本项目备份复用成功的公开行情响应，保留当前缓存和完整频控账本。"""
    folder = archive.resolve()
    if not folder.is_relative_to((ROOT / '.state/backups').resolve()) or not folder.is_dir():
        raise RuntimeError('只允许复用本项目备份目录中的公开行情缓存。')
    destination = ROOT / '.state/baostock-wire'
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in (folder / 'baostock-wire').glob('*.json'):
        if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
            continue
        saved = json.loads(path.read_text())
        fields = saved.get('reply', '')[21:].split('\x01', 3)
        if len(fields) < 3 or fields[:3] != ['0', 'success', 'query_daily_history_k_AStock']:
            continue
        target = destination / path.name
        if target.exists():
            continue
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(saved, stream)
        os.chown(target, 10001, 10001)
        copied += 1
    print(f'已复用{copied}份成功的公开行情缓存；未改写当前缓存或源频控账本。')


def local_base(image: str, record: dict[str, str]) -> str:
    """只检查本机镜像，并离线验证 Ubuntu、运行架构及复制的 uv/Python。"""
    explicit = '@' in image or ':' in image.rsplit('/', 1)[-1]
    references = [image] if explicit else command(['docker', 'image', 'ls', '--filter', f'reference={image}:*',
        '--format', '{{.Repository}}:{{.Tag}}'], capture=True).stdout.splitlines()
    if not references:
        raise RuntimeError('本机没有指定仓库的镜像，不自动拉取。')
    candidates = []
    for reference in dict.fromkeys(references):
        inspected = json.loads(command(['docker', 'image', 'inspect', reference], capture=True).stdout)[0]
        if inspected['Os'] == 'linux' and inspected['Architecture'] == 'amd64':
            candidates.append((reference, inspected))
    candidates.sort(key=lambda item: (item[0].endswith(':latest'), item[1].get('Created', '')), reverse=True)
    payload = ROOT / '.docker-build'
    binary = shlex.quote('/opt/python/' + record['python'] + '/bin/python3.12')
    probe = shlex.quote("import ssl, hashlib; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); assert hasattr(hashlib, 'scrypt')")
    script = '. /etc/os-release; test "$ID" = ubuntu; /opt/stocksieve-uv --version; ' + binary + ' -c ' + probe
    for reference, inspected in candidates:
        try:
            command(['docker', 'run', '--rm', '--pull=never', '--network=none', '--entrypoint', '/bin/sh',
                     '--env', 'PYTHONTZPATH=/opt/stocksieve-zoneinfo',
                     '--mount', f'type=bind,src={payload / "uv"},dst=/opt/stocksieve-uv,readonly',
                     '--mount', f'type=bind,src={payload / "python"},dst=/opt/python,readonly',
                     '--mount', f'type=bind,src={payload / "zoneinfo"},dst=/opt/stocksieve-zoneinfo,readonly', reference, '-ec', script])
        except subprocess.CalledProcessError as error:
            if explicit or error.returncode == 125:
                raise
            continue
        print(f"已核实本机基础镜像：{reference} ({inspected['Id']})")
        return reference
    raise RuntimeError('没有通过 Ubuntu、linux/amd64 和运行材料兼容性检查的本机镜像。')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', default=os.environ.get('STOCKSIEVE_BASE_IMAGE', DEFAULT_BASE))
    parser.add_argument('--image', default='stocksieve-backend:local')
    parser.add_argument('--reuse-price-cache-from', type=Path, help='从项目私有备份复用成功的公开行情响应，避免重复取数')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prepare-only', action='store_true')
    modes.add_argument('--build-only', action='store_true')
    modes.add_argument('--database-only', action='store_true', help='首次仅准备统一配置、数据库及结构')
    modes.add_argument('--validate-stack', action='store_true', help='准备统一配置并检查资源接续计划，不停止服务')
    modes.add_argument('--reset-data', action='store_true', help='明确清空重建：备份后删除本项目容器、数据库和状态卷，再部署空库')
    args = parser.parse_args()
    stack = None
    credentials = None
    try:
        if args.validate_stack:
            env = prepare_env()
            command([sys.executable, str(ROOT / 'run.py'), 'check-config'], env=env)
            planned = Stack(command, env)
            migrate = planned.prepare()
            print(json.dumps({'compose': str(COMPOSE), 'postgresMigrationRequired': migrate,
                              'persistentVolumes': ['stocksieve-postgres-data', 'stocksieve-backend-state']}, ensure_ascii=False))
            return 0
        if args.database_only:
            env = prepare_env()
            command([sys.executable, str(ROOT / 'run.py'), 'check-config'], env=env)
            stack = Stack(command, env)
            migrate = stack.prepare()
            stack.start_database(migrate, database_only=True)
            command([sys.executable, str(ROOT / 'run.py'), 'sync-schema', '--apply'])
            stack.finish()
            print('数据库已在backend/compose.yaml下就绪，私有配置位于backend/.env。')
            return 0
        record = prepare()
        if args.reuse_price_cache_from:
            reuse_price_cache(args.reuse_price_cache_from)
        if args.prepare_only:
            return 0
        base_image = local_base(args.base_image, record)
        check_production_dependencies()
        command(['docker', 'build', '--pull=false', '--file', str(ROOT / 'deploy/docker/Dockerfile'),
                 '--build-arg', f'STOCKSIEVE_BASE_IMAGE={base_image}', '--tag', args.image, str(ROOT)])
        if args.build_only:
            return 0
        env = {**prepare_env(), 'STOCKSIEVE_BACKEND_IMAGE': args.image}
        command([sys.executable, str(ROOT / 'run.py'), 'check-config'], env=env)
        stack = Stack(command, env)
        if args.reset_data:
            credentials = json.loads((ROOT / '.state/initial-admin.json').read_text())
            if not isinstance(credentials.get('username'), str) or not isinstance(credentials.get('password'), str) or not credentials['password']:
                raise RuntimeError('重建所需的管理员私有凭据不完整，未清理。')
            stack.reset()
        migrate = stack.prepare()
        stack.start_database(migrate)
        # PG健康后直接对齐项目SQL约束；既有数据只审计、校验，不重新初始化。
        command([sys.executable, str(ROOT / 'run.py'), 'sync-schema', '--apply'])
        if credentials:
            command([sys.executable, str(ROOT / 'run.py'), 'create-admin', '--username', credentials['username'],
                     '--password-env', 'STOCKSIEVE_RESET_ADMIN_PASSWORD'],
                    env={**env, 'STOCKSIEVE_RESET_ADMIN_PASSWORD': credentials['password']})
        compose = stack.compose
        command([*compose, 'run', '--rm', '--no-deps', 'api', 'check-db'], env=env)
        command([*compose, 'up', '-d', '--no-build', '--pull', 'never', 'api', 'worker', 'scheduler'], env=env)
        # Compose旧版本对没有healthcheck的后台进程无法使用--wait，只等待API的真实健康检查。
        command([*compose, 'up', '-d', '--no-build', '--pull', 'never', '--wait', 'api'], env=env)
        running = set(command([*compose, 'ps', '--status', 'running', '--services'], capture=True, env=env).stdout.splitlines())
        if not {'postgres', 'api', 'worker', 'scheduler'} <= running:
            raise RuntimeError('后端进程未全部处于运行状态。')
        port = env.get('STOCKSIEVE_API_PORT', '8000')
        command([*compose, 'exec', '-T', 'api', '/usr/local/bin/uv', 'run', '--frozen', '--no-dev', '--no-sync',
                 'python', 'http_check.py', '--base-url', f'http://127.0.0.1:{port}'], env=env)
        command([*compose, 'exec', '-T', 'api', '/app/.venv/bin/python', 'run.py', 'check-config'], env=env)
        overridden = command([*compose, 'run', '--rm', '--no-deps', '--env', 'STOCKSIEVE_HISTORY_TRADING_DAYS=250',
            '--env', 'STOCKSIEVE_HISTORY_MAX_TRADING_DAYS=500', 'api', 'check-config'], capture=True, env=env)
        active = json.loads(overridden.stdout)
        if active['historyTradingDays'] != 250 or active['historyMaxTradingDays'] != 500:
            raise RuntimeError('历史配置的容器环境覆盖校验失败。')
        print('历史配置容器覆盖校验通过：250/500个交易日，仅检查配置，未执行行情同步。')
        stack.finish()
        print('容器已通过基础HTTP检查；账号与真实数据验收仍需提供登录配置并运行完整检查。')
        return 0
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        if stack:
            try:
                stack.rollback()
            except Exception:
                print('自动恢复未完成；原数据卷与数据库备份保留，请核对容器状态。')
        print(f'部署未完成：{error}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
