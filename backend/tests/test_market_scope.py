"""本文件验证用户选择的四板块范围、任务冻结及后续配置变化不能改写已发布范围。"""

from dataclasses import replace
from typing import Any

from stocksieve.domain import BOARDS


def four_boards(context: Any) -> None:
    settings = replace(context.api.settings, market_boards=tuple(board for board in BOARDS if board != 'BSE'))
    context.api.settings = settings
    context.api.tasks.settings = settings
    context.api.registry.boards = frozenset(settings.market_boards)


def test_four_board_publication_and_filter_coverage(context: Any) -> None:
    four_boards(context)
    snapshot = context.published()
    status = context.request('GET', '/api/v1/market/status')[1]['data']
    assert status['coverage']['totalStocks'] == 4 and status['coverage']['byExchange']['BSE'] == 0
    assert status['latestTradingDate'] == '2026-10-30'
    assert 'BSE' not in status['publishedSnapshot']['boards']
    filters = context.request('GET', '/api/v1/catalog/filters')[1]['data']
    assert len(filters['boards']) == 4
    code, result, _ = context.request('GET', '/api/v1/opportunities?strategy=short&board=BSE')
    assert code == 400 and result['error']['code'] == 'FILTER_UNAVAILABLE'
    assert context.request('GET', '/api/v1/opportunities?strategy=short')[1]['data']['snapshot']['id'] == snapshot


def test_queued_task_keeps_accepted_scope_after_configuration_changes(context: Any) -> None:
    four_boards(context)
    context.submit('initialize'); context.run()
    job = context.submit('daily')
    context.api.settings = replace(context.api.settings, market_boards=tuple(BOARDS))
    context.api.tasks.settings = context.api.settings
    context.api.registry.boards = frozenset(BOARDS)
    context.run()
    result = context.api.tasks.detail(job, 'sync')
    assert result['status'] == 'succeeded' and 'BSE' not in result['boards']
    published = context.request('GET', '/api/v1/market/status')[1]['data']
    assert published['initialized'] is False
    assert 'BSE' not in published['publishedSnapshot']['boards']
