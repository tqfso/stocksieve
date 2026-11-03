"""本文件提供可重复执行的新浪实测，复用正式访问账本和缓存，不修改业务表。"""

import asyncio
from datetime import date, datetime
from typing import Any

from stocksieve.modules.ingestion.application.ports import SourceDeferred
from stocksieve.modules.ingestion.infrastructure.providers.sina import (
    SAMPLES,
    SinaSource,
)
from stocksieve.modules.ingestion.infrastructure.providers.tencent import (
    TencentMetadata,
)
from stocksieve.modules.ingestion.infrastructure.rate_policy import SourcePolicy
from stocksieve.modules.market.domain.models import Stock, adjusted_history
from stocksieve.shared.settings import Settings


async def check_sina(settings: Settings, policy: SourcePolicy, target: date, directory: bool = False) -> dict[str, Any]:
    """最多等待一次请求间隔；预算或冷却等待交给调用者，不自动重试被拒绝的请求。"""
    source = SinaSource(policy, settings.state_dir / 'source-cache', settings.history_trading_days, settings.history_max_trading_days,
        TencentMetadata(policy, settings.state_dir / 'source-cache'))
    try:
        return await probe(source, settings, target, directory)
    finally:
        await source.close()


async def probe(source: SinaSource, settings: Settings, target: date, directory: bool) -> dict[str, Any]:
    """使用同一会话核验样本，仅对短暂本地请求间隔继续执行。"""

    async def paced(call: Any) -> Any:
        while True:
            try:
                return await call()
            except SourceDeferred as wait:
                delay = wait.until.timestamp() - datetime.now().timestamp()
                if wait.reason != 'SOURCE_REQUEST_INTERVAL' or delay > settings.request_interval + 2:
                    raise
                await asyncio.sleep(max(.01, delay))

    await paced(lambda: source.history_calendar(target))
    checks = await paced(lambda: source.precheck(target))
    samples = []
    for board, symbol in SAMPLES.items():
        exchange = 'SSE' if symbol.startswith('sh') else 'SZSE'
        stock = Stock(f'{exchange}.{symbol[2:]}', symbol[2:], symbol, exchange, board)
        packet = await paced(lambda stock=stock: source.daily(stock, target, ()))
        adjusted = adjusted_history(list(packet.bars), target)
        samples.append({'board': board, 'code': stock.code, 'bars': len(packet.bars),
            'latestDate': str(packet.bars[-1].day), 'rawClose': str(packet.bars[-1].close),
            'factor': str(packet.bars[-1].factor), 'qfqClose': str(adjusted[-1].close),
            'turnover': str(packet.bars[-1].turnover), 'excludedReason': packet.excluded_reason})
    stocks = await paced(source.directory) if directory else ()
    return {'source': source.id, 'targetDate': str(target), 'precheck': checks, 'samples': samples,
        'directoryCount': len(stocks) if directory else None, 'directoryBoards': sorted({s.board for s in stocks}),
        'samplesVerified': all(c['status'] == 'ready' for c in checks) and all(s['factor'] != 'None' and s['turnover'] != 'None' for s in samples)}
