// 本文件验证真实接口客户端的会话撤销、空值、单位换算、固定快照与提交去重。
import test, { afterEach, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import * as api from '../src/api.ts';
const originalFetch = globalThis.fetch;
let calls: { url: string; options: RequestInit }[];
let replies: { status: number; body?: unknown; failure?: boolean }[];
const user = { id: 'user-test', username: 'admin', name: '测试用户', role: 'admin' as const, createdAt: '2026-10-30T08:00:00Z' };
const snapshot = { id: 'snapshot-test', dataDate: '2026-10-30', publishedAt: '2026-10-30T10:00:00Z', adjustment: 'qfq', strategyVersion: 'v1' };
function fixture() {
    return { stock: { stockId: 'SSE.600000', code: '600000', name: '测试股票', exchange: 'SSE', board: 'SSE_MAIN' as const, industry: null, concepts: [] }, quote: { price: 10, change: null, turnover: null, amount: 50000000, totalMarketCap: null }, strategies: ['short' as const], signal: { strategy: 'short' as const, reason: '后端入选理由', metrics: { amountMultiple: 2 } } };
}
beforeEach(async () => {
    globalThis.fetch = async () => new Response(null, { status: 204 }); await api.logout();
    calls = []; replies = [];
    globalThis.fetch = async (input, options = {}) => {
        calls.push({ url: String(input), options }); const response = replies.shift();
        assert.ok(response, '测试必须明确提供每个HTTP响应');
        if (response.failure) throw new TypeError('network unavailable');
        return new Response(response.status === 204 ? null : JSON.stringify(response.body), { status: response.status, headers: { 'Content-Type': 'application/json' } });
    };
});
afterEach(async () => { globalThis.fetch = async () => new Response(null, { status: 204 }); await api.logout(); globalThis.fetch = originalFetch; });

test('客户端只换算展示单位，保留缺失值与后端信号', () => {
    const stock = api.mapStock(fixture(), snapshot);
    assert.equal(stock.amount, 0.5); assert.equal(stock.totalMarketCap, null); assert.equal(stock.turnover, null);
    assert.equal(stock.industry, '未知行业'); assert.equal(stock.snapshotId, snapshot.id);
    assert.equal(stock.signals[0]?.reason, '后端入选理由'); assert.equal(api.formatNumber(stock.turnover), '—');
});
test('筛选与分页使用元口径，并沿用同一个快照', async () => {
    replies.push({ status: 200, body: { data: { snapshot, items: [fixture()], total: 101, page: 2, pageSize: 6, summary: { industryCount: 1, strategyCounts: { short: 101 } } } } });
    const result = await api.listStocks({ strategy: 'short', search: '', industry: '', concept: '', board: '', minTurnover: undefined, maxTurnover: undefined, minMarketCap: 10, maxMarketCap: undefined, sort: 'default', descending: true }, 2, 6, snapshot.id);
    const query = new URL(calls[0]!.url, 'http://test.invalid').searchParams;
    assert.equal(query.get('minMarketCap'), '1000000000'); assert.equal(query.get('snapshotId'), snapshot.id);
    assert.equal(query.get('page'), '2'); assert.equal(query.has('order'), false); assert.equal(query.has('industryId'), false);
    assert.equal(result.total, 101); assert.equal(result.items[0]?.stockId, 'SSE.600000');
});
test('登录后附带真实会话令牌，401清理本地会话', async () => {
    replies.push({ status: 200, body: { data: { accessToken: 'test-token-for-client', user } } }, { status: 401, body: { error: { code: 'TOKEN_EXPIRED', message: '会话已过期。' }, requestId: 'request-test' } });
    await api.login('admin', 'test-password'); assert.equal(api.currentUser()?.id, user.id);
    await assert.rejects(api.marketStatus(), (error: unknown) => error instanceof api.ApiError && error.status === 401 && error.requestId === 'request-test');
    assert.equal((calls[1]!.options.headers as Record<string, string>).Authorization, 'Bearer test-token-for-client');
    assert.equal(calls[1]!.options.redirect, 'error'); assert.equal(api.currentUser(), null);
});
test('退出通过后端撤销，会话不会仅由浏览器角色切换', async () => {
    replies.push({ status: 200, body: { data: { accessToken: 'test-token-for-client', user } } }, { status: 204 });
    await api.login('admin', 'test-password'); await api.logout();
    assert.equal(calls[1]!.url, '/api/v1/auth/logout'); assert.equal(calls[1]!.options.method, 'POST'); assert.equal(api.currentUser(), null);
});
test('分析历史跨快照读取，重复进行中冲突恢复原任务', async () => {
    const stock = api.mapStock(fixture(), snapshot);
    replies.push({ status: 200, body: { data: { items: [] } } }, { status: 409, body: { error: { code: 'ANALYSIS_IN_PROGRESS', message: '已有任务', details: { analysisId: 'existing' } } } }, { status: 200, body: { data: { id: 'existing', status: 'queued', simulated: true } } });
    await api.analysesFor(stock, 'fundamental', 'short');
    assert.equal(new URL(calls[0]!.url, 'http://test.invalid').searchParams.has('snapshotId'), false);
    const task = await api.startAnalysis(stock, 'fundamental', 'short', 'same-request-key');
    assert.equal(task.id, 'existing'); assert.equal((calls[1]!.options.headers as Record<string, string>)['Idempotency-Key'], 'same-request-key');
    const body = JSON.parse(String(calls[1]!.options.body)); assert.equal(body.snapshotId, snapshot.id); assert.equal('strategy' in body, false); assert.equal('scenario' in body, false);
});
test('同步重试沿用原任务接口并发送幂等标识，不创建新任务', async () => {
    replies.push({ status: 202, body: { data: { id: 'original-job', status: 'queued', stockProgress: { processedCount: 513, totalCount: 5222 } } } });
    const result = await api.retrySync('original-job', 'retry-original-key');
    assert.equal(calls[0]!.url, '/api/v1/admin/sync-jobs/original-job/retry');
    assert.deepEqual(JSON.parse(String(calls[0]!.options.body)), {});
    assert.equal((calls[0]!.options.headers as Record<string, string>)['Idempotency-Key'], 'retry-original-key');
    assert.equal(result.id, 'original-job'); assert.equal(result.stockProgress?.processedCount, 513);
});
test('K线与均线采用服务器返回值，不重新生成或计算', async () => {
    const stock = api.mapStock(fixture(), snapshot);
    replies.push({ status: 200, body: { data: { items: [{ time: snapshot.dataDate, open: 10, high: 11, low: 9, close: 10, volume: null, ma5: null, ma20: 9.5, isComplete: false }], hasMore: true, nextBefore: snapshot.dataDate } } });
    const result = await api.getCandles(stock, 'week'); assert.equal(result.items[0]?.ma5, null); assert.equal(result.items[0]?.ma20, 9.5); assert.equal(result.items[0]?.volume, null);
    const query = new URL(calls[0]!.url, 'http://test.invalid').searchParams; assert.equal(query.get('snapshotId'), snapshot.id); assert.equal(query.get('period'), 'week');
});
test('网络失败不自动重试或回退演示结果，同一key可由调用者重发', async () => {
    replies.push({ status: 200, failure: true }, { status: 200, body: { data: { id: 'accepted' } } });
    await assert.rejects(api.startSync('initialize', 'source', undefined, 'same-request-key'), (error: unknown) => error instanceof api.ApiError && error.code === 'NETWORK_ERROR');
    assert.equal(calls.length, 1);
    await api.startSync('initialize', 'source', undefined, 'same-request-key');
    assert.equal((calls[0]!.options.headers as Record<string, string>)['Idempotency-Key'], (calls[1]!.options.headers as Record<string, string>)['Idempotency-Key']);
});

test('旧账号的迟到401不会清除新账号会话', async () => {
    replies.push({ status: 200, body: { data: { accessToken: 'old-test-token', user } } });
    await api.login('admin', 'test-password');
    let finish: ((response: Response) => void) | undefined;
    globalThis.fetch = async (input) => {
        if (String(input).endsWith('/market/status')) return new Promise<Response>(resolve => { finish = resolve; });
        return new Response(JSON.stringify({ data: { accessToken: 'new-test-token', user: { ...user, id: 'new-user', username: 'reader', role: 'user' } } }), { status: 200 });
    };
    const old = api.marketStatus().catch(error => error);
    await api.login('reader', 'test-password');
    finish!(new Response(JSON.stringify({ error: { code: 'TOKEN_EXPIRED', message: '旧会话过期' } }), { status: 401 }));
    await old;
    assert.equal(api.currentUser()?.id, 'new-user');
});

test('通用买卖建议不发送伪策略，历史只读取通用模式', async () => {
    const stock = api.mapStock(fixture(), snapshot);
    replies.push({ status: 200, body: { data: { items: [] } } }, { status: 202, body: { data: { id: 'general-task', strategy: null } } });
    await api.analysesFor(stock, 'trade', 'general');
    assert.equal(new URL(calls[0]!.url, 'http://test.invalid').searchParams.get('strategy'), 'general');
    await api.startAnalysis(stock, 'trade', 'general', 'general-submit-key');
    const body = JSON.parse(String(calls[1]!.options.body));
    assert.equal(body.kind, 'trade'); assert.equal('strategy' in body, false);
});

test('局域网HTTP中缺少randomUUID仍可登录并提交带默认幂等编号的同步请求', async () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto')!;
    const native = globalThis.crypto;
    try {
        Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { getRandomValues: native.getRandomValues.bind(native) } });
        replies.push({ status: 200, body: { data: { accessToken: 'http-token-for-client', user } } }, { status: 202, body: { data: { id: 'http-sync-job' } } });
        await api.login('admin', 'test-password');
        await api.startSync('daily', 'baostock', '2026-10-30');
        for (const call of calls) assert.match((call.options.headers as Record<string, string>)['X-Request-ID']!, /^web_[0-9a-f-]{36}$/);
        assert.match((calls[1]!.options.headers as Record<string, string>)['Idempotency-Key']!, /^[0-9a-f-]{36}$/);
        assert.equal(api.currentUser()?.id, user.id);
    } finally { Object.defineProperty(globalThis, 'crypto', original); }
});
