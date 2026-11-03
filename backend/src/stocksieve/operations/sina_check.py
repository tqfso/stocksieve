"""本文件提供可重复执行的新浪实测，复用正式访问账本和缓存，不修改业务表。"""

import asyncio
import json
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
from stocksieve.shared.errors import DomainError
from stocksieve.shared.settings import Settings


async def check_basics(settings: Settings, policy: SourcePolicy) -> dict[str, Any]:
    """通过现有频控检查公开基础端点，原文由正式HTTP缓存保存，不写业务表。"""
    source = SinaSource(policy, settings.state_dir / 'source-cache')
    endpoints = {
        'industries': 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php',
        'csrcIndustries': 'https://money.finance.sina.com.cn/q/view/newFLJK.php?param=industry',
        'nodes': 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodes',
        'company': 'https://money.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/600000.phtml',
    }
    results = {}
    try:
        for name, url in endpoints.items():
            raw, fetched = await source.document(url, 86400)
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                text = raw.decode('gb18030')
            if name == 'company':
                position = text.find('上市日期')
                excerpt = text[max(0, position - 100):position + 300] if position >= 0 else text[:300]
            else:
                excerpt = text[:2500]
            results[name] = {'bytes': len(raw), 'fetchedAt': fetched.isoformat(), 'excerpt': excerpt}
        from urllib.parse import urlencode

        from stocksieve.modules.ingestion.infrastructure.http import SourceHTTP
        query = {'stock': json.dumps([{'code': code, 'market': 'ab', 'type': 'stock'} for code in ['600000', '000001', '300750', '688981']], separators=(',', ':')), 'finClientType': 'pc'}
        client = SourceHTTP(policy, settings.state_dir / 'source-cache')
        try:
            raw, fetched = await client.document('baidu', 'https://finance.pae.baidu.com/api/getrelatedblock?' + urlencode(query))
            results['baiduCategories'] = {'fetchedAt': fetched.isoformat(), 'excerpt': raw.decode('utf-8')[:6000]}
        finally:
            await client.close()
        from stocksieve.modules.ingestion.infrastructure.providers.sina_catalog import (
            SinaCatalog,
            listing_date,
        )
        catalog = SinaCatalog(settings.state_dir / 'source-cache', source.document)
        foundation = {}
        for symbol in ['sh600000', 'sz000001', 'sz300750', 'sh688981']:
            raw, _ = await source.document('https://money.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/' + symbol[2:] + '.phtml', 365 * 86400)
            foundation[symbol] = {'listedOn': str(listing_date(raw)), 'board': await catalog.board(symbol)}
        try:
            foundation['sz302132'] = {'board': await catalog.board('sz302132')}
        except DomainError as error:
            foundation['sz302132'] = {'error': type(error).__name__}
        results['foundation'] = foundation
        return results
    finally:
        await source.close()


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
