// 本文件定义页面与模拟数据层共同使用的业务类型。
export type Strategy = 'short' | 'swing' | 'long' | 'trend_wait' | 'base_wait';
export type Role = 'admin' | 'user';
export type Period = 'day' | 'week' | 'year';
export type AnalysisKind = 'fundamental' | 'chips' | 'trade';
export type AnalysisScenario = 'normal' | 'partial' | 'failed';
export type AnalysisStage = 'queued' | 'researching' | 'analyzing' | 'writing' | 'completed';
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
    /** 等待策略的固定区间及量价样例，供列表、详情与报告复用。 */
    waiting?: {
        lower: number;
        upper: number;
        amountRatio: number;
        position120: number;
    };
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
export interface ChipDistribution {
    currentPrice: number;
    bins: ChipBin[];
    averageCost: number;
    profitableRatio: number;
    cost70: [number, number];
    cost90: [number, number];
    support: ChipBin;
    resistance: ChipBin;
}
/** 完整模拟报告的内容结构，预览与保存后的报告共用。 */
export interface ReportContent {
    sampleVersion: 4;
    title: string;
    dataDate: string;
    strategy: Strategy;
    overview: ReportOverview;
    metrics: { label: string; value: string; detail: string }[];
    summary: string;
    sections: {
        title: string;
        text: string;
    }[];
    sources: ReportSource[];
    materialsAsOfAt: string;
    dataQuality: 'complete' | 'partial';
    missingInformation: string[];
    chipDistribution?: ChipDistribution;
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
/** 来源保留实际可展示的链接和时间；模拟链接只指向公开入口，不冒充公告原文。 */
export interface ReportSource {
    title: string;
    url: string | null;
    publishedAt: string | null;
    fetchedAt: string;
}
export interface Report extends ReportContent {
    id: string;
    userId: string;
    code: string;
    kind: AnalysisKind;
    createdAt: string;
}
/** 分析任务独立于页面存活，报告成功后以 reportId 关联历史正文。 */
export interface AnalysisTask {
    id: string;
    code: string;
    kind: AnalysisKind;
    strategy: Strategy | null;
    snapshotId: string;
    dataDate: string;
    status: 'queued' | 'running' | 'succeeded' | 'failed';
    stage: AnalysisStage;
    progress: number;
    createdAt: string;
    startedAt: string | null;
    finishedAt: string | null;
    error: { code: string; message: string } | null;
    reportId: string | null;
}
export interface SyncJob {
    id: string;
    source: 'Sina' | 'Baostock' | '分类样例';
    kind: 'initialize' | 'catalog' | 'daily';
    status: 'running' | 'success' | 'failed';
    progress: number;
    createdAt: string;
    count: number;
    logs: string[];
}
