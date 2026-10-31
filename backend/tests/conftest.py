"""本文件提供独立测试库、冻结时钟和显式注入的行情渠道，不连接生产或真实供应商。"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from stocksieve.api import API
from stocksieve.config import Settings
from stocksieve.database import Database
from stocksieve.domain import BOARDS, Bar, Stock, StockState
from stocksieve.sources import CalendarDay, DailyPacket, Initialization, Registry
from stocksieve.sync import Sync
from stocksieve.worker import Worker


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FixtureSource:
    id = 'fixture'
    name = '离线测试渠道'
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


@dataclass
class Context:
    api: API
    source: FixtureSource
    clock: Clock
    worker: Worker
    token: str = field(repr=False)

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
            request = make_mocked_request(method, path, headers=supplied, app=self.api.app)
            request._read_bytes = encoded or b''
            response = await self.api.app._handle(request)
            return response.status, json.loads(response.text) if response.text else None, response.headers
        return asyncio.run(run())

    def submit(self, kind: str, *, day: str | None = None) -> str:
        payload = {'kind': kind, 'source': 'fixture'}
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


@pytest.fixture
def context(tmp_path: Any) -> Any:
    database = Database('sqlite+pysqlite:///:memory:', testing=True)
    database.create_test_schema()
    clock = Clock()
    source = FixtureSource()
    registry = Registry(); registry.register(source)
    settings = Settings(database_url='sqlite+pysqlite:///:memory:', state_dir=tmp_path, testing=True, default_source='fixture')
    api = API(database, settings, registry, FixtureRuntime(), clock.now)
    api.auth.create_user('admin', '测试管理员', 'testpass123', admin=True)
    token = api.auth.login('admin', 'testpass123')['accessToken']
    api.app.freeze()
    yield Context(api, source, clock, Worker(api.tasks, Sync(api.tasks, registry)), token)
    database.close()
