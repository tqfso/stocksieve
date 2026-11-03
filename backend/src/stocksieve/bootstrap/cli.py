"""本文件提供 API、worker、scheduler、数据库检查及管理员初始化命令。"""

import argparse
import asyncio
import getpass
import json
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Any

from aiohttp import web
from sqlalchemy import inspect, text

from stocksieve.infrastructure.persistence.database import Database
from stocksieve.infrastructure.persistence.schema import metadata as table_metadata
from stocksieve.interfaces.http.api import API
from stocksieve.modules.analysis.application.service import Analysis
from stocksieve.modules.analysis.infrastructure.agent import create_runtime
from stocksieve.modules.ingestion.application.ports import SourceDeferred
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
    dedup = sub.add_parser('reconcile-sync-duplicates', help='复用已成功结果，停止旧版本受理的重复普通收盘任务')
    dedup.add_argument('--apply', action='store_true')
    baidu_check = sub.add_parser('check-baidu-classifications', help='按正式频控核对全量行业与题材，保留缓存，不写分类业务表')
    baidu_check.add_argument('--symbol', action='append', help='少量指定代码时仅核对原始行业分组')
    sync_check = sub.add_parser('inspect-sync', help='只读查看同步检查点和下一只证券，可复现取数异常')
    sync_check.add_argument('--job', required=True)
    sync_check.add_argument('--probe', action='store_true')
    sina_check = sub.add_parser('check-sina', help='沿用正式账本验证新浪四板块日线、因子、股本和交易日历，不写业务表')
    sina_check.add_argument('--date', type=date.fromisoformat, required=True)
    sina_check.add_argument('--directory', action='store_true', help='同时分页核对完整沪深目录')
    sina_check.add_argument('--basics', action='store_true', help='少量串行请求核对新浪基础资料与行业端点')
    sina_check.add_argument('--incremental', action='store_true', help='用真实数据库历史核对四板块增量，不写业务行情')
    retire = sub.add_parser('retire-source-jobs', help='切换默认渠道后停止旧渠道排队任务，保留已发布数据和历史日志')
    retire.add_argument('--source', required=True)
    retire.add_argument('--apply', action='store_true')
    check = sub.add_parser('check-db')
    check.add_argument('--counts', action='store_true', help='只读显示public关键业务表行数与剩余临时测试schema数量')
    check.add_argument('--constraints', action='store_true', help='只读显示真实任务约束，核对现有数据库与初始化定义是否一致')
    check.add_argument('--sync-jobs', action='store_true', help='只读显示最近收盘任务的就绪检查和等待原因')
    schema = sub.add_parser('sync-schema', help='审计或直接对齐现有库与当前初始化SQL的约束，不重建业务表')
    schema.add_argument('--apply', action='store_true')
    schema.add_argument('--details', action='store_true')
    recovery = sub.add_parser('recover-history-bootstrap', help='将旧自然年配置的首次排队任务恢复为近期发布、历史回补')
    recovery.add_argument('--apply', action='store_true')
    sub.add_parser('check-quote-metadata', help='只读检查免费批量市值、行业等补充字段')
    sub.add_parser('check-exchange-directory', help='只读核对深交所官方目录和新增代码板块')
    sub.add_parser('reconcile-tencent', help='核验证据后纠正已确认的本地格式误判冷却，保留真实限流和全部次数')
    metadata_check = sub.add_parser('check-tencent-metadata', help='核验指定真实证券的板块标签和总市值')
    metadata_check.add_argument('--symbol', action='append', required=True)
    pipeline = sub.add_parser('bootstrap-data', help='通过真实HTTP完成初始化、分类和收盘发布验收，可跨中断恢复')
    pipeline.add_argument('--credentials-file', required=True, type=Path)
    pipeline.add_argument('--state-file', type=Path, default=Path('backend/.state/live-pipeline.json'))
    pipeline.add_argument('--source', default='sina', help='默认新浪证券与行情；分类步骤自动使用百度行业与题材')
    pipeline.add_argument('--date', type=date.fromisoformat)
    pipeline.add_argument('--retry-failed', action='store_true')
    pipeline.add_argument('--submit-only', action='store_true', help='配合--only-kind，仅提交并保存后台任务，不等待多年历史完成')
    pipeline.add_argument('--force', action='store_true', help='明确重新计算已发布日期，普通更新默认复用成功结果')
    scope = pipeline.add_mutually_exclusive_group()
    scope.add_argument('--skip-initialize', action='store_true', help='只刷新分类并重新发布，不重复基础初始化')
    scope.add_argument('--only-kind', choices=('initialize', 'catalog', 'daily'), help='仅验收一种任务，不重复其他已完成步骤')
    pipeline.add_argument('--base-url', default='http://127.0.0.1:8000')
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
                BaiduConcepts,
            )
            registry.register(BaiduCatalogSource(BaiduConcepts(policy, settings.state_dir / 'source-cache', batch_limit=300, include_industry=True)))
        if set(settings.enabled_sources) - registry.sources.keys():
            raise RuntimeError('请求启用的渠道尚无通过覆盖验证的适配器；不能仅靠配置将候选源标为可用。')
        database = Database(settings.database_url)
        runtime = create_runtime(settings)
        service = API(database, settings, registry, runtime)
        if args.command == 'check-config':
            print(json.dumps({'defaultSource': settings.default_source, 'enabledSources': list(settings.enabled_sources), 'historyTradingDays': settings.history_trading_days, 'historyMaxTradingDays': settings.history_max_trading_days,
                              'sourceIntervalSeconds': settings.request_interval, 'sourceDailyLimit': settings.source_daily_limit, 'sourceForegroundReserve': policy.foreground_reserve,
                              'marketBoards': list(settings.market_boards), 'aiMode': settings.ai_mode}, ensure_ascii=False))
        elif args.command == 'reconcile-sync-duplicates':
            from stocksieve.operations.sync_dedup import reconcile_duplicates
            print(json.dumps(reconcile_duplicates(service.tasks, apply=args.apply), ensure_ascii=False, indent=2))
        elif args.command == 'check-baidu-classifications':
            from datetime import datetime

            from sqlalchemy import select

            import stocksieve.infrastructure.persistence.schema as tables
            from stocksieve.modules.ingestion.application.progress import progress_sink
            from stocksieve.modules.ingestion.application.service import stock_value
            from stocksieve.modules.ingestion.infrastructure.providers.baidu import (
                BaiduConcepts,
            )
            with database.transaction() as connection:
                stocks = tuple(stock_value(row) for row in connection.execute(select(tables.stocks)).mappings())
            if args.symbol:
                from urllib.parse import urlencode
                async def inspect_groups() -> dict[str, Any]:
                    client = SourceHTTP(policy, settings.state_dir / 'source-cache')
                    try:
                        query = {'stock': json.dumps([{'code': code, 'market': 'ab', 'type': 'stock'} for code in args.symbol], separators=(',', ':')), 'finClientType': 'pc'}
                        raw, _ = await client.document('baidu', 'https://finance.pae.baidu.com/api/getrelatedblock?' + urlencode(query))
                        value = json.loads(raw)
                        return {code: [group for group in groups or [] if group.get('name') == '行业'] for code, groups in value.get('Result', {}).items()}
                    finally:
                        await client.close()
                print(json.dumps(asyncio.run(inspect_groups()), ensure_ascii=False, indent=2))
                return 0
            provider = BaiduConcepts(policy, settings.state_dir / 'source-cache', batch_limit=300, include_industry=True)
            def show_progress(message: str, progress: int, advanced: bool) -> None:
                if progress % 6 == 0:
                    print(json.dumps({'progress': progress, 'message': message}, ensure_ascii=False), flush=True)
            with progress_sink(show_progress, operation_id='validate-baidu-classifications-v2-' + datetime.now().strftime('%Y%m%d')):
                data = asyncio.run(provider.read(stocks))
            print(json.dumps({'industryCoverage': data.get('industryCoverage'), 'conceptCollection': data.get('conceptCollection'),
                'provenance': data.get('provenance'), 'samples': {key: data['stocks'].get(key) for key in
                    ['SSE.600000', 'SZSE.000001', 'SZSE.300750', 'SSE.688981']}}, ensure_ascii=False, indent=2))
        elif args.command == 'inspect-sync':
            from stocksieve.operations.sync_check import inspect_sync
            print(json.dumps(asyncio.run(inspect_sync(service.tasks, registry, args.job, probe=args.probe)), ensure_ascii=False, indent=2))
        elif args.command == 'check-sina':
            from stocksieve.operations.sina_check import check_sina
            if args.basics:
                from stocksieve.operations.sina_check import check_basics
                print(json.dumps(asyncio.run(check_basics(settings, policy)), ensure_ascii=False, indent=2))
            elif args.incremental:
                from stocksieve.operations.sina_incremental_check import (
                    check_incremental,
                )
                print(json.dumps(asyncio.run(check_incremental(database, settings, policy, args.date)), ensure_ascii=False, indent=2))
            else:
                print(json.dumps(asyncio.run(check_sina(settings, policy, args.date, args.directory)), ensure_ascii=False, indent=2))
        elif args.command == 'retire-source-jobs':
            from stocksieve.operations.source_switch import retire_source_jobs
            registry.get(settings.default_source, 'daily')
            print(json.dumps(retire_source_jobs(database, args.source, settings.default_source, apply=args.apply), ensure_ascii=False, indent=2))
        elif args.command == 'check-db':
            inspector = inspect(database.engine)
            for name, table in table_metadata.tables.items():
                if name not in inspector.get_table_names():
                    raise RuntimeError(f'数据库缺少表：{name}')
                actual = {column['name'] for column in inspector.get_columns(name)}
                if set(table.c.keys()) - actual:
                    raise RuntimeError(f'数据库表字段不匹配：{name}')
            print('数据库连接和17张业务表字段检查通过。')
            if args.sync_jobs:
                print(json.dumps(service.tasks.list('sync', {'kind': 'daily', 'pageSize': '5'}), ensure_ascii=False, indent=2))
            if args.constraints:
                print(json.dumps(inspector.get_check_constraints('tasks', schema='public'), ensure_ascii=False, indent=2))
            if args.counts:
                with database.transaction() as connection:
                    rows = {name: connection.scalar(text(f'SELECT count(*) FROM public.{name}')) for name in
                            ('users', 'stocks', 'tasks', 'data_snapshots', 'daily_bars', 'strategy_signals', 'analysis_results')}
                    rows['temporaryTestSchemas'] = connection.scalar(text("SELECT count(*) FROM pg_catalog.pg_namespace WHERE nspname ~ '^stocksieve_test_[a-f0-9]{16}$'"))
                print(json.dumps(rows, ensure_ascii=False, indent=2))
        elif args.command == 'sync-schema':
            from stocksieve.infrastructure.persistence.schema_sync import sync
            print(json.dumps(sync(database, apply=args.apply, details=args.details), ensure_ascii=False, indent=2))
        elif args.command == 'bootstrap-data':
            from stocksieve.operations.live_pipeline import bootstrap
            asyncio.run(bootstrap(args.base_url, args.credentials_file, args.state_file, args.source, args.date, args.retry_failed, args.skip_initialize, args.only_kind, args.submit_only, force=args.force))
        elif args.command == 'check-tencent-metadata':
            from stocksieve.modules.ingestion.infrastructure.providers.tencent import (
                TencentMetadata,
            )
            metadata = TencentMetadata(policy, settings.state_dir / 'source-cache')
            asyncio.run(metadata.fetch(args.symbol))
            print(json.dumps({symbol: metadata.values.get(symbol) for symbol in args.symbol}, ensure_ascii=False))
        elif args.command == 'reconcile-tencent':
            from stocksieve.modules.ingestion.infrastructure.repairs import (
                reconcile_tencent,
            )
            print(json.dumps(reconcile_tencent(policy), ensure_ascii=False))
        elif args.command == 'check-exchange-directory':
            async def check_exchange() -> None:
                from io import BytesIO

                import pandas as pd
                client = SourceHTTP(policy, settings.state_dir / 'source-cache')
                try:
                    url = 'https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1'
                    content, fetched = await client.document('szse', url)
                    frame = pd.read_excel(BytesIO(content), dtype=str)
                    rows = frame[frame['A股代码'].astype(str).str.startswith('302')].to_dict(orient='records')
                    print(json.dumps({'check': 'szse-official-directory', 'fetchedAt': fetched.isoformat(), 'rows': len(frame), 'fields': list(frame.columns), 'newCodeSamples': rows[:5]}, ensure_ascii=False))
                finally:
                    await client.close()
            asyncio.run(check_exchange())
        elif args.command == 'check-quote-metadata':
            async def check_metadata() -> None:
                client = SourceHTTP(policy, settings.state_dir / 'source-cache')
                try:
                    result = await client.get_json('eastmoney', 'https://push2.eastmoney.com/api/qt/clist/get',
                        {'pn': 1, 'pz': 5, 'po': 1, 'np': 1, 'ut': 'bd1d9ddb04089700cf9c27f6f7426281', 'fltt': 2, 'invt': 2,
                         'fid': 'f12', 'fs': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23', 'fields': 'f12,f13,f14,f2,f20,f100,f102,f103,f124'})
                    print(json.dumps(result, ensure_ascii=False))
                finally:
                    await client.close()
            asyncio.run(check_metadata())
        elif args.command == 'recover-history-bootstrap':
            print(json.dumps(service.tasks.recover_history_bootstrap(apply=args.apply), ensure_ascii=False))
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
                    await Worker(service.tasks, Sync(service.tasks, registry), Analysis(service.tasks, runtime, http)).serve()
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
    except SourceDeferred as waiting:
        print('遵守渠道访问限制，下次可执行时间：' + waiting.until.isoformat())
        return 3
    except RuntimeError as error:
        print(str(error))
        return 2
    finally:
        if database:
            database.close()
    return 0
