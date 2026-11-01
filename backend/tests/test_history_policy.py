"""本文件验证环境配置、任务冻结、真实日历选择及历史上限不改写旧快照。"""

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from stocksieve.modules.ingestion.infrastructure.providers.baostock import (
    BaostockSource,
)
from stocksieve.modules.market.domain.history import HistoryPolicy
from stocksieve.shared.errors import DomainError, MissingData
from stocksieve.shared.settings import Settings


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
