"""本文件提供 API、worker、scheduler、数据库检查及管理员初始化命令。"""

import argparse
import asyncio
import getpass
import json
import os
import secrets
from pathlib import Path

from aiohttp import web
from sqlalchemy import inspect, text

from stocksieve.infrastructure.persistence.database import Database
from stocksieve.infrastructure.persistence.schema import metadata as table_metadata
from stocksieve.interfaces.http.api import API
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.infrastructure.agent import create_runtime
from stocksieve.modules.analysis.infrastructure.reader import DocumentReader
from stocksieve.modules.analysis.infrastructure.research import CninfoSearch
from stocksieve.modules.ingestion.application.registry import Registry
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.jobs.application.worker import Scheduler, Worker
from stocksieve.shared.errors import DomainError
from stocksieve.shared.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description='StockSieve 后端运行与维护')
    sub = parser.add_subparsers(dest='command', required=True)
    api = sub.add_parser('api')
    api.add_argument('--host', default='127.0.0.1')
    api.add_argument('--port', type=int, default=8000)
    sub.add_parser('worker')
    sub.add_parser('scheduler')
    sub.add_parser('check-config', help='只读显示非敏感的历史窗口及运行模式配置')
    check = sub.add_parser('check-db', help='检查数据库连接和业务表字段')
    check.add_argument('--counts', action='store_true', help='只读统计业务数据和临时测试schema数量')
    admin = sub.add_parser('create-admin')
    admin.add_argument('--username', default='admin')
    admin.add_argument('--name', default='管理员')
    password_options = admin.add_mutually_exclusive_group()
    password_options.add_argument('--password-env', help='从指定环境变量读取密码，密码不作为命令行参数')
    password_options.add_argument('--generate-password-file', type=Path, help='生成随机初始口令，保存到仅当前服务器用户可读的新文件，不在终端显示')
    args = parser.parse_args()
    database = None
    try:
        settings = Settings.from_env()
        registry = Registry(settings.market_boards)
        policy = SourcePolicy(settings.rate_dir, settings.request_interval, settings.source_daily_limit)
        if 'sina' in settings.enabled_sources:
            from stocksieve.modules.ingestion.infrastructure.providers.sina import (
                SinaSource,
            )
            registry.register(SinaSource(policy, settings.state_dir / 'source-cache', settings.history_trading_days,
                settings.history_max_trading_days))
        if 'baidu_concepts' in settings.enabled_sources:
            from stocksieve.modules.ingestion.infrastructure.providers.baidu import (
                BaiduCatalogSource,
                BaiduClassifications,
            )
            registry.register(BaiduCatalogSource(BaiduClassifications(policy, settings.state_dir / 'source-cache', batch_limit=300)))
        if set(settings.enabled_sources) - registry.sources.keys():
            raise RuntimeError('请求启用的渠道尚无通过覆盖验证的适配器；不能仅靠配置将候选源标为可用。')
        database = Database(settings.database_url)
        runtime = create_runtime(settings)
        service = API(database, settings, registry, runtime)
        if args.command == 'check-config':
            print(json.dumps({'defaultSource': settings.default_source, 'enabledSources': list(settings.enabled_sources), 'historyTradingDays': settings.history_trading_days, 'historyMaxTradingDays': settings.history_max_trading_days,
                              'sourceIntervalSeconds': settings.request_interval, 'sourceDailyLimit': settings.source_daily_limit, 'sourceForegroundReserve': policy.foreground_reserve,
                              'marketBoards': list(settings.market_boards), 'aiMode': settings.ai_mode}, ensure_ascii=False))
        elif args.command == 'check-db':
            inspector = inspect(database.engine)
            for name, table in table_metadata.tables.items():
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
            generated = args.generate_password_file
            password = secrets.token_urlsafe(18) if generated else os.environ.get(args.password_env) if args.password_env else getpass.getpass('管理员密码：')
            if not password:
                raise RuntimeError('未提供管理员密码。')
            if generated:
                generated.parent.mkdir(parents=True, exist_ok=True)
                # 先安全保存新口令，再创建账号，避免创建成功后丢失凭据；已有文件绝不覆盖。
                try:
                    descriptor = os.open(generated, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    raise RuntimeError('初始口令文件已存在，不覆盖已有凭据。') from None
                try:
                    with os.fdopen(descriptor, 'w') as file:
                        json.dump({'username': args.username, 'password': password}, file)
                        file.write('\n')
                    user = service.auth.create_user(args.username, args.name, password, admin=True)
                except BaseException:
                    generated.unlink(missing_ok=True)
                    raise
                print(f'随机初始口令已保存：{generated.resolve()}（仅文件所有者可读）')
            else:
                user = service.auth.create_user(args.username, args.name, password, admin=True)
            print(f"管理员已创建：{user['username']}")
        elif args.command == 'api':
            web.run_app(service.app, host=args.host, port=args.port, access_log=None)
        elif args.command == 'scheduler':
            asyncio.run(Scheduler(service.tasks).serve())
        else:
            async def run_worker() -> None:
                http = SourceHTTP(policy, settings.state_dir / 'source-cache')
                try:
                    await Worker(service.tasks, Sync(service.tasks, registry),
                        Analysis(service.tasks, runtime, http, CninfoSearch(http), DocumentReader())).serve()
                finally:
                    await http.close()
            asyncio.run(run_worker())
    except ModuleNotFoundError as error:
        print(f'运行依赖尚未安装：{error.name}。请先完成后端依赖准备。')
        return 2
    except DomainError as error:
        print(f'{error.code}: {error.message}')
        if args.command.startswith('check-') and error.__cause__:
            print('核验诊断类型：' + type(error.__cause__).__name__)
        return 2
    except RuntimeError as error:
        print(str(error))
        return 2
    finally:
        if database:
            database.close()
    return 0
