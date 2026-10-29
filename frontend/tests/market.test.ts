// 本文件验证复合筛选、策略排序以及模拟 K 线的确定性和 OHLC 聚合口径。
import test from 'node:test';
import assert from 'node:assert/strict';
import { DATA_DATE, STOCKS } from '../src/mock/fixtures.ts';
import { aggregateCandles, filterStocks, generateCandles } from '../src/mock/market.ts';
import type { Candle, StockQuery } from '../src/types.ts';
const base: StockQuery = { strategy: 'short', search: '', industry: '', concept: '', exchange: '', minTurnover: undefined, maxTurnover: undefined, sort: 'default', descending: true };
test('复合条件只返回匹配策略、行业、概念、交易所与换手率的股票', () => {
    const results = filterStocks(STOCKS, { ...base, industry: '电子', concept: '半导体', exchange: '上海', minTurnover: 6, maxTurnover: 10 });
    assert.deepEqual(results.map((stock) => stock.code), ['688256']);
    assert.equal(filterStocks(STOCKS, { ...base, search: '不存在' }).length, 0);
    assert.equal(filterStocks(STOCKS, { ...base, search: ' 300750 ' })[0]?.name, '宁德时代');
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
