"""本文件验证环境配置、任务冻结、真实日历选择及历史上限不改写旧快照。"""

import asyncio
from dataclasses import replace
from copy import copy
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from datetime import datetime

import pytest
from stocksieve.modules.ingestion.infrastructure.providers.baostock import (
    BaostockSource,
)
from stocksieve.modules.market.domain.history import HistoryPolicy
from stocksieve.shared.errors import DomainError, MissingData
from stocksieve.shared.settings import Settings
from stocksieve.modules.ingestion.application.ports import CalendarDay, SourceDeferred
from stocksieve.modules.ingestion.application.progress import report_progress
from stocksieve.shared.time import SHANGHAI
from conftest import FixtureSource
from sqlalchemy import select, update
from stocksieve.infrastructure.persistence import schema as t


class WindowSource(FixtureSource):
    """近期窗口正常完成，较长窗口可暂停，用于验证后台回补不会撤销已发布策略。"""

    def __init__(self, now: Any) -> None:
        super().__init__()
        self.policy = HistoryPolicy()
        self.now = now
        self.paused = True
        self.on_prepare = None

    def for_history(self, policy: HistoryPolicy) -> Any:
        result = copy(self)
        result.policy = policy
        return result

    async def history_calendar(self, target: date) -> Any:
        data = await self.initialize()
        for days in data.calendar.values():
            days[self.policy.calendar_start(target)] = CalendarDay(False)
        return data.calendar, self.now()

    async def universe(self, target: date, known: Any) -> Any:
        if self.policy.initial_days > 150 and self.on_prepare:
            self.on_prepare()
            report_progress('已保存一批真实历史，检查日常任务优先级。', 4, True)
        if self.policy.initial_days > 150 and self.paused:
            raise SourceDeferred(self.now() + timedelta(days=1), '后台历史暂时等待')
        return self.universe_stocks

    async def daily(self, stock: Any, target: date, history: Any) -> Any:
        packet = await super().daily(stock, target, history)
        return replace(packet, bars=packet.bars[-self.policy.initial_days:])


def test_history_env_and_invalid_boundaries(monkeypatch: Any) -> None:
    monkeypatch.setenv('STOCKSIEVE_DATABASE_URL', 'postgresql+psycopg://unused/unused')
    monkeypatch.setenv('STOCKSIEVE_HISTORY_TRADING_DAYS', '250')
    monkeypatch.setenv('STOCKSIEVE_HISTORY_MAX_TRADING_DAYS', '500')
    settings = Settings.from_env()
    assert (settings.history_trading_days, settings.history_max_trading_days) == (250, 500)
    for initial, maximum in ((139, 0), (150, 140), (150, -1), (True, 0)):
        with pytest.raises(ValueError):
            replace(settings, history_trading_days=initial, history_max_trading_days=maximum)
        with pytest.raises(ValueError):
            HistoryPolicy(initial, maximum)
    assert HistoryPolicy().max_days == 0


def test_large_maximum_does_not_block_recent_strategy_window() -> None:
    target = date(2026, 10, 30)
    days = [target - timedelta(days=index * 2) for index in range(3000)]
    policy = HistoryPolicy(150, 2500)
    assert policy.initial_dates(days, target) == sorted(days)[-150:]
    assert policy.cutoff(days, target) == sorted(days)[-2500]
    assert policy.calendar_start(target) == HistoryPolicy(150, 0).calendar_start(target)


def test_window_uses_confirmed_sessions_and_expands_calendar() -> None:
    target = date(2026, 10, 30)
    days = [target - timedelta(days=index * 2) for index in range(300)]
    policy = HistoryPolicy(150, 200)
    assert policy.initial_dates(days + [target + timedelta(days=1)], target) == sorted(days)[-150:]
    assert policy.cutoff(days, target) == sorted(days)[-200]
    assert HistoryPolicy(150, 0).cutoff(days, target) is None
    with pytest.raises(MissingData):
        policy.initial_dates(days[:140], target)
    assert HistoryPolicy(500, 1000).calendar_start(target) < target - timedelta(days=730)


def test_source_clones_frozen_policy_and_rejects_short_calendar(tmp_path: Path) -> None:
    wire = SimpleNamespace(policy=SimpleNamespace(directory=tmp_path))
    source = BaostockSource(wire, tmp_path / 'market.sqlite')
    other = source.for_history(HistoryPolicy(250, 500))
    assert other.wire is source.wire and source.history_days == 150 and other.history_days == 250
    assert other.history_policy.max_days == 500


def test_requested_history_cannot_silently_shrink_to_strategy_minimum(tmp_path: Path) -> None:
    from stocksieve.modules.ingestion.application.ports import CalendarDay
    target = date(2026, 10, 30)
    dates = {target - timedelta(days=index): CalendarDay(True, None) for index in range(145)}
    source = BaostockSource(SimpleNamespace(policy=SimpleNamespace(directory=tmp_path)), tmp_path / 'market.sqlite', history_days=250)
    source.stocks = lambda day: ()
    source.calendar = lambda day: dates
    with pytest.raises(DomainError) as error:
        asyncio.run(source.universe(target, ()))
    assert error.value.code == 'TRADING_CALENDAR_UNAVAILABLE' and '250' in error.value.message


def test_latest_history_cap_preserves_old_snapshot_and_queued_settings(context: Any) -> None:
    old = context.published()
    stock_id = context.source.universe_stocks[0].stock_id
    path = f'/api/v1/stocks/{stock_id}/candles?period=day&limit=1000'
    historical = context.request('GET', path + '&snapshotId=' + old)[1]['data']
    assert len(historical['items']) == 160
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=140, history_max_trading_days=140)
    job = context.submit('daily')
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=250, history_max_trading_days=500)
    context.run()
    assert context.api.tasks.detail(job, 'sync')['status'] == 'succeeded'
    latest = context.request('GET', path)[1]['data']
    previous = context.request('GET', path + '&snapshotId=' + old)[1]['data']
    assert len(latest['items']) == 140 and len(previous['items']) == 160
    assert previous == historical
    # 参数扩大也不会给旧快照补造更早的数据，原始版本仍由自身历史下界控制。
    assert latest['items'][0]['time'] > previous['items'][0]['time']


def test_recent_strategy_publishes_before_backfill_and_old_snapshot_stays_frozen(context: Any) -> None:
    source = WindowSource(context.clock.now)
    context.source = source
    context.api.registry.sources['fixture'] = source
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=150, history_max_trading_days=160)
    recent = context.published()
    stock = source.universe_stocks[0].stock_id
    candles = f'/api/v1/stocks/{stock}/candles?period=day&limit=1000'
    assert len(context.request('GET', candles)[1]['data']['items']) == 150
    assert context.run()
    with context.api.db.transaction() as connection:
        waiting = connection.execute(select(t.tasks).where(t.tasks.c.status == 'queued')).mappings().one()
        assert waiting['options_json']['historyBackfill'] and waiting['options_json']['historyDays'] == 160
    assert context.request('GET', '/api/v1/opportunities?strategy=short')[1]['data']['total'] == 5
    industry = context.request('GET', '/api/v1/stocks/' + stock)[1]['data']['stock']['industry']
    source.catalog_name = '后来更新的行业'
    context.submit('catalog'); context.run()
    source.paused = False
    context.clock.advance(3 * 86400)
    context.token = context.api.auth.login('admin', 'testpass123')['accessToken']
    assert context.run()
    assert len(context.request('GET', candles)[1]['data']['items']) == 160
    assert len(context.request('GET', candles + '&snapshotId=' + recent)[1]['data']['items']) == 150
    assert context.request('GET', '/api/v1/stocks/' + stock)[1]['data']['stock']['industry'] == industry
    with context.api.db.transaction() as connection:
        assert not connection.scalar(select(t.tasks.c.id).where(t.tasks.c.status.in_(['queued', 'running'])))


def test_legacy_bootstrap_recovery_keeps_checkpoint_and_uses_two_settings(context: Any) -> None:
    context.submit('initialize'); context.run()
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=150, history_max_trading_days=2500)
    job = context.submit('daily')
    with context.api.db.transaction() as connection:
        options = connection.scalar(select(t.tasks.c.options_json).where(t.tasks.c.id == job))
        options.update(historyYears=10, cacheCheckpoint='keep')
        connection.execute(update(t.tasks).where(t.tasks.c.id == job).values(options_json=options, run_after=context.clock.now() + timedelta(days=1)))
    assert context.api.tasks.recover_history_bootstrap()['taskIds'] == [job]
    assert context.api.tasks.recover_history_bootstrap(apply=True)['taskIds'] == [job]
    with context.api.db.transaction() as connection:
        options = connection.scalar(select(t.tasks.c.options_json).where(t.tasks.c.id == job))
    assert 'historyYears' not in options and options['cacheCheckpoint'] == 'keep'
    assert (options['historyDays'], options['historyMaxDays']) == (150, 2500)


def test_persisted_calendar_is_reused_without_upstream_request(tmp_path: Path) -> None:
    target = date(2026, 10, 30)
    source = BaostockSource(SimpleNamespace(policy=SimpleNamespace(directory=tmp_path)), tmp_path / 'market.sqlite')
    days = {source.history_policy.calendar_start(target): CalendarDay(False), target: CalendarDay(True)}
    source.use_calendar({'SSE': days, 'SZSE': days}, datetime.now(SHANGHAI))
    assert source.calendar(target) == days


def test_regular_updates_are_accepted_and_claimed_before_waiting_backfill(context: Any) -> None:
    source = WindowSource(context.clock.now)
    context.source = source
    context.api.registry.sources['fixture'] = source
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=150, history_max_trading_days=160)
    context.published()
    regular = context.submit('daily')
    lease = context.api.tasks.claim('sync', 'priority-test')
    assert lease is not None and lease.id == regular
    with context.api.db.transaction() as connection:
        pending = connection.execute(select(t.tasks).where(t.tasks.c.status == 'queued')).mappings().one()
        assert pending['options_json']['historyBackfill']


def test_running_backfill_yields_after_saved_batch_when_regular_update_arrives(context: Any) -> None:
    source = WindowSource(context.clock.now)
    source.paused = False
    context.source = source
    context.api.registry.sources['fixture'] = source
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=150, history_max_trading_days=160)
    context.published()
    created = []
    def submit_during_download() -> None:
        source.on_prepare = None
        with context.api.db.transaction(serialized=True) as connection:
            user_id = connection.scalar(select(t.users.c.id).where(t.users.c.username == 'admin'))
            job = context.api.tasks.new(connection, category='sync', kind='daily', user_id=user_id,
                source='fixture', target=context.clock.now().date(), options={'retryMinutes': 10, 'readinessHours': 4})
            created.append(job['id'])
    source.on_prepare = submit_during_download
    assert context.run()
    with context.api.db.transaction() as connection:
        backfill = connection.execute(select(t.tasks).where(t.tasks.c.options_json['historyBackfill'].as_boolean().is_(True))).mappings().one()
        assert backfill['status'] == 'queued' and backfill['options_json']['sourceWaitReason'] == 'FOREGROUND_SYNC_PENDING'
        assert '已保存一批真实历史' in backfill['options_json']['sourceProgressMessage']
    assert context.run()
    assert context.api.tasks.detail(created[0], 'sync')['status'] == 'succeeded'
    context.clock.advance(3)
    assert context.run()
    assert context.api.tasks.detail(backfill['id'], 'sync')['status'] == 'succeeded'


def test_scheduled_initial_update_keeps_its_background_history_task_valid(context: Any) -> None:
    source = WindowSource(context.clock.now)
    source.paused = False
    context.source = source
    context.api.registry.sources['fixture'] = source
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=150, history_max_trading_days=160)
    context.submit('initialize'); context.run()
    context.submit('catalog'); context.run()
    with context.api.db.transaction(serialized=True) as connection:
        context.api.tasks.new(connection, category='sync', kind='daily', user_id=None, source='fixture',
            target=context.clock.now().date(), scheduled_for=context.clock.now(), options={'retryMinutes': 10, 'readinessHours': 4})
    assert context.run() and context.run()
    stock = source.universe_stocks[0].stock_id
    assert len(context.request('GET', f'/api/v1/stocks/{stock}/candles?period=day&limit=1000')[1]['data']['items']) == 160
