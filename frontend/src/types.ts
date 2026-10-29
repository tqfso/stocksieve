// 本文件定义页面与模拟数据层共同使用的业务类型。
export type Strategy = 'short' | 'swing' | 'long';
export type Role = 'admin' | 'user';
export type Period = 'day' | 'week' | 'year';
export type AnalysisKind = 'fundamental' | 'chips' | 'trade';
export type Scenario = 'normal' | 'empty' | 'error';
export type Board = 'SSE_MAIN' | 'SZSE_MAIN' | 'CHINEXT' | 'STAR' | 'BSE';
export interface User {
    id: string;
    username: string;
    name: string;
    role: Role;
    createdAt: string;
}
export interface Stock {
    code: string;
    name: string;
    exchange: '上海' | '深圳' | '北京';
    board: Board;
    industry: string;
    concepts: string[];
    strategies: Strategy[];
    price: number;
    change: number;
    turnover: number;
    amount: number;
    /** 模拟展示模型的总市值，单位亿元；缺失时不按零值处理。 */
    totalMarketCap: number | null;
    volumeRatio: number;
    ma20Distance: number;
    ma60Distance: number;
    pullbackRatio: number;
    reference: number;
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
/** 完整模拟报告的内容结构，预览与保存后的报告共用。 */
export interface ReportContent {
    sampleVersion: 2;
    title: string;
    dataDate: string;
    strategy: Strategy;
    metrics: { label: string; value: string; detail: string }[];
    summary: string;
    sections: {
        title: string;
        text: string;
    }[];
    sources: string[];
}
export interface Report extends ReportContent {
    id: string;
    userId: string;
    code: string;
    kind: AnalysisKind;
    createdAt: string;
}
export interface SyncJob {
    id: string;
    source: 'Sina' | 'Baostock';
    kind: 'initialize' | 'daily';
    status: 'running' | 'success' | 'failed';
    progress: number;
    createdAt: string;
    count: number;
    logs: string[];
}
