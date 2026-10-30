// 本文件实现可测试的模拟筛选与确定性 OHLC 数据生成，不读取网络行情。
import type { Candle, Period, Stock, StockQuery } from '../types.ts';
import { DATA_DATE, distanceToUpper } from './fixtures.ts';
/** 同一套筛选口径服务于页面与模拟异步接口。 */
export function filterStocks(stocks: Stock[], query: StockQuery): Stock[] {
    const needle = query.search.trim().toLowerCase();
    return stocks.filter((stock) => stock.strategies.includes(query.strategy) &&
        (!needle || `${stock.code}${stock.name}`.toLowerCase().includes(needle)) &&
        (!query.industry || stock.industry === query.industry) &&
        (!query.concept || stock.concepts.includes(query.concept)) &&
        (!query.board || stock.board === query.board) &&
        (query.minTurnover === undefined || stock.turnover >= query.minTurnover) &&
        (query.maxTurnover === undefined || stock.turnover <= query.maxTurnover) &&
        (query.minMarketCap === undefined || (stock.totalMarketCap !== null && stock.totalMarketCap >= query.minMarketCap)) &&
        (query.maxMarketCap === undefined || (stock.totalMarketCap !== null && stock.totalMarketCap <= query.maxMarketCap))).sort((a, b) => {
        if (query.sort !== 'default') {
            const first = a[query.sort], second = b[query.sort];
            // 缺失市值不当作零，升序和降序都排在已知值后面。
            if (first === null) return second === null ? 0 : 1;
            if (second === null) return -1;
            return (first - second) * (query.descending ? -1 : 1);
        }
        if (query.strategy === 'short')
            return b.volumeRatio - a.volumeRatio;
        if (query.strategy === 'trend_wait')
            return distanceToUpper(a) - distanceToUpper(b) || a.code.localeCompare(b.code);
        if (query.strategy === 'base_wait')
            return (a.waiting?.position120 ?? Infinity) - (b.waiting?.position120 ?? Infinity) || a.code.localeCompare(b.code);
        return query.strategy === 'swing' ? a.ma20Distance - b.ma20Distance : a.ma60Distance - b.ma60Distance;
    });
}
/** 通过固定种子生成工作日示例，末根收盘价与股票卡片保持一致。 */
export function generateCandles(stock: Stock): Candle[] {
    let seed = Number(stock.code);
    const random = (): number => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    const dates: string[] = [];
    const cursor = new Date(`${DATA_DATE}T00:00:00Z`);
    while (dates.length < 2400) {
        if (cursor.getUTCDay() !== 0 && cursor.getUTCDay() !== 6)
            dates.unshift(cursor.toISOString().slice(0, 10));
        cursor.setUTCDate(cursor.getUTCDate() - 1);
    }
    let previous = stock.price * 0.48;
    const candles = dates.map((time, index) => {
        const open = previous * (1 + (random() - 0.5) * 0.012);
        const close = open * (1 + (random() - 0.48) * 0.035 + (index > 2340 ? 0.002 : 0));
        const high = Math.max(open, close) * (1 + random() * 0.017);
        const low = Math.min(open, close) * (1 - random() * 0.017);
        previous = close;
        return { time, open, close, high, low, volume: Math.round(1000000 + random() * 8000000) };
    });
    // 统一缩放所有 OHLC，确保确定性走势与详情报价一致。
    const ratio = stock.price / candles[candles.length - 1]!.close;
    const scaled = candles.map((item) => ({ ...item, open: +(item.open * ratio).toFixed(2), close: +(item.close * ratio).toFixed(2), high: +(item.high * ratio).toFixed(2), low: +(item.low * ratio).toFixed(2) }));
    // 等待样例的近期 K 线整理于同一固定区间，便于评审上下沿与现价的关系。
    if (stock.waiting) {
        const { lower, upper } = stock.waiting;
        const width = upper - lower;
        for (let offset = 0; offset <= 20; offset++) {
            const candle = scaled[scaled.length - 21 + offset]!;
            candle.close = +(lower + width * (0.52 + Math.sin(offset * 1.1) * 0.22)).toFixed(2);
            candle.open = +(lower + width * (0.50 + Math.sin((offset - 1) * 1.1) * 0.22)).toFixed(2);
            candle.high = +Math.min(upper, Math.max(candle.open, candle.close) + width * 0.08).toFixed(2);
            candle.low = +Math.max(lower, Math.min(candle.open, candle.close) - width * 0.08).toFixed(2);
            candle.volume = Math.round((2000000 + random() * 800000) * (1 - offset * 0.02));
        }
        scaled[scaled.length - 19]!.high = upper;
        scaled[scaled.length - 18]!.low = lower;
        const latest = scaled.at(-1)!;
        latest.close = stock.price;
        latest.high = Math.max(latest.high, stock.price);
        latest.low = Math.min(latest.low, stock.price);
    }
    // 对齐最后两日的收盘价，使概览涨跌幅与图中最后一日保持同一口径。
    const previousDay = scaled[scaled.length - 2]!;
    previousDay.close = +(stock.price / (1 + stock.change / 100)).toFixed(2);
    previousDay.high = Math.max(previousDay.high, previousDay.close);
    previousDay.low = Math.min(previousDay.low, previousDay.close);
    return scaled;
}
/** 按 UTC 周一或年份聚合，保留首开、末收、最高、最低与成交量总和。 */
export function aggregateCandles(candles: Candle[], period: Period): Candle[] {
    if (period === 'day')
        return candles;
    const groups = new Map<string, Candle>();
    for (const candle of candles) {
        const date = new Date(`${candle.time}T00:00:00Z`);
        date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
        const key = period === 'year' ? candle.time.slice(0, 4) : date.toISOString().slice(0, 10);
        const group = groups.get(key);
        if (group) {
            group.close = candle.close;
            group.high = Math.max(group.high, candle.high);
            group.low = Math.min(group.low, candle.low);
            group.volume += candle.volume;
            group.time = candle.time;
        }
        else
            groups.set(key, { ...candle });
    }
    return [...groups.values()];
}
