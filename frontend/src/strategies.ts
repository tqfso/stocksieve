// 本文件提供策略展示文案；入选理由与数值均读取后端冻结信号。
import type { Strategy } from "./types.ts";
import type { LiveStock } from "./api.ts";
import { formatNumber } from "./api.ts";
export function isWaitingStrategy(strategy: Strategy): boolean { return strategy === 'trend_wait' || strategy === 'base_wait'; }
export function signalMetrics(stock: LiveStock, strategy: Strategy): Record<string, number | null> { return stock.signals.find(row => row.strategy === strategy)?.metrics ?? {}; }
export function selectionReason(stock: LiveStock, strategy: Strategy): string { return stock.signals.find(row => row.strategy === strategy)?.reason ?? '所选快照未入选此策略。'; }
export function strategyMetric(stock: LiveStock, strategy: Strategy): string {
    const metrics = signalMetrics(stock, strategy);
    if (strategy === 'short') return formatNumber(metrics.amountMultiple) + '×';
    if (strategy === 'swing') return formatNumber(metrics.pullbackAmountRatio == null ? null : metrics.pullbackAmountRatio * 100, 0) + '%';
    if (strategy === 'long') return formatNumber(metrics.ma60Distance, 1) + '%';
    if (strategy === 'trend_wait') return formatNumber(metrics.distanceToUpper, 1) + '%';
    return formatNumber(metrics.position120, 0) + '%';
}
/** 页面顺序、网址恢复和测试共用同一策略清单。 */
export const STRATEGY_ORDER: Strategy[] = ['short', 'swing', 'long', 'trend_wait', 'base_wait'];
/** 策略定义复用现有产品文档的展示口径。 */
export const STRATEGIES: Record<Strategy, {
    name: string;
    subtitle: string;
    period: string;
    description: string;
    invalid: string;
    conditions: string[];
    metricName: string;
    metricHint: string;
    icon: string;
}> = {
    short: {
        name: '短线', subtitle: '放量突破', period: '3～10 个交易日',
        description: '关注突破近期高点、成交额同步放大的机会。',
        invalid: '后续收盘价低于入选时的突破参考价 B，该次突破信号失效。',
        conditions: ['收盘价突破此前 20 个交易日最高价', '当日成交额至少为此前 5 日均值的 1.5 倍', '收盘价高于 MA20，偏离幅度不超过 10%'],
        metricName: '成交额放大', metricHint: '倍数从高到低', icon: 'chart',
    },
    swing: {
        name: '波段', subtitle: '回调企稳', period: '2～8 周',
        description: '关注上升趋势中，缩量回调后重新企稳的机会。',
        invalid: '后续收盘价低于入选时记录的回调参考低点 P，该次企稳信号失效。',
        conditions: ['MA20 高于 MA60，且 MA60 较 5 个交易日前抬升', '此前 5 日内曾下跌回调至 MA20 上下 3% 范围', '此前 5 日均成交额不超过此前 20 日均值的 80%', '当日收涨，且收盘价位于 MA20 上方'],
        metricName: '回调成交额比', metricHint: '距 MA20 从小到大', icon: 'refresh',
    },
    long: {
        name: '长线', subtitle: '趋势持续', period: '3～12 个月',
        description: '关注中长期均线保持多头排列并持续向上的机会。',
        invalid: '连续 5 个交易日收盘价低于各自 MA60，或 MA60 不再高于 MA120，长期趋势信号失效。',
        conditions: ['MA20 > MA60 > MA120', 'MA60 与 MA120 均高于 20 个交易日前的数值', '收盘价高于 MA60，偏离幅度不超过 20%'],
        metricName: '距 MA60', metricHint: '距 MA60 从小到大', icon: 'spark',
    },
    trend_wait: {
        name: '趋势埋伏', subtitle: '缩量整理', period: '1～4 周',
        description: '关注上升趋势中的窄幅整理，提前观察区间上沿的突破条件。',
        invalid: '后续收盘价低于入选时的区间下沿 P，或趋势条件不再满足，本次等待信号失效。',
        conditions: ['MA20 ≥ MA60，且 MA60 不低于 5 个交易日前', '此前 20 日区间宽度不超过 12%', '近 5 日均成交额不超过近 20 日均值的 80%', '收盘价位于区间内，距此前 20 日上沿不超过 3%'],
        metricName: '距区间上沿', metricHint: '距上沿从小到大', icon: 'clock',
    },
    base_wait: {
        name: '低位筑底', subtitle: '筑底观察', period: '2～8 周',
        description: '关注下跌后在历史低区横盘、近期停止创新低的股票，等待趋势修复。',
        invalid: '后续收盘价低于入选时的区间下沿 P，本次筑底等待信号失效；其余条件不满足时移出当日名单。',
        conditions: ['MA20 < MA60，趋势尚待修复', '收盘价位于此前 120 日高低区间的 35% 以下', '此前 20 日区间宽度不超过 12%，收盘价仍在区间内', '最近 5 日最低价不低于此前 15 日最低价', '近 5 日均成交额不超过近 20 日均值的 80%'],
        metricName: '120日区间位置', metricHint: '区间位置从低到高', icon: 'layers',
    },
};
