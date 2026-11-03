// 本文件定义页面与服务端接口共用的业务类型。
export type Strategy = 'short' | 'swing' | 'long' | 'trend_wait' | 'base_wait';
export type Role = 'admin' | 'user';
export type Period = 'day' | 'week' | 'year';
export type AnalysisKind = 'fundamental' | 'chips' | 'trade';
export type AnalysisStage = 'queued' | 'preparing' | 'researching' | 'analyzing' | 'writing' | 'completed';
export type Board = 'SSE_MAIN' | 'SZSE_MAIN' | 'CHINEXT' | 'STAR' | 'BSE';
export interface User {
    id: string;
    username: string;
    name: string;
    role: Role;
    createdAt: string;
}
export interface StockQuery {
    strategy: Strategy;
    search: string;
    industry: string;
    concept: string;
    board: Board | '';
    minTurnover: number | undefined;
    maxTurnover: number | undefined;
    /** 总市值筛选区间，单位亿元，与页面选项保持一致。 */
    minMarketCap: number | undefined;
    maxMarketCap: number | undefined;
    sort: 'default' | 'change' | 'turnover' | 'amount' | 'totalMarketCap';
    descending: boolean;
}
export interface Candle {
    time: string;
    open: number;
    close: number;
    high: number;
    low: number;
    volume: number;
}
/** 筹码档位表示一个成本区间的估算占比，不能当作某价位的真实持股清单。 */
export interface ChipBin {
    lower: number;
    upper: number;
    price: number;
    ratio: number;
}
/** 默认阅读层展示简短机会、风险和有条件的参考价位。 */
export interface ReportOverview {
    decision: { action: 'consider' | 'wait' | 'avoid'; label: string; reason: string };
    opportunities: string[];
    risks: string[];
    pricePlan?: {
        waiting: boolean;
        entryLabel: string;
        entryPrice: string;
        entryCondition: string;
        exitPrice: string;
        exitCondition: string;
        considerIf: string;
        avoidIf: string;
    };
}
/** 来源保留实际可展示的链接和时间；未知链接保持为空。 */
export interface ReportSource {
    title: string;
    url: string | null;
    publishedAt: string | null;
    fetchedAt: string;
}
