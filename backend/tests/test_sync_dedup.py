"""本文件验证同日普通更新复用成功结果，明确重算、分类和配置变化仍能生成新任务。"""

from dataclasses import replace
from typing import Any

import stocksieve.infrastructure.persistence.schema as t
from sqlalchemy import func, select


def test_completed_same_date_is_reused_without_new_task_or_snapshot(context: Any) -> None:
    snapshot = context.published()
    with context.api.db.transaction() as connection:
        task_id = connection.scalar(select(t.snapshots.c.sync_task_id).where(t.snapshots.c.id == snapshot))
        count = connection.scalar(select(func.count()).select_from(t.tasks))
    status, body, headers = context.request('POST', '/api/v1/admin/sync-jobs',
        {'kind': 'daily', 'source': 'fixture', 'dataDate': '2026-10-30'}, key='already-completed')
    assert status == 202 and body['data']['id'] == task_id and body['data']['status'] == 'succeeded'
    assert headers['Location'].endswith(task_id)
    with context.api.db.transaction() as connection:
        assert connection.scalar(select(func.count()).select_from(t.tasks)) == count
    assert not context.run()


def test_force_refresh_and_updated_catalog_are_not_silently_skipped(context: Any) -> None:
    context.published()
    forced = context.submit('daily', force=True)
    assert context.api.tasks.detail(forced, 'sync')['status'] == 'queued'
    context.run()
    context.source.catalog_name = '更新行业'
    context.submit('catalog'); context.run()
    refreshed = context.submit('daily', force=False)
    assert refreshed != forced and context.api.tasks.detail(refreshed, 'sync')['status'] == 'queued'


def test_changed_history_window_needs_a_new_task(context: Any) -> None:
    context.published()
    context.api.tasks.settings = replace(context.api.settings, history_trading_days=250, history_max_trading_days=500)
    job = context.submit('daily', force=False)
    assert context.api.tasks.detail(job, 'sync')['status'] == 'queued'


def test_force_is_strict_and_only_valid_for_daily(context: Any) -> None:
    path = '/api/v1/admin/sync-jobs'
    assert context.request('POST', path, {'kind': 'initialize', 'source': 'fixture', 'force': True}, key='invalid-force')[0] == 400
    assert context.request('POST', path, {'kind': 'daily', 'source': 'fixture', 'force': 'true'}, key='string-force')[0] == 400


def test_historical_recalculation_keeps_confirmed_status_and_cap_when_source_has_no_history_metadata(context: Any) -> None:
    from stocksieve.modules.market.domain.models import StockState
    context.submit('initialize'); context.run()
    first = context.submit('daily', day='2026-10-29'); context.run()
    original = context.source.daily
    async def historical(*args: Any) -> Any:
        packet = await original(*args)
        return replace(packet, state=StockState(None, None, None), bars=tuple(replace(bar, total_market_cap=None) for bar in packet.bars))
    context.source.daily = historical
    second = context.submit('daily', day='2026-10-29'); context.run()
    assert second != first and context.api.tasks.detail(second, 'sync')['status'] == 'succeeded'
    result = context.request('GET', '/api/v1/opportunities?strategy=short')[1]['data']
    assert result['total'] == 5
