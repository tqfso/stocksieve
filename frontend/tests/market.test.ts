// 本文件验证复合筛选、策略排序以及模拟 K 线的确定性和 OHLC 聚合口径。
import test from 'node:test';
import assert from 'node:assert/strict';
import { DATA_DATE, STOCKS, STRATEGY_ORDER } from '../src/mock/fixtures.ts';
import { aggregateCandles, filterStocks, generateCandles } from '../src/mock/market.ts';
import type { Candle, StockQuery } from '../src/types.ts';
const base: StockQuery = { strategy: 'short', search: '', industry: '', concept: '', board: '', minTurnover: undefined, maxTurnover: undefined, minMarketCap: undefined, maxMarketCap: undefined, sort: 'default', descending: true };
test('复合条件只返回匹配策略、行业、概念、上市板块与换手率的股票', () => {
    const results = filterStocks(STOCKS, { ...base, industry: '电子', concept: '半导体', board: 'STAR', minTurnover: 6, maxTurnover: 10 });
    assert.deepEqual(results.map((stock) => stock.code), ['688256']);
    assert.equal(filterStocks(STOCKS, { ...base, search: '不存在' }).length, 0);
    assert.equal(filterStocks(STOCKS, { ...base, search: ' 300750 ' })[0]?.name, '宁德时代');
});
test('板块筛选区分同一交易所下的主板与创业板或科创板', () => {
    const codes = (board: StockQuery['board']): string[] => filterStocks(STOCKS, { ...base, board }).map((stock) => stock.code);
    assert.deepEqual(codes('SSE_MAIN'), ['601127', '601899']);
    assert.deepEqual(codes('SZSE_MAIN'), ['002594']);
    assert.deepEqual(codes('CHINEXT'), ['300750']);
    assert.deepEqual(codes('STAR'), ['688256', '688981', '688111']);
    assert.deepEqual(codes('BSE'), ['430047']);
    assert.equal(codes('').length, 8);
});
test('总市值支持单侧、完整区间及包含边界的组合筛选', () => {
    const codes = (filters: Partial<StockQuery>): string[] => filterStocks(STOCKS, { ...base, ...filters }).map((stock) => stock.code);
    assert.deepEqual(codes({ maxMarketCap: 100 }), ['430047']);
    assert.deepEqual(codes({ minMarketCap: 10000 }), ['300750']);
    assert.deepEqual(codes({ minMarketCap: 1850, maxMarketCap: 2400 }), ['688256', '601127']);
    assert.deepEqual(codes({ minMarketCap: 1500, maxMarketCap: 1500 }), ['688111']);
    assert.deepEqual(codes({ board: 'STAR', minMarketCap: 1000, maxMarketCap: 3000 }), ['688256', '688111']);
    assert.deepEqual(codes({ minMarketCap: 20000 }), []);
});
test('缺失总市值不会当作零，排序时始终位于已知值之后', () => {
    const stocks = [
        STOCKS[3]!,
        { ...STOCKS[10]!, totalMarketCap: null },
        { ...STOCKS[11]!, totalMarketCap: 0 },
    ];
    assert.deepEqual(filterStocks(stocks, { ...base, maxMarketCap: 0 }).map((stock) => stock.code), ['430047']);
    assert.deepEqual(filterStocks(stocks, { ...base, minMarketCap: 0 }).map((stock) => stock.code), ['430047', '002594']);
    assert.deepEqual(filterStocks(stocks, { ...base, sort: 'totalMarketCap', descending: false }).map((stock) => stock.code), ['430047', '002594', '688256']);
    assert.deepEqual(filterStocks(stocks, { ...base, sort: 'totalMarketCap', descending: true }).map((stock) => stock.code), ['002594', '430047', '688256']);
});
test('三类策略使用各自排序口径，用户排序支持两个方向', () => {
    for (const strategy of ['short', 'swing', 'long'] as const) {
        const results = filterStocks(STOCKS, { ...base, strategy });
        for (let index = 1; index < results.length; index++) {
            const a = results[index - 1]!, b = results[index]!;
            assert.ok(strategy === 'short' ? a.volumeRatio >= b.volumeRatio : strategy === 'swing' ? a.ma20Distance <= b.ma20Distance : a.ma60Distance <= b.ma60Distance);
        }
    }
    const descending = filterStocks(STOCKS, { ...base, sort: 'turnover', descending: true });
    const ascending = filterStocks(STOCKS, { ...base, sort: 'turnover', descending: false });
    assert.deepEqual(descending.map((stock) => stock.code), ascending.map((stock) => stock.code).reverse());
});
test('行情生成可重现，末日收盘价一致，OHLC 合法且覆盖多年', () => {
    const stock = STOCKS[1]!;
    const candles = generateCandles(stock);
    assert.deepEqual(candles, generateCandles(stock));
    assert.equal(candles.at(-1)?.time, DATA_DATE);
    assert.equal(candles.at(-1)?.close, stock.price);
    assert.ok(Math.abs((stock.price / candles.at(-2)!.close - 1) * 100 - stock.change) < 0.01);
    assert.ok(aggregateCandles(candles, 'year').length >= 9);
    assert.ok(candles.every((item, index) => item.low > 0 && item.low <= Math.min(item.open, item.close) && item.high >= Math.max(item.open, item.close) && item.volume > 0 && (index === 0 || candles[index - 1]!.time < item.time)));
});
test('等待策略支持独立筛选、默认排序，并遵守与其他策略的边界', () => {
    assert.equal(STRATEGY_ORDER.length, 5);
    const trend = filterStocks(STOCKS, { ...base, strategy: 'trend_wait' });
    const bottom = filterStocks(STOCKS, { ...base, strategy: 'base_wait' });
    assert.equal(trend[0]?.code, '600900');
    assert.equal(bottom[0]?.code, '688009');
    assert.ok(trend.some((stock) => stock.strategies.includes('long') && stock.strategies.includes('swing')));
    assert.deepEqual(filterStocks(STOCKS, { ...base, strategy: 'base_wait', board: 'CHINEXT' }).map((stock) => stock.code), ['300124']);
    for (const stock of [...trend, ...bottom]) {
        const waiting = stock.waiting!;
        assert.ok(waiting.lower <= stock.price && stock.price <= waiting.upper);
        assert.ok(waiting.upper / waiting.lower - 1 <= 0.12);
        assert.ok(waiting.amountRatio <= 0.8);
        assert.equal(stock.strategies.includes('short'), false);
        const ma20 = stock.price / (1 + stock.ma20Distance / 100);
        const ma60 = stock.price / (1 + stock.ma60Distance / 100);
        if (stock.strategies.includes('trend_wait')) {
            assert.ok(ma20 >= ma60);
            assert.ok((waiting.upper - stock.price) / waiting.upper <= 0.03);
        } else {
            assert.ok(ma20 < ma60 && waiting.position120 <= 35);
            assert.deepEqual(stock.strategies, ['base_wait']);
        }
    }
});
test('等待样例的近期K线与固定区间、现价保持一致', () => {
    for (const stock of STOCKS.filter((item) => item.waiting)) {
        const candles = generateCandles(stock);
        const previous20 = candles.slice(-21, -1);
        assert.equal(Math.max(...previous20.map((item) => item.high)), stock.waiting!.upper);
        assert.equal(Math.min(...previous20.map((item) => item.low)), stock.waiting!.lower);
        assert.equal(candles.at(-1)!.close, stock.price);
        assert.ok(candles.slice(-21).every((item) => item.low <= Math.min(item.open, item.close) && item.high >= Math.max(item.open, item.close)));
        assert.ok(Math.abs((stock.price / candles.at(-2)!.close - 1) * 100 - stock.change) < 0.1);
    }
});
test('周线按周一分组，年线跨年分组，并保持 OHLC 与成交量口径', () => {
    const candles: Candle[] = [
        { time: '2025-12-31', open: 10, close: 12, high: 13, low: 9, volume: 100 },
        { time: '2026-01-02', open: 12, close: 11, high: 14, low: 10, volume: 200 },
        { time: '2026-01-05', open: 11, close: 15, high: 16, low: 10, volume: 300 },
    ];
    const weeks = aggregateCandles(candles, 'week');
    assert.equal(weeks.length, 2);
    assert.deepEqual(weeks[0], { time: '2026-01-02', open: 10, close: 11, high: 14, low: 9, volume: 300 });
    const years = aggregateCandles(candles, 'year');
    assert.equal(years.length, 2);
    assert.deepEqual(years[1], { time: '2026-01-05', open: 12, close: 15, high: 16, low: 10, volume: 500 });
    assert.deepEqual(aggregateCandles([], 'week'), []);
    assert.equal(candles[0]?.close, 12);
});
