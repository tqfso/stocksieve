"""本文件准备本机运行材料，核实已有 Ubuntu 基础镜像并构建、启动后端容器。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import BinaryIO
from zoneinfo import TZPATH

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
    rate = ROOT / '.state/rate'
    rate.mkdir(parents=True, exist_ok=True)
    if rate.is_symlink():
        raise RuntimeError('取数账本目录不能是符号链接。')
    for path in [rate, *rate.iterdir()]:
        if path.is_symlink():
            raise RuntimeError('取数账本中不能包含符号链接。')
        os.chown(path, 10001, 10001)
    # 仅共享公开源的成功响应缓存；管理员私有凭据不在这些挂载目录内。
    for name in ('source-cache',):
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


def prepare_env() -> dict[str, str]:
    """首次生成私有配置；之后完整保留已有账号、历史窗口及渠道设置。"""
    target = ROOT / '.env'
    if not target.exists():
        content = (ROOT / '.env.example').read_text().replace('STOCKSIEVE_POSTGRES_PASSWORD=\n',
            'STOCKSIEVE_POSTGRES_PASSWORD=' + secrets.token_hex(32) + '\n')
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
    values = {}
    for line in target.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"\'')
    target.chmod(0o600)
    return {**values, **os.environ}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', default=DEFAULT_BASE)
    parser.add_argument('--image', default='stocksieve-backend:local')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prepare-only', action='store_true')
    modes.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    try:
        # 1. 使用本机 Ubuntu、uv 管理的 Python 和前端构建产物生成镜像。
        record = prepare()
        if args.prepare_only:
            return 0
        base_image = local_base(args.base_image, record)
        check_production_dependencies()
        command(['docker', 'build', '--pull=false', '--file', str(ROOT / 'deploy/docker/Dockerfile'),
                 '--build-arg', f'STOCKSIEVE_BASE_IMAGE={base_image}', '--tag', args.image, str(ROOT)])
        if args.build_only:
            return 0
        # 2. 接续固定的持久卷；初始化 SQL 仅由 PostgreSQL 在空卷首次启动时执行。
        env = {**prepare_env(), 'STOCKSIEVE_BACKEND_IMAGE': args.image}
        compose = ['docker', 'compose', '--project-directory', str(ROOT), '--env-file', str(ROOT / '.env'), '-f', str(COMPOSE)]
        command([*compose, 'config', '--quiet'], env=env)
        for name in ('stocksieve-postgres-data', 'stocksieve-backend-state'):
            command(['docker', 'volume', 'create', name], capture=True)
        command([*compose, 'up', '-d', '--pull', 'never', '--wait', 'postgres'], env=env)
        command([*compose, 'run', '--rm', '--no-deps', 'api', 'check-db'], env=env)
        # 3. 启动三个应用进程，检查容器内和映射端口上的 HTTP 服务。
        command([*compose, 'up', '-d', '--no-build', '--pull', 'never', 'api', 'worker', 'scheduler'], env=env)
        command([*compose, 'up', '-d', '--no-build', '--pull', 'never', '--wait', 'api'], env=env)
        running = set(command([*compose, 'ps', '--status', 'running', '--services'], capture=True, env=env).stdout.splitlines())
        if not {'postgres', 'api', 'worker', 'scheduler'} <= running:
            raise RuntimeError('四个服务未全部处于运行状态。')
        command([*compose, 'exec', '-T', 'api', '/app/.venv/bin/python', 'http_check.py',
                 '--base-url', 'http://127.0.0.1:8000'], env=env)
        port = env.get('STOCKSIEVE_API_PORT', '24048')
        command([sys.executable, str(ROOT / 'deploy/docker/http_check.py'), '--base-url', f'http://127.0.0.1:{port}'], env=env)
        print(f'部署完成：四个服务运行，前端和 API 共用宿主机端口 {port}。')
        return 0
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f'部署未完成：{error}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
