"""本文件提供 API、worker、scheduler、数据库检查及管理员初始化命令。"""

import argparse
import asyncio
import getpass
import json
import os

from aiohttp import web
from sqlalchemy import inspect, text

from .agent import create_runtime
from .analysis import Analysis
from .api import API
from .config import Settings
from .database import Database
from .errors import DomainError
from .sources import RateGate, Registry, SourceHTTP
from .sync import Sync
from .tables import metadata
from .worker import Scheduler, Worker


def main() -> int:
    parser = argparse.ArgumentParser(description='StockSieve 后端运行与维护')
    sub = parser.add_subparsers(dest='command', required=True)
    api = sub.add_parser('api')
    api.add_argument('--host', default='127.0.0.1')
    api.add_argument('--port', type=int, default=8000)
    sub.add_parser('worker')
    sub.add_parser('scheduler')
    check = sub.add_parser('check-db')
    check.add_argument('--counts', action='store_true', help='只读显示public关键业务表行数与剩余临时测试schema数量')
    admin = sub.add_parser('create-admin')
    admin.add_argument('--username', default='admin')
    admin.add_argument('--name', default='管理员')
    admin.add_argument('--password-env', help='从指定环境变量读取密码，密码不作为命令行参数')
    args = parser.parse_args()
    database = None
    try:
        settings = Settings.from_env()
        registry = Registry()
        if set(settings.enabled_sources) - registry.sources.keys():
            raise RuntimeError('请求启用的渠道尚无通过覆盖验证的适配器；不能仅靠配置将候选源标为可用。')
        database = Database(settings.database_url)
        runtime = create_runtime(settings)
        service = API(database, settings, registry, runtime)
        if args.command == 'check-db':
            inspector = inspect(database.engine)
            for name, table in metadata.tables.items():
                if name not in inspector.get_table_names():
                    raise RuntimeError(f'数据库缺少表：{name}')
                actual = {column['name'] for column in inspector.get_columns(name)}
                if set(table.c.keys()) - actual:
                    raise RuntimeError(f'数据库表字段不匹配：{name}')
            print('数据库连接和17张业务表字段检查通过。')
            if args.counts:
                with database.transaction() as connection:
                    rows = {name: connection.scalar(text(f'SELECT count(*) FROM public.{name}')) for name in
                            ('users', 'stocks', 'tasks', 'data_snapshots', 'daily_bars', 'strategy_signals', 'analysis_results')}
                    rows['temporaryTestSchemas'] = connection.scalar(text("SELECT count(*) FROM pg_catalog.pg_namespace WHERE nspname ~ '^stocksieve_test_[a-f0-9]{16}$'"))
                print(json.dumps(rows, ensure_ascii=False, indent=2))
        elif args.command == 'create-admin':
            password = os.environ.get(args.password_env) if args.password_env else getpass.getpass('管理员密码：')
            if not password:
                raise RuntimeError('未提供管理员密码。')
            user = service.auth.create_user(args.username, args.name, password, admin=True)
            print(f"管理员已创建：{user['username']}")
        elif args.command == 'api':
            web.run_app(service.app, host=args.host, port=args.port, access_log=None)
        elif args.command == 'scheduler':
            asyncio.run(Scheduler(service.tasks).serve())
        else:
            async def run_worker() -> None:
                http = SourceHTTP(RateGate(settings.state_dir / 'rate.sqlite', settings.request_interval, settings.source_daily_limit), settings.state_dir / 'source-cache')
                try:
                    await Worker(service.tasks, Sync(service.tasks, registry), Analysis(service.tasks, runtime, http)).serve()
                finally:
                    await http.close()
            asyncio.run(run_worker())
    except ModuleNotFoundError as error:
        print(f'运行依赖尚未安装：{error.name}。请先完成后端依赖准备。')
        return 2
    except DomainError as error:
        print(f'{error.code}: {error.message}')
        return 2
    except RuntimeError as error:
        print(str(error))
        return 2
    finally:
        if database:
            database.close()
    return 0
