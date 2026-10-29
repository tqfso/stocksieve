// 本文件定义页面与模拟数据层共同使用的业务类型。
export type Strategy = 'short' | 'swing' | 'long';
export type Role = 'admin' | 'user';
export type Period = 'day' | 'week' | 'year';
export type AnalysisKind = 'fundamental' | 'chips' | 'trade';
export type Scenario = 'normal' | 'empty' | 'error';
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
    industry: string;
    concepts: string[];
    strategies: Strategy[];
    price: number;
    change: number;
    turnover: number;
    amount: number;
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
    exchange: string;
    minTurnover: number | undefined;
    maxTurnover: number | undefined;
    sort: 'default' | 'change' | 'turnover' | 'amount';
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
export interface Report {
    id: string;
    userId: string;
    code: string;
    kind: AnalysisKind;
    createdAt: string;
    summary: string;
    sections: {
        title: string;
        text: string;
    }[];
    sources: string[];
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
