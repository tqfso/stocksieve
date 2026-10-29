// 本文件提供固定的示例股票与策略文案；所有价格和指标均为虚构。
import type { Stock, Strategy } from '../types.ts';
export const DATA_DATE = '2026-10-28';
/** 策略定义复用现有产品文档的展示口径。 */
export const STRATEGIES: Record<Strategy, {
    name: string;
    subtitle: string;
    period: string;
    description: string;
    invalid: string;
    conditions: string[];
}> = {
    short: {
        name: '短线', subtitle: '放量突破', period: '3～10 个交易日',
        description: '关注突破近期高点、成交额同步放大的机会。',
        invalid: '后续收盘价低于入选时的突破参考价 B，该次突破信号失效。',
        conditions: ['收盘价突破此前 20 个交易日最高价', '当日成交额至少为此前 5 日均值的 1.5 倍', '收盘价高于 MA20，偏离幅度不超过 10%'],
    },
    swing: {
        name: '波段', subtitle: '回调企稳', period: '2～8 周',
        description: '关注上升趋势中，缩量回调后重新企稳的机会。',
        invalid: '后续收盘价低于入选时记录的回调参考低点 P，该次企稳信号失效。',
        conditions: ['MA20 高于 MA60，且 MA60 较 5 个交易日前抬升', '此前 5 日内曾下跌回调至 MA20 上下 3% 范围', '此前 5 日均成交额不超过此前 20 日均值的 80%', '当日收涨，且收盘价位于 MA20 上方'],
    },
    long: {
        name: '长线', subtitle: '趋势持续', period: '3～12 个月',
        description: '关注中长期均线保持多头排列并持续向上的机会。',
        invalid: '连续 5 个交易日收盘价低于各自 MA60，或 MA60 不再高于 MA120，长期趋势信号失效。',
        conditions: ['MA20 > MA60 > MA120', 'MA60 与 MA120 均高于 20 个交易日前的数值', '收盘价高于 MA60，偏离幅度不超过 20%'],
    },
};
/** 示例横跨三大交易所、多个行业，并包含同时满足多个策略的股票。 */
export const STOCKS: Stock[] = [
    ['600519', '贵州茅台', '上海', 'SSE_MAIN', '食品饮料', ['白酒', '消费'], ['long'], 1462.50, 1.26, 0.42, 48.6, 1.1, 2.5, 6.2, 0.7, 20000],
    ['300750', '宁德时代', '深圳', 'CHINEXT', '电力设备', ['新能源', '储能'], ['short', 'long'], 268.32, 4.35, 2.86, 86.4, 2.3, 6.8, 12.4, 0.65, 11800],
    ['600036', '招商银行', '上海', 'SSE_MAIN', '银行', ['金融', '高股息'], ['long', 'swing'], 42.68, 0.85, 0.68, 23.8, 1.2, 1.6, 4.8, 0.72, 10500],
    ['002594', '比亚迪', '深圳', 'SZSE_MAIN', '汽车', ['新能源', '智能驾驶'], ['short'], 312.56, 3.62, 3.24, 72.3, 1.9, 5.3, 14.2, 0.68, 9000],
    ['688981', '中芯国际', '上海', 'STAR', '电子', ['半导体', '国产替代'], ['short'], 98.24, 6.18, 5.82, 65.2, 2.8, 8.6, 18.3, 0.62, 7800],
    ['000333', '美的集团', '深圳', 'SZSE_MAIN', '家用电器', ['消费', '机器人'], ['swing', 'long'], 78.46, 1.58, 1.12, 18.9, 1.05, 1.2, 5.4, 0.68, 5600],
    ['601899', '紫金矿业', '上海', 'SSE_MAIN', '有色金属', ['黄金', '资源'], ['short', 'long'], 23.58, 3.14, 2.45, 42.7, 1.75, 4.2, 9.6, 0.75, 6200],
    ['300059', '东方财富', '深圳', 'CHINEXT', '非银金融', ['金融', '互联网'], ['swing'], 26.82, 2.18, 4.56, 58.3, 1.14, 0.8, 6.8, 0.64, 4100],
    ['600900', '长江电力', '上海', 'SSE_MAIN', '公用事业', ['高股息', '绿电'], ['long'], 29.86, -0.46, 0.36, 12.6, 0.9, 1.4, 3.2, 0.78, 7300],
    ['002475', '立讯精密', '深圳', 'SZSE_MAIN', '电子', ['消费电子', '机器人'], ['swing'], 46.32, 1.92, 2.35, 22.4, 1.12, 2.1, 8.2, 0.72, 3200],
    ['688256', '寒武纪', '上海', 'STAR', '电子', ['人工智能', '半导体'], ['short'], 586.42, 7.36, 8.24, 56.8, 3.12, 9.2, 19.5, 0.6, 2400],
    ['430047', '诺思兰德', '北京', 'BSE', '医药生物', ['创新药', '生物医药'], ['short'], 22.36, 8.24, 12.56, 3.8, 2.65, 7.4, 16.8, 0.65, 60],
    ['835185', '贝特瑞', '北京', 'BSE', '电力设备', ['新能源', '锂电池'], ['swing', 'long'], 32.48, 2.45, 3.67, 4.2, 1.08, 1.8, 7.6, 0.7, 360],
    ['601127', '赛力斯', '上海', 'SSE_MAIN', '汽车', ['智能驾驶', '人工智能'], ['short'], 128.64, 4.82, 6.42, 47.6, 2.05, 6.1, 15.2, 0.72, 1850],
    ['600276', '恒瑞医药', '上海', 'SSE_MAIN', '医药生物', ['创新药', '生物医药'], ['swing', 'long'], 56.72, 1.36, 1.56, 16.8, 1.2, 0.6, 4.2, 0.66, 3400],
    ['000858', '五粮液', '深圳', 'SZSE_MAIN', '食品饮料', ['白酒', '消费'], ['swing'], 138.26, 0.92, 0.82, 12.4, 1.02, 1.4, 6.3, 0.74, 5200],
    ['688111', '金山办公', '上海', 'STAR', '计算机', ['人工智能', '国产替代'], ['short', 'long'], 324.82, 3.78, 2.74, 15.6, 1.82, 5.6, 11.4, 0.7, 1500],
    ['832982', '锦波生物', '北京', 'BSE', '美容护理', ['生物医药', '消费'], ['long'], 198.46, -1.24, 1.32, 2.1, 0.96, 2.4, 8.6, 0.76, 280],
].map((row) => {
    const [code, name, exchange, board, industry, concepts, strategies, price, change, turnover, amount, volumeRatio, ma20Distance, ma60Distance, pullbackRatio, totalMarketCap] = row as [
        string,
        string,
        Stock['exchange'],
        Stock['board'],
        string,
        string[],
        Strategy[],
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number,
        number
    ];
    return { code, name, exchange, board, industry, concepts, strategies, price, change, turnover, amount, totalMarketCap, volumeRatio, ma20Distance, ma60Distance, pullbackRatio, reference: +(price / 1.03).toFixed(2) };
});
/** 展示理由来自示例指标，不承担真实市场策略计算。 */
export function selectionReason(stock: Stock, strategy: Strategy): string {
    if (strategy === 'short')
        return `突破此前 20 日高点，成交额放大 ${stock.volumeRatio.toFixed(2)} 倍，高于 MA20 ${stock.ma20Distance.toFixed(1)}%。`;
    if (strategy === 'swing')
        return `上升趋势中缩量回调，回调成交额比例 ${(stock.pullbackRatio * 100).toFixed(0)}%，当日收涨并站上 MA20。`;
    return `中长期均线保持多头排列并上行，收盘价高于 MA60 ${stock.ma60Distance.toFixed(1)}%。`;
}
