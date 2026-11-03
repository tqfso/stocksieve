// 本文件通过真实HTTP接口提供会话、行情、后台任务与分析报告，不生成浏览器业务数据。
import { randomId } from './ids.ts';
import type { AnalysisStage, Board, Candle, ChipBin, Period, ReportOverview, ReportSource, StockQuery, Strategy, User } from './types.ts';

export interface Snapshot { id: string; dataDate: string; publishedAt: string; adjustment: string; strategyVersion: string; boards?: Board[]; catalogAsOfAt?: string | null }
export interface Source { id: string; name: string; available: boolean; supportedSyncKinds: SyncJob['kind'][] }
export interface MarketStatus {
    initialized: boolean; stockUpdatedAt: string | null; catalogUpdatedAt: string | null;
    latestTradingDate: string | null; publishedSnapshot: Snapshot | null;
    marketBoards?: Board[]; defaultSource?: string; analysisMode?: 'mock' | 'live';
    coverage: { totalStocks: number; byExchange: Record<string, number> }; sources: Source[];
}
export interface Signal { strategy: Strategy; reason: string; metrics: Record<string, number | null>; conditionResults?: { id: string; passed: boolean }[]; invalidation?: unknown }
interface Quote { price: number | null; change: number | null; turnover: number | null; amount: number | null; totalMarketCap: number | null }
interface StockResponse {
    stock: { stockId: string; code: string; name: string; exchange: string; board: Board | null; industry: { id: string; name: string } | null; concepts: { id: string; name: string }[] };
    quote: Quote | null; strategies?: Strategy[]; signals?: Signal[]; signal?: Signal; snapshot?: Snapshot;
}
export interface LiveStock {
    stockId: string; snapshotId: string; dataDate: string; code: string; name: string; exchange: string; board: Board | null;
    industry: string; concepts: string[]; strategies: Strategy[]; signals: Signal[];
    price: number | null; change: number | null; turnover: number | null; amount: number | null; totalMarketCap: number | null;
}
export interface Filters { snapshotId: string; boards: { id: Board; name: string; exchange: string }[]; industries: { id: string; name: string }[]; concepts: { id: string; name: string }[]; coverage: Record<string, string> }
export interface StockMatch { stockId: string; code: string; name: string; exchange: string; board: Board; dataStatus: 'ready' | 'pending'; dataDate: string | null }
export interface StockSearchPage { items: StockMatch[]; total: number; page: number; pageSize: number }
export interface OpportunityPage { snapshot: Snapshot; items: LiveStock[]; total: number; page: number; pageSize: number; summary: { industryCount: number; strategyCounts: Record<Strategy, number> } }
export interface SyncJob {
    id: string; kind: 'initialize' | 'catalog' | 'daily'; source: string; status: 'queued' | 'running' | 'succeeded' | 'failed';
    progress: number; dataDate: string | null; createdAt: string; waitReason: string | null; nextRetryAt: string | null;
    error: { code: string; message: string } | null; result: { totalCount: number; processedCount: number; publishedSnapshotId: string | null } | null;
    message?: string | null;
    historyBackfill?: boolean;
    stockProgress?: { processedCount: number; totalCount: number } | null;
    phase?: 'checking' | 'waiting_data' | 'syncing' | 'calculating' | null;
    waitMessage?: string | null;
    precheck?: { checkedAt: string; boards: { board: Board; status: 'ready' | 'pending' | 'market_closed' | 'unknown' | 'unsupported'; latestDataDate: string | null; message: string | null }[] } | null;
}
export interface AnalysisTask {
    id: string; stockId: string; kind: 'report'; strategy: Strategy | null; snapshotId: string; dataDate: string;
    status: SyncJob['status']; stage: AnalysisStage; progress: number; createdAt: string; simulated: boolean;
    error: { code: string; message: string } | null; report?: LiveReport | null;
}
export interface LiveReport {
    title: string; overview: ReportOverview; summary: string; metrics: { label: string; value: string; detail: string }[];
    sections: { id: string; title: string; headline: string; text: string; sourceIds: string[] }[]; sources: ReportSource[]; simulated: boolean;
    dataQuality: 'complete' | 'partial'; missingInformation: string[]; materialsAsOfAt: string; generatedAt: string;
    stock: { code: string; name: string; board: Board | null; industry: { name: string } | null };
    snapshotId: string; dataDate: string; researchStatus: 'not_searched' | 'researched';
    shareholders?: Shareholders;
    valuation: { basis: string; companyPe: number | null; industryPe: number | null; industry: string | null; companyDataDate: string; industryDataDate: string | null; industryClassification: string; industryStatistic: string | null; explanation: string };
}
export interface ServerCandle extends Omit<Candle, 'volume'> { volume: number | null; ma5: number | null; ma10: number | null; ma20: number | null; ma60: number | null; ma120: number | null; isComplete: boolean }
export interface CandlePage { items: ServerCandle[]; hasMore: boolean; nextBefore: string | null }

export class ApiError extends Error {
    readonly code: string; readonly status: number; readonly details: Record<string, unknown>; readonly requestId?: string;
    constructor(message: string, code: string, status: number, details: Record<string, unknown> = {}, requestId?: string) { super(message); this.code = code; this.status = status; this.details = details; this.requestId = requestId; }
}
interface Session { accessToken: string; user: User }
const SESSION_KEY = 'stocksieve.auth.v1';
let session: Session | null | undefined;
let restored: Promise<User | null> | undefined;

/** 只保存当前浏览器会话的令牌；业务记录与口令均由服务器管理。 */
function readSession(): Session | null {
    if (session !== undefined) return session;
    try {
        const parsed = JSON.parse(globalThis.sessionStorage?.getItem(SESSION_KEY) ?? 'null') as Session | null;
        session = parsed && typeof parsed.accessToken === 'string' && typeof parsed.user?.id === 'string' ? parsed : null;
    } catch { session = null; }
    return session;
}
function saveSession(value: Session | null): void {
    session = value;
    try { if (value) globalThis.sessionStorage?.setItem(SESSION_KEY, JSON.stringify(value)); else globalThis.sessionStorage?.removeItem(SESSION_KEY); } catch { /* 存储受限时使用本次内存会话。 */ }
}
export function currentUser(): User | null { return readSession()?.user ?? null; }
export function params(values: Record<string, unknown>): string {
    const result = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== null && value !== '') result.set(key, String(value));
    const text = result.toString();
    return text ? '?' + text : '';
}

/** 统一处理响应、超时与401，不对失败请求自动重试或自动生成数据。 */
export async function request<T>(path: string, method = 'GET', body?: unknown, key?: string): Promise<T> {
    const headers: Record<string, string> = { 'X-Request-ID': 'web_' + randomId() };
    const token = readSession()?.accessToken;
    if (token) headers.Authorization = 'Bearer ' + token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (key) headers['Idempotency-Key'] = key;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
        const response = await fetch('/api/v1' + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, redirect: 'error' });
        if (response.status === 204) return undefined as T;
        const result = await response.json().catch(() => { throw new ApiError('服务未返回有效响应，请稍后重试。', 'INVALID_RESPONSE', response.status); });
        if (!response.ok) {
            if (response.status === 401 && path !== '/auth/login' && readSession()?.accessToken === token) {
                saveSession(null); restored = undefined;
                if (typeof window !== 'undefined') window.dispatchEvent(new Event('stocksieve:session-expired'));
            }
            throw new ApiError(result.error?.message ?? '请求未成功。', result.error?.code ?? 'HTTP_ERROR', response.status, result.error?.details ?? {}, result.requestId);
        }
        return result.data as T;
    } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError(controller.signal.aborted ? '请求超时，请重试。' : '无法连接后端服务，请稍后重试。', 'NETWORK_ERROR', 0);
    } finally { clearTimeout(timer); }
}
export async function login(username: string, password: string): Promise<User> {
    const result = await request<Session>('/auth/login', 'POST', { username, password });
    saveSession(result); restored = Promise.resolve(result.user); return result.user;
}
export async function restoreSession(): Promise<User | null> {
    if (!readSession()) return null;
    const token = readSession()!.accessToken;
    return restored ??= request<User>('/auth/me').then(user => {
        if (readSession()?.accessToken !== token) return currentUser();
        saveSession({ accessToken: token, user }); return user;
    }).catch(error => { if (readSession()?.accessToken === token) restored = undefined; throw error; });
}
export async function logout(): Promise<void> {
    const token = readSession()?.accessToken;
    try { if (token) await request('/auth/logout', 'POST'); }
    finally { if (readSession()?.accessToken === token) { saveSession(null); restored = undefined; } }
}
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
    await request('/auth/password', 'PUT', { oldPassword, newPassword }); saveSession(null); restored = undefined;
}
export async function listUsers(): Promise<User[]> { return (await request<{ items: User[] }>('/admin/users?pageSize=100')).items; }
export function addUser(username: string, name: string, password: string): Promise<User> { return request('/admin/users', 'POST', { username, name, password }); }
export function marketStatus(): Promise<MarketStatus> { return request('/market/status'); }
export function searchStocks(search: string, page = 1): Promise<StockSearchPage> { return request('/stocks' + params({ search, page, pageSize: 20 })); }
export function filters(snapshotId: string): Promise<Filters> { return request('/catalog/filters' + params({ snapshotId })); }

/** 价格以元、成交额/市值以亿元展示；空值保持null，分类未知明确显示。 */
export function mapStock(row: StockResponse, snapshot: Snapshot): LiveStock {
    const signals = row.signals ?? (row.signal ? [row.signal] : []);
    return { ...row.stock, snapshotId: snapshot.id, dataDate: snapshot.dataDate,
        industry: row.stock.industry?.name ?? '未知行业', concepts: row.stock.concepts.map(item => item.name),
        strategies: row.strategies ?? signals.map(item => item.strategy), signals,
        price: row.quote?.price ?? null, change: row.quote?.change ?? null, turnover: row.quote?.turnover ?? null,
        amount: row.quote?.amount == null ? null : row.quote.amount / 1e8,
        totalMarketCap: row.quote?.totalMarketCap == null ? null : row.quote.totalMarketCap / 1e8 };
}
export async function listStocks(query: StockQuery, page: number, pageSize: number, snapshotId?: string): Promise<OpportunityPage> {
    const raw = await request<Omit<OpportunityPage, 'items'> & { items: StockResponse[] }>('/opportunities' + params({
        strategy: query.strategy, search: query.search, industryId: query.industry, conceptId: query.concept, board: query.board,
        minTurnover: query.minTurnover, maxTurnover: query.maxTurnover,
        minMarketCap: query.minMarketCap === undefined ? undefined : query.minMarketCap * 1e8,
        maxMarketCap: query.maxMarketCap === undefined ? undefined : query.maxMarketCap * 1e8,
        sort: query.sort, order: query.sort === 'default' ? undefined : query.descending ? 'desc' : 'asc', page, pageSize, snapshotId,
    }));
    return { ...raw, items: raw.items.map(item => mapStock(item, raw.snapshot)) };
}
export async function getStock(stockId: string, snapshotId?: string): Promise<LiveStock> {
    const result = await request<StockResponse & { snapshot: Snapshot }>('/stocks/' + encodeURIComponent(stockId) + params({ snapshotId }));
    return mapStock(result, result.snapshot);
}
export function getCandles(stock: LiveStock, period: Period, before?: string): Promise<CandlePage> { return request('/stocks/' + encodeURIComponent(stock.stockId) + '/candles' + params({ snapshotId: stock.snapshotId, period, before, limit: 1000 })); }
export async function syncJobs(): Promise<SyncJob[]> { return (await request<{ items: SyncJob[] }>('/admin/sync-jobs?pageSize=100')).items; }
export function startSync(kind: SyncJob['kind'], source: string, dataDate?: string, key: string = randomId()): Promise<SyncJob> { return request('/admin/sync-jobs', 'POST', { kind, source, ...(dataDate ? { dataDate } : {}) }, key); }
export function retrySync(id: string, key: string = randomId()): Promise<SyncJob> { return request('/admin/sync-jobs/' + encodeURIComponent(id) + '/retry', 'POST', {}, key); }
export async function syncLogs(id: string): Promise<string[]> {
    let after: string | undefined; const output: string[] = [];
    do {
        const page = await request<{ items: { time: string; message: string }[]; hasMore: boolean; nextCursor: string | null }>('/admin/sync-jobs/' + encodeURIComponent(id) + '/logs' + params({ after, limit: 100 }));
        output.push(...page.items.map(row => `${row.time} ${row.message}`));
        after = page.hasMore ? page.nextCursor ?? undefined : undefined;
    } while (after && output.length < 1000);
    return output;
}
export async function analysesFor(stock: LiveStock): Promise<AnalysisTask[]> { return (await request<{ items: AnalysisTask[] }>('/analyses' + params({ stockId: stock.stockId, pageSize: 100 }))).items; }
export function getAnalysis(id: string): Promise<AnalysisTask> { return request('/analyses/' + encodeURIComponent(id)); }
export async function startAnalysis(stock: LiveStock, key: string): Promise<AnalysisTask> {
    try { return await request('/stocks/' + encodeURIComponent(stock.stockId) + '/analyses', 'POST', { snapshotId: stock.snapshotId }, key); }
    catch (error) { if (error instanceof ApiError && error.code === 'ANALYSIS_IN_PROGRESS' && typeof error.details.analysisId === 'string') return getAnalysis(error.details.analysisId); throw error; }
}
export interface ChipDistribution { currentPrice: number; bins: ChipBin[]; averageCost: number; profitableRatio: number; cost70: [number, number]; cost90: [number, number]; support: ChipBin | null; resistance: ChipBin | null }
export interface Shareholders { status: 'unavailable'; latestCount: number | null; previousCount: number | null; changePercent: number | null; asOf: string | null; previousAsOf: string | null; publishedAt: string | null; sourceUrl: string | null; trend: 'unknown'; trendLabel: string; explanation: string }
export interface ChipsResult { stockId: string; snapshotId: string; dataDate: string; distribution: ChipDistribution | null; method: string; missingInformation: string[] }

export function getChips(stock: LiveStock): Promise<ChipsResult> { return request('/stocks/' + encodeURIComponent(stock.stockId) + '/chips' + params({ snapshotId: stock.snapshotId })); }

/** 通过鉴权下载当前选中的已保存报告，令牌不放入URL。 */
export async function downloadReport(id: string, format: 'docx' | 'pdf'): Promise<void> {
    const token = readSession()?.accessToken;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
        const response = await fetch('/api/v1/analyses/' + encodeURIComponent(id) + '/export' + params({ format }), {
            headers: { ...(token ? { Authorization: 'Bearer ' + token } : {}), 'X-Request-ID': 'web_' + randomId() },
            signal: controller.signal, redirect: 'error',
        });
        if (!response.ok) {
            if (response.status === 401 && readSession()?.accessToken === token) {
                saveSession(null); restored = undefined;
                window.dispatchEvent(new Event('stocksieve:session-expired'));
            }
            const result = await response.json().catch(() => ({}));
            throw new ApiError(result.error?.message ?? '报告下载失败，请重试。', result.error?.code ?? 'HTTP_ERROR', response.status);
        }
        const file = await response.blob();
        const url = URL.createObjectURL(file);
        const anchor = document.createElement('a');
        anchor.href = url;
        const name = response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
        anchor.download = name ? decodeURIComponent(name) : `StockSieve报告.${format}`;
        document.body.append(anchor); anchor.click(); anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError(controller.signal.aborted ? '导出超时，请重试。' : '下载失败，请检查连接后重试。', 'NETWORK_ERROR', 0);
    } finally { clearTimeout(timer); }
}
export function formatNumber(value: number | null | undefined, digits = 2): string { return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits); }
export function quoteClass(value: number | null): string { return value == null ? 'muted' : value >= 0 ? 'rise' : 'fall'; }
export function formatTime(value: string | null | undefined): string { return value ? new Date(value).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'; }
