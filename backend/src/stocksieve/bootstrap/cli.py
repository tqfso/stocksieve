"""本文件提供 API、worker、scheduler、数据库检查及管理员初始化命令。"""

import argparse
import asyncio
import getpass
import json
import os
import secrets
import time
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
    check = sub.add_parser('check-db')
    check.add_argument('--counts', action='store_true', help='只读显示public关键业务表行数与剩余临时测试schema数量')
    check.add_argument('--constraints', action='store_true', help='只读显示真实任务约束，核对现有数据库与初始化定义是否一致')
    schema = sub.add_parser('sync-schema', help='审计或直接对齐现有库与当前初始化SQL的约束，不重建业务表')
    schema.add_argument('--apply', action='store_true')
    schema.add_argument('--details', action='store_true')
    recovery = sub.add_parser('recover-history-bootstrap', help='将旧自然年配置的首次排队任务恢复为近期发布、历史回补')
    recovery.add_argument('--apply', action='store_true')
    source_check = sub.add_parser('check-source', help='通过正式限频账本验证免费批量渠道，不写业务表')
    source_check.add_argument('--date', required=True, type=date.fromisoformat)
    source_check.add_argument('--adjustment', action='store_true', help='交叉核对四板块除权参考价推导的相对因子与渠道前复权行情')
    sub.add_parser('check-quote-metadata', help='只读检查免费批量市值、行业等补充字段')
    sub.add_parser('check-exchange-directory', help='只读核对深交所官方目录和新增代码板块')
    sub.add_parser('reconcile-tencent', help='核验证据后纠正已确认的本地格式误判冷却，保留真实限流和全部次数')
    metadata_check = sub.add_parser('check-tencent-metadata', help='核验指定真实证券的板块标签和总市值')
    metadata_check.add_argument('--symbol', action='append', required=True)
    concepts_check = sub.add_parser('check-concepts', help='只读核验免费概念目录，不逐股票抓取')
    concepts_check.add_argument('--source', choices=('baostock', 'tdx'), default='baostock')
    slow = sub.add_parser('reconcile-slow-response', help='核验旧版接收期限误判，恢复对应已排队任务，不清空额度')
    slow.add_argument('--job', required=True)
    slow.add_argument('--partial-retry-policy', action='store_true', help='仅对正常成功流中断采用有界短退避，真实拒绝访问冷却保持')
    pipeline = sub.add_parser('bootstrap-data', help='通过真实HTTP完成初始化、分类和收盘发布验收，可跨中断恢复')
    pipeline.add_argument('--credentials-file', required=True, type=Path)
    pipeline.add_argument('--state-file', type=Path, default=Path('backend/.state/live-pipeline.json'))
    pipeline.add_argument('--source', default='baostock')
    pipeline.add_argument('--date', type=date.fromisoformat)
    pipeline.add_argument('--retry-failed', action='store_true')
    pipeline.add_argument('--submit-only', action='store_true', help='配合--only-kind，仅提交并保存后台任务，不等待多年历史完成')
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
    wire = None
    try:
        settings = Settings.from_env()
        registry = Registry(settings.market_boards)
        policy = SourcePolicy(settings.rate_dir, settings.request_interval, settings.source_daily_limit)
        if 'baidu_concepts' in settings.enabled_sources:
            from stocksieve.modules.ingestion.infrastructure.providers.baidu import (
                BaiduCatalogSource,
                BaiduConcepts,
            )
            registry.register(BaiduCatalogSource(BaiduConcepts(policy, settings.state_dir / 'source-cache')))
        if 'baostock' in settings.enabled_sources:
            from stocksieve.modules.ingestion.infrastructure.baostock_wire import (
                BaostockWire,
            )
            from stocksieve.modules.ingestion.infrastructure.providers.baostock import (
                BaostockSource,
            )
            from stocksieve.modules.ingestion.infrastructure.providers.tencent import (
                TencentMetadata,
            )
            wire = BaostockWire(policy, settings.state_dir / 'baostock-wire')
            base_source = BaostockSource(wire, settings.state_dir / 'baostock-market.sqlite',
                history_days=settings.history_trading_days, history_max_days=settings.history_max_trading_days,
                metadata=TencentMetadata(policy, settings.state_dir / 'source-cache'))
            registry.register(base_source)
        if set(settings.enabled_sources) - registry.sources.keys():
            raise RuntimeError('请求启用的渠道尚无通过覆盖验证的适配器；不能仅靠配置将候选源标为可用。')
        database = Database(settings.database_url)
        runtime = create_runtime(settings)
        service = API(database, settings, registry, runtime)
        if args.command == 'check-config':
            print(json.dumps({'historyTradingDays': settings.history_trading_days, 'historyMaxTradingDays': settings.history_max_trading_days,
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
            asyncio.run(bootstrap(args.base_url, args.credentials_file, args.state_file, args.source, args.date, args.retry_failed, args.skip_initialize, args.only_kind, args.submit_only))
        elif args.command == 'reconcile-slow-response':
            from datetime import datetime, timezone

            from sqlalchemy import select, update

            import stocksieve.infrastructure.persistence.schema as tables
            from stocksieve.modules.ingestion.infrastructure.repairs import (
                reconcile_short_receive_deadline,
            )
            from stocksieve.shared.query import resource_id
            with database.transaction(serialized=True) as connection:
                row = connection.execute(select(tables.tasks).where(tables.tasks.c.id == resource_id(args.job, 'SYNC_JOB_NOT_FOUND'), tables.tasks.c.category == 'sync').with_for_update()).mappings().one()
                if row['status'] != 'queued' or row['data_source'] != 'baostock' or row['lease_owner'] is not None or row['options_json'].get('sourceWaitReason') != 'SOURCE_ACCESS_WAIT':
                    raise RuntimeError('任务不是无租约的渠道等待状态，未修改任务。')
                if args.partial_retry_policy:
                    from stocksieve.modules.ingestion.infrastructure.repairs import (
                        reconcile_partial_stream_backoff,
                    )
                    result = reconcile_partial_stream_backoff(policy, settings.state_dir / 'baostock-wire')
                else:
                    result = reconcile_short_receive_deadline(policy, settings.state_dir / 'baostock-wire')
                connection.execute(update(tables.tasks).where(tables.tasks.c.id == row['id']).values(run_after=datetime.now(timezone.utc)))
                connection.execute(tables.logs.insert().values(task_id=row['id'], level='info', message=result['reason']))
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == 'check-concepts':
            if args.source == 'baostock':
                from baostock.security.sectorinfo import query_stock_concept

                from stocksieve.modules.ingestion.infrastructure.baostock_wire import (
                    BaostockWire,
                )
                client = wire or BaostockWire(policy, settings.state_dir / 'baostock-wire')
                deadline = time.monotonic() + 300
                try:
                    while True:
                        try:
                            rows = client.rows(query_stock_concept())
                            print(json.dumps({'source': args.source, 'rows': len(rows), 'fields': list(rows[0]) if rows else [], 'sample': rows[:2]}, ensure_ascii=False))
                            break
                        except SourceDeferred as waiting:
                            delay = waiting.until.timestamp() - time.time()
                            if delay > 30 or time.monotonic() + delay > deadline:
                                raise RuntimeError('概念验证等待渠道可执行时间：' + waiting.until.isoformat()) from None
                            time.sleep(max(.1, delay))
                finally:
                    client.close()
            else:
                from stocksieve.modules.ingestion.infrastructure.providers.tdx import (
                    TdxConcepts,
                )
                mapping = asyncio.run(TdxConcepts(policy, settings.state_dir / 'source-cache').read())
                print(json.dumps({'source': args.source, 'mappedSecurities': len(mapping), 'concepts': len({row['id'] for values in mapping.values() for row in values}), 'sample': list(mapping.items())[:2]}, ensure_ascii=False))
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
        elif args.command == 'check-source':
            from stocksieve.modules.ingestion.infrastructure.baostock_wire import (
                BaostockWire,
            )
            client = wire or BaostockWire(policy, settings.state_dir / 'baostock-wire')
            operations = [('daily', lambda: client.sdk.query_daily_history_k_AStock(str(args.date))),
                          ('factors', lambda: client.sdk.query_daily_adjust_factor(str(args.date))),
                          ('basic', lambda: client.sdk.query_stock_basic())]
            if args.adjustment:
                from datetime import timedelta
                operations = []
                for code in ('sh.600000', 'sz.000001', 'sz.300750', 'sh.688981'):
                    for flag in ('3', '2'):
                        operations.append((code + ':' + flag, lambda code=code, flag=flag: client.sdk.query_history_k_data_plus(code, 'date,code,open,high,low,close,preclose', str(args.date - timedelta(days=400)), str(args.date), adjustflag=flag)))
            checked: dict[str, Any] = {}
            deadline = time.monotonic() + 300
            try:
                for name, query in operations:
                    while True:
                        try:
                            rows = client.rows(query())
                            checked[name] = rows
                            print(json.dumps({'check': name, 'count': len(rows), 'fields': list(rows[0]) if rows else [], 'sample': rows[:1]}, ensure_ascii=False), flush=True)
                            break
                        except SourceDeferred as waiting:
                            delay = waiting.until.timestamp() - time.time()
                            if delay > 30 or time.monotonic() + delay > deadline:
                                raise RuntimeError('验证等待渠道可执行时间：' + waiting.until.isoformat()) from None
                            time.sleep(max(.1, delay))
                if args.adjustment:
                    from decimal import Decimal as D
                    for code in ('sh.600000', 'sz.000001', 'sz.300750', 'sh.688981'):
                        raw, adjusted = checked[code + ':3'], {row['date']: row for row in checked[code + ':2']}
                        factor = D(1)
                        prior = None
                        values = []
                        for row in raw:
                            if prior is not None and D(row['preclose']) > 0:
                                factor *= prior / D(row['preclose'])
                            values.append((row, factor))
                            prior = D(row['close'])
                        anchor = values[-1][1]
                        qscale = D(raw[-1]['close']) / D(adjusted[raw[-1]['date']]['close'])
                        error = max(abs(D(row[field]) * value / anchor - D(adjusted[row['date']][field]) * qscale) for row, value in values for field in ('open', 'high', 'low', 'close'))
                        print(json.dumps({'check': 'adjustment-match', 'code': code, 'rows': len(values), 'maximumPriceDifference': str(error)}, ensure_ascii=False), flush=True)
                        if error > D('.02'):
                            raise RuntimeError('复权推导未通过交叉核验，不能启用。')
                else:
                    from stocksieve.modules.ingestion.infrastructure.providers.baostock import (
                        BaostockSource,
                    )
                    from stocksieve.modules.ingestion.infrastructure.providers.tencent import (
                        TencentMetadata,
                    )
                    source = BaostockSource(client, settings.state_dir / 'baostock-market.sqlite', metadata=TencentMetadata(policy, settings.state_dir / 'source-cache'))
                    stocks = source.stocks(args.date)
                    quotes = {row['code'] for row in checked['daily']}
                    missing = [stock.stock_id for stock in stocks if source.vendor_code(stock) not in quotes]
                    print(json.dumps({'check': 'four-board-coverage', 'stocks': len(stocks),
                        'byBoard': {board: sum(stock.board == board for stock in stocks) for board in sorted(source.boards)}, 'missingDaily': len(missing), 'missingSamples': missing[:5]}, ensure_ascii=False), flush=True)
                    if missing:
                        raise RuntimeError('目录与批量日线覆盖不一致，不能启用。')
            finally:
                client.close()
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
        if wire:
            wire.close()
        if database:
            database.close()
    return 0
