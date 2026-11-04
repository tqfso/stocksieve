"""本文件在真实 PostgreSQL 隔离 schema 中准备数据，通过 TCP/HTTP 验证接口。"""

import asyncio
import json
import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestServer
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from stocksieve.infrastructure.persistence.database import Database
from stocksieve.interfaces.http.api import API
from stocksieve.modules.ingestion.application.ports import (
    CalendarDay,
    DailyPacket,
    Initialization,
)
from stocksieve.modules.ingestion.application.registry import Registry
from stocksieve.modules.ingestion.application.service import Sync
from stocksieve.modules.jobs.application.worker import Worker
from stocksieve.modules.market.domain.models import BOARDS, Bar, Stock, StockState
from stocksieve.shared.paths import BACKEND_ROOT
from stocksieve.shared.settings import Settings


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FixtureSource:
    id = 'fixture'
    name = '接口测试渠道'
    supported_kinds = frozenset({'initialize', 'catalog', 'daily'})
    boards = frozenset(BOARDS)

    def __init__(self) -> None:
        self.pending = False
        self.bad = False
        self.catalog_calls = 0
        self.catalog_name = '测试行业'
        self.universe_stocks = tuple(Stock(f'{exchange}.{600000 + index:06}', f'{600000 + index:06}', f'测试证券{index}', exchange, board)
                                     for index, (board, (_, exchange)) in enumerate(BOARDS.items()))

    async def initialize(self) -> Initialization:
        start, end = date(2025, 1, 1), date(2027, 12, 31)
        days = {}
        while start <= end:
            opened = start.weekday() < 5
            days[start] = CalendarDay(opened, datetime.combine(start, datetime.min.time(), timezone.utc) + timedelta(hours=7) if opened else None)
            start += timedelta(days=1)
        return Initialization(self.universe_stocks, {exchange: days for exchange in ('SSE', 'SZSE', 'BSE')})

    async def catalog(self, stocks: tuple[Stock, ...]) -> dict[str, Any]:
        self.catalog_calls += 1
        return {'stocks': {stock.stock_id: {'industry': {'id': 'industry_test', 'name': self.catalog_name},
                                         'concepts': [{'id': 'concept_test', 'name': '测试题材'}]} for stock in stocks}}

    async def precheck(self, target: date) -> list[dict[str, Any]]:
        return [{'board': board, 'status': 'pending' if self.pending and board == 'BSE' else 'ready',
                 'latestDataDate': target.isoformat(), 'message': None} for board in BOARDS]

    async def universe(self, target: date, known: tuple[Stock, ...]) -> tuple[Stock, ...]:
        return self.universe_stocks

    async def daily(self, stock: Stock, target: date, history: tuple[Bar, ...]) -> DailyPacket:
        days = []
        cursor = target
        while len(days) < 160:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor -= timedelta(days=1)
        days.reverse()
        index = self.universe_stocks.index(stock)
        result = []
        for offset, day in enumerate(days):
            price = D(10) + D(offset) / 50
            if day == target:
                price += D('.2')
            amount = D(50_000_000) * (D(2) + D(index) / 10) if day == target else D(50_000_000)
            result.append(Bar(day, price, price + D('.02'), price - D('.02'), price,
                              None if self.bad and day == target else amount, 1000000, D(2),
                              None if index == 1 else D(10_000_000_000) + D(index) * 1_000_000, D(1)))
        return DailyPacket(stock, StockState('trading', False, False), tuple(result))


class FixtureRuntime:
    def identity(self) -> dict[str, Any]:
        return {'provider': 'fixture', 'model': 'fixture', 'protocol': 'chat_completions'}


class HTTPServer:
    """在独立线程监听本机随机端口，所有接口请求仍进入实际应用及数据库。"""

    def __init__(self, app: Any) -> None:
        self.loop = asyncio.new_event_loop()
        self.server = TestServer(app, host='127.0.0.1', port=0)
        ready: queue.Queue[Any] = queue.Queue()
        def serve() -> None:
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self.server.start_server())
                ready.put(str(self.server.make_url('')).rstrip('/'))
                self.loop.run_forever()
            except BaseException as error:
                ready.put(error)
            finally:
                self.loop.close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        result = ready.get(timeout=15)
        if isinstance(result, BaseException):
            raise RuntimeError('HTTP测试端口启动失败。') from None
        self.base_url = result

    def close(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self.server.close(), self.loop)
        future.result(timeout=15)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=15)


@dataclass
class Context:
    api: API
    source: FixtureSource
    clock: Clock
    worker: Worker
    token: str = field(repr=False)
    server: HTTPServer | None = field(default=None, repr=False)

    def request(self, method: str, path: str, payload: Any = None, *, token: str | None = '', key: str | None = None,
                headers: dict[str, str] | None = None, raw: bytes | None = None) -> tuple[int, Any, Any]:
        async def run() -> tuple[int, Any, Any]:
            supplied = {'X-Request-ID': 'test-request', **(headers or {})}
            selected = self.token if token == '' else token
            if selected:
                supplied['Authorization'] = 'Bearer ' + selected
            if key:
                supplied['Idempotency-Key'] = key
            encoded = json.dumps(payload).encode() if payload is not None else raw
            if encoded is not None:
                supplied.setdefault('Content-Type', 'application/json')
                supplied['Content-Length'] = str(len(encoded))
            assert self.server is not None
            url = self.server.base_url + quote(path, safe='/:?&=%+@,')
            http_request = Request(url, data=encoded, headers=supplied, method=method)
            try:
                response = build_opener(ProxyHandler({})).open(http_request, timeout=15)
            except HTTPError as error:
                response = error
            with response:
                value = response.read()
                return response.status, (json.loads(value) if 'application/json' in response.headers.get('Content-Type', '') else value) if value else None, response.headers
        return asyncio.run(run())

    def submit(self, kind: str, *, day: str | None = None, force: bool = True) -> str:
        payload = {'kind': kind, 'source': 'fixture'}
        # 场景测试显式要求重新执行；普通接口提交的默认行为另行验证复用。
        if kind == 'daily' and force:
            payload['force'] = True
        if day is not None:
            payload['dataDate'] = day
        from uuid import uuid4
        status, response, _ = self.request('POST', '/api/v1/admin/sync-jobs', payload, key=str(uuid4()))
        assert status == 202, response
        return response['data']['id']

    def run(self) -> bool:
        return asyncio.run(self.worker.run_once('sync'))

    def published(self) -> str:
        self.submit('initialize'); self.run()
        self.submit('catalog'); self.run()
        task = self.submit('daily'); self.run()
        result = self.api.tasks.detail(task, 'sync')
        assert result['status'] == 'succeeded', result
        return result['result']['publishedSnapshotId']


def pytest_addoption(parser: Any) -> None:
    parser.addoption('--live-model', action='store_true', help='显式使用私有配置调用真实模型与公开公告，产生模型用量；测试业务数据仍在隔离schema。')
    parser.addoption('--postgres-url-env', default=None, help='数据库URL环境变量名；省略则读取项目数据库配置')
    parser.addoption('--browser', action='store_true', help='使用本机浏览器验证隔离数据的前后端完整流程，需已构建前端')
    parser.addoption('--deployed-base', default=None, help='正式部署的浏览器验收地址')
    parser.addoption('--deployed-credentials', default=None, help='正式管理员私有凭据JSON文件路径，不在终端显示内容')
    parser.addoption('--deployed-data', action='store_true', help='验收正式图表、筹码及报告；按服务模式调用模型并保存结果')
    parser.addoption('--deployed-concepts', action='store_true', help='显式验收正式题材筛选，不生成分析报告')
    parser.addoption('--deployed-strategies', action='store_true', help='只读验收正式五类策略、空结果与筛选重置')
    parser.addoption('--deployed-research', action='store_true', help='验收正式任意股票搜索和报告；按服务模式调用模型并保存结果')
    parser.addoption('--deployed-stock', default=None, help='报告专项指定已同步股票ID；省略则选择未入选股票')
    parser.addoption('--deployed-readiness', action='store_true', help='只读验收正式收盘数据未就绪或渠道等待提示')
    parser.addoption('--deployed-retry-job', default=None, help='显式通过页面继续指定失败同步任务，沿用原检查点')


def pytest_report_header(config: Any) -> str:
    return 'StockSieve：真实 PostgreSQL 隔离 schema / TCP HTTP；供应商使用受控输入，不访问外网。'


@pytest.fixture
def context(tmp_path: Any, request: Any) -> Any:
    __tracebackhide__ = True
    variable = request.config.getoption('--postgres-url-env')
    url = os.environ.get(variable) if variable else Settings.from_env().database_url
    if not url or make_url(url).get_backend_name() != 'postgresql':
        pytest.fail('接口测试需要有效的 PostgreSQL 配置。', pytrace=False)
    schema = 'stocksieve_test_' + uuid4().hex[:16]
    schema_created = False
    database = None
    server = None
    try:
        try:
            database = Database(make_url(url))
            # schema 名只来自 UUID，数据库连接始终绑定该 schema，禁止写入 public。
            @event.listens_for(database.engine, 'connect')
            def select_schema(connection: Any, record: Any) -> None:
                with connection.cursor() as cursor:
                    cursor.execute(f'SET search_path TO "{schema}"')
                    cursor.execute('SET statement_timeout = 10000')
                    cursor.execute('SET lock_timeout = 5000')
                connection.commit()
            script = (BACKEND_ROOT / 'sql/init.sql').read_text().replace('public.', f'"{schema}".').replace("namespace.nspname = 'public'", f"namespace.nspname = '{schema}'")
            with database.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                schema_created = True
                assert connection.exec_driver_sql('SELECT current_schema()').scalar() == schema
                with connection.connection.driver_connection.cursor() as cursor:
                    cursor.execute(script, prepare=False)
        except ModuleNotFoundError as error:
            pytest.fail(f'数据库自测未执行：缺少运行依赖 {error.name}。', pytrace=False)
        except SQLAlchemyError:
            pytest.fail('数据库自测未执行：连接或隔离结构初始化失败，请检查驱动、权限和服务状态。', pytrace=False)
        clock = Clock()
        source = FixtureSource()
        registry = Registry(); registry.register(source)
        settings = Settings(database_url=url, state_dir=tmp_path, default_source='fixture')
        api = API(database, settings, registry, FixtureRuntime(), clock.now)
        api.auth.create_user('admin', '测试管理员', 'testpass123', admin=True)
        token = api.auth.login('admin', 'testpass123')['accessToken']
        api.app.freeze()
        server = HTTPServer(api.app)
        yield Context(api, source, clock, Worker(api.tasks, Sync(api.tasks, registry)), token, server)
    finally:
        try:
            if server:
                server.close()
        finally:
            if database:
                try:
                    if schema_created:
                        with database.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
                finally:
                    database.close()
