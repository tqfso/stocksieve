"""本文件提供 /api/v1 接口，控制层只校验权限和参数并调用业务服务。"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from aiohttp import web
from sqlalchemy import func, select, text

import stocksieve.infrastructure.persistence.schema as t
from stocksieve.infrastructure.persistence.database import Database, utc
from stocksieve.interfaces.http.middleware import USER, body, envelope, success
from stocksieve.interfaces.http.query import known_query
from stocksieve.modules.identity.application.commands import (
    LoginInput,
    PasswordInput,
    UserInput,
)
from stocksieve.modules.identity.application.service import Auth
from stocksieve.modules.ingestion.application.registry import Registry
from stocksieve.modules.jobs.application.commands import (
    AnalysisInput,
    SyncInput,
    SyncRetryInput,
)
from stocksieve.modules.jobs.application.service import Tasks
from stocksieve.modules.market.application.queries import Market, strategy_catalog
from stocksieve.modules.market.domain.models import BOARDS
from stocksieve.shared.errors import DomainError
from stocksieve.shared.query import integer, invalid
from stocksieve.shared.settings import Settings


class Runtime(Protocol):
    def identity(self) -> dict[str, Any] | None: ...


class API:
    """依赖在应用构造时注入，离线接口测试不需要监听端口或访问真实数据源。"""

    def __init__(self, database: Database, settings: Settings, registry: Registry | None = None,
                 runtime: Runtime | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self.db, self.settings = database, settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.registry = registry or Registry(settings.market_boards)
        self.runtime = runtime
        self.auth = Auth(database, self.clock)
        self.market = Market(database)
        self.tasks = Tasks(database, settings, self.registry, self.market, self.clock)
        self.app = web.Application(middlewares=[envelope, self.authenticate], client_max_size=64 * 1024)
        self.app.add_routes([
            web.get('/health/live', self.live), web.get('/health/ready', self.ready),
            web.post('/api/v1/auth/login', self.login), web.get('/api/v1/auth/me', self.me),
            web.post('/api/v1/auth/logout', self.logout), web.put('/api/v1/auth/password', self.password),
            web.get('/api/v1/admin/users', self.users), web.post('/api/v1/admin/users', self.create_user),
            web.get('/api/v1/market/status', self.status), web.get('/api/v1/catalog/filters', self.filters),
            web.get('/api/v1/strategies', self.strategies), web.get('/api/v1/opportunities', self.opportunities),
            web.get('/api/v1/stocks', self.search_stocks),
            web.get('/api/v1/stocks/{stockId}', self.stock), web.get('/api/v1/stocks/{stockId}/candles', self.candles),
            web.post('/api/v1/admin/sync-jobs', self.submit_sync), web.get('/api/v1/admin/sync-jobs', self.sync_list),
            web.get('/api/v1/admin/sync-jobs/{jobId}', self.sync_detail), web.get('/api/v1/admin/sync-jobs/{jobId}/logs', self.sync_logs),
            web.post('/api/v1/admin/sync-jobs/{jobId}/retry', self.retry_sync),
            web.post('/api/v1/stocks/{stockId}/analyses', self.submit_analysis),
            web.get('/api/v1/analyses', self.analysis_list), web.get('/api/v1/analyses/{analysisId}', self.analysis_detail),
        ])
        # 仅提供编译后的前端目录；不把项目源码、配置或状态目录映射到HTTP。
        if (settings.web_dir / 'index.html').is_file():
            self.app.router.add_get('/', self.index)
            self.app.router.add_static('/', settings.web_dir, follow_symlinks=False)

    async def index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.settings.web_dir / 'index.html')

    @web.middleware
    async def authenticate(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.path.startswith('/api/v1/') and request.path != '/api/v1/auth/login':
            request[USER] = self.auth.authenticate(request.headers.get('Authorization'))
            if request.path.startswith('/api/v1/admin/') and request[USER]['role'] != 'admin':
                raise DomainError('FORBIDDEN', '仅管理员可执行此操作。', 403)
        return await handler(request)

    async def live(self, request: web.Request) -> web.Response:
        return success(request, {'status': 'alive'})

    async def ready(self, request: web.Request) -> web.Response:
        try:
            with self.db.transaction() as connection:
                connection.execute(text('SELECT 1'))
        except Exception as error:
            raise DomainError('SERVICE_UNAVAILABLE', '数据库尚未就绪。', 503) from error
        return success(request, {'status': 'ready'})

    async def login(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        data = await body(request, LoginInput)
        return success(request, self.auth.login(data.username, data.password))

    async def me(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        return success(request, {key: value for key, value in request[USER].items() if key != 'sessionId'})

    async def logout(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        if request.can_read_body or request.content_length not in {None, 0}:
            raise invalid('body', '退出接口不接受请求体。')
        self.auth.logout(request[USER])
        return success(request, status=204)

    async def password(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        if request[USER]['role'] != 'admin':
            raise DomainError('FORBIDDEN', '仅管理员可修改自身密码。', 403)
        data = await body(request, PasswordInput)
        self.auth.change_password(request[USER], data.oldPassword, data.newPassword)
        return success(request, status=204)

    async def users(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'search', 'role', 'page', 'pageSize'})
        if len(query.get('search', '').strip()) > 50 or ('role' in query and query['role'] not in {'admin', 'user'}):
            raise invalid('search' if len(query.get('search', '').strip()) > 50 else 'role')
        result = self.auth.list_users(query.get('search', '').strip(), query.get('role'), integer(query, 'page', 1), integer(query, 'pageSize', 20, maximum=100))
        return success(request, result)

    async def create_user(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        data = await body(request, UserInput)
        return success(request, self.auth.create_user(data.username, data.name, data.password), status=201)

    async def status(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        with self.db.transaction() as connection:
            times = {}
            for kind in ('initialize', 'catalog'):
                stamp = connection.scalar(select(func.max(t.tasks.c.finished_at)).where(t.tasks.c.category == 'sync', t.tasks.c.kind == kind, t.tasks.c.status == 'succeeded'))
                times[kind] = utc(stamp).isoformat() if stamp else None
            initialized = self.tasks.initialized(connection)
            counts = dict(connection.execute(select(t.stocks.c.exchange, func.count()).where(t.stocks.c.board.in_(self.settings.market_boards)).group_by(t.stocks.c.exchange)).all()) if initialized else {}
            snapshot = connection.execute(select(t.snapshots).where(t.snapshots.c.status == 'published').order_by(t.snapshots.c.data_date.desc(), t.snapshots.c.published_at.desc(), t.snapshots.c.version.desc()).limit(1)).mappings().first()
            # 仅依据当前覆盖范围所需交易所的完整日历确定最近交易日。
            exchanges = {BOARDS[board][1] for board in self.settings.market_boards}
            calendar_rows = connection.execute(select(t.calendar).where(t.calendar.c.exchange.in_(exchanges), t.calendar.c.is_open.is_(True), t.calendar.c.close_at <= self.clock()).order_by(t.calendar.c.trade_date.desc())).mappings().all()
            latest = None
            for day in sorted({row['trade_date'] for row in calendar_rows}, reverse=True):
                all_rows = connection.execute(select(t.calendar).where(t.calendar.c.trade_date == day, t.calendar.c.exchange.in_(exchanges))).mappings().all()
                if {row['exchange'] for row in all_rows} == exchanges and all(row['is_open'] is False or (row['is_open'] is True and row['close_at'] is not None and utc(row['close_at']) <= self.clock()) for row in all_rows):
                    latest = day.isoformat()
                    break
        return success(request, {'initialized': initialized, 'stockUpdatedAt': times['initialize'], 'catalogUpdatedAt': times['catalog'], 'marketBoards': list(self.settings.market_boards),
            'latestTradingDate': latest, 'defaultSource': self.settings.default_source, 'publishedSnapshot': self.market.snapshot_view(snapshot) if snapshot else None,
            'coverage': {'totalStocks': sum(counts.values()), 'byExchange': {key: counts.get(key, 0) for key in ('SSE', 'SZSE', 'BSE')}}, 'sources': self.registry.public()})

    async def filters(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'snapshotId'})
        return success(request, self.market.filters(query.get('snapshotId')))

    async def strategies(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        return success(request, strategy_catalog())

    async def opportunities(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'strategy', 'snapshotId', 'search', 'industryId', 'conceptId', 'board', 'minTurnover', 'maxTurnover', 'minMarketCap', 'maxMarketCap', 'sort', 'order', 'page', 'pageSize'})
        return success(request, self.market.opportunities(query))

    async def stock(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'snapshotId'})
        return success(request, self.market.stock(request.match_info['stockId'], query.get('snapshotId')))

    async def search_stocks(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'search', 'page', 'pageSize'})
        return success(request, self.market.search_stocks(query, self.settings.market_boards))

    async def candles(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'snapshotId', 'period', 'before', 'limit'})
        return success(request, self.market.candle_page(request.match_info['stockId'], query))

    async def submit_sync(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        data = await body(request, SyncInput)
        result, headers = self.tasks.submit_sync(request[USER], request.path, request.headers.get('Idempotency-Key'), data)
        return success(request, result, status=202, headers=headers)

    async def retry_sync(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        await body(request, SyncRetryInput)
        result, headers = self.tasks.retry_sync(request[USER], request.match_info['jobId'], request.path, request.headers.get('Idempotency-Key'))
        return success(request, result, status=202, headers=headers)

    async def sync_list(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'kind', 'status', 'trigger', 'page', 'pageSize'})
        return success(request, self.tasks.list('sync', query))

    async def sync_detail(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        return success(request, self.tasks.detail(request.match_info['jobId'], 'sync'))

    async def sync_logs(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'after', 'limit'})
        return success(request, self.tasks.log_page(request.match_info['jobId'], query))

    async def submit_analysis(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        data = await body(request, AnalysisInput)
        identity = self.runtime.identity if self.runtime else None
        result, headers = self.tasks.submit_analysis(request[USER], request.match_info['stockId'], request.path,
                                                     request.headers.get('Idempotency-Key'), data, identity)
        return success(request, result, status=202, headers=headers)

    async def analysis_list(self, request: web.Request) -> web.Response:
        query = known_query(request.query, {'stockId', 'kind', 'strategy', 'snapshotId', 'status', 'page', 'pageSize'})
        return success(request, self.tasks.list('analysis', query, request[USER]['id']))

    async def analysis_detail(self, request: web.Request) -> web.Response:
        known_query(request.query, set())
        return success(request, self.tasks.detail(request.match_info['analysisId'], 'analysis', request[USER]['id']))
