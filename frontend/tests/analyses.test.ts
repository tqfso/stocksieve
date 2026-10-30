// 本文件验证分析任务的阶段、刷新恢复、幂等、资料缺失和历史保留，不访问模型或网络。
import test from 'node:test';
import assert from 'node:assert/strict';
import * as api from '../src/mock/service.ts';
import { STOCKS } from '../src/mock/fixtures.ts';
import { ANALYSIS_DURATION } from '../src/mock/analyses.ts';
const storage = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', { value: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
} });
const stock = STOCKS[0]!;

test('分析任务专项流程', async (t) => {
    t.beforeEach(() => api.resetDemo());
    await t.test('阶段跟随受理时间恢复，查询只结算一次报告', async (t) => {
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        await api.login('admin', 'demo1234');
        const task = api.startAnalysis(stock, 'fundamental');
        assert.equal(task.status, 'queued');
        assert.equal(task.stage, 'queued');
        assert.equal(task.reportId, null);
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 0);
        t.mock.timers.tick(2000);
        assert.equal(api.getAnalysis(task.id).stage, 'researching');
        t.mock.timers.tick(4000);
        assert.equal(api.getAnalysis(task.id).stage, 'analyzing');
        t.mock.timers.tick(3500);
        assert.equal(api.getAnalysis(task.id).stage, 'writing');
        t.mock.timers.tick(2500);
        assert.equal(api.getAnalysis(task.id).stage, 'completed');
        assert.equal(api.getAnalysis(task.id).progress, 100);
        assert.equal(api.getAnalysis(task.id).reportId, task.id);
        for (let index = 0; index < 4; index++) api.analysesFor(stock.code, 'fundamental');
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 1);
    });
    await t.test('原 key 重发与进行中重复点击复用任务，同 key 更改内容拒绝', async (t) => {
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        await api.login('admin', 'demo1234');
        const task = api.startAnalysis(stock, 'fundamental', { idempotencyKey: 'request-one' });
        assert.equal(api.startAnalysis(stock, 'fundamental', { idempotencyKey: 'request-one' }).id, task.id);
        assert.equal(api.startAnalysis(stock, 'fundamental', { idempotencyKey: 'request-two' }).id, task.id);
        assert.throws(() => api.startAnalysis(stock, 'chips', { idempotencyKey: 'request-one' }), /提交标识/);
        t.mock.timers.tick(ANALYSIS_DURATION);
        assert.equal(api.startAnalysis(stock, 'fundamental', { idempotencyKey: 'request-one' }).id, task.id);
        assert.equal(api.analysesFor(stock.code, 'fundamental').length, 1);
    });
    await t.test('资料不完整仍成功，缺失说明保存在当前报告且不污染预览', async (t) => {
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        await api.login('admin', 'demo1234');
        const task = api.startAnalysis(stock, 'fundamental', { scenario: 'partial' });
        t.mock.timers.tick(ANALYSIS_DURATION);
        assert.equal(api.getAnalysis(task.id).status, 'succeeded');
        const report = api.reportsFor(stock.code, 'fundamental')[0]!;
        assert.equal(report.dataQuality, 'partial');
        assert.ok(report.missingInformation.length);
        assert.match(report.overview.risks[0]!, /无法确认减持风险/);
        assert.equal(report.overview.decision.action, 'wait');
        assert.doesNotMatch(report.overview.risks[0]!, /计划尚未结束/);
        assert.match(report.summary, /有限结论/);
        assert.equal(report.dataDate, task.dataDate);
        assert.ok(report.materialsAsOfAt.slice(0, 10) > report.dataDate);
        const complete = api.startAnalysis(stock, 'fundamental');
        t.mock.timers.tick(ANALYSIS_DURATION);
        assert.equal(api.getAnalysis(complete.id).status, 'succeeded');
        assert.equal(api.reportsFor(stock.code, 'fundamental')[0]?.dataQuality, 'complete');
        assert.equal(api.reportsFor(stock.code, 'fundamental')[1]?.dataQuality, 'partial');
    });
    await t.test('新任务失败保留旧报告，新 key 重试保留失败记录', async (t) => {
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        await api.login('admin', 'demo1234');
        const successful = api.startAnalysis(stock, 'chips');
        t.mock.timers.tick(ANALYSIS_DURATION);
        api.getAnalysis(successful.id);
        const failed = api.startAnalysis(stock, 'chips', { scenario: 'failed' });
        t.mock.timers.tick(6000);
        const failedResult = api.getAnalysis(failed.id);
        assert.equal(failedResult.status, 'failed');
        assert.equal(failedResult.reportId, null);
        assert.ok(failedResult.error?.message);
        assert.equal(failedResult.error?.code, 'ANALYSIS_TIMEOUT');
        assert.equal(api.reportsFor(stock.code, 'chips')[0]?.id, successful.id);
        const retried = api.startAnalysis(stock, 'chips');
        assert.notEqual(retried.id, failed.id);
        t.mock.timers.tick(ANALYSIS_DURATION);
        assert.equal(api.getAnalysis(retried.id).status, 'succeeded');
        assert.equal(api.getAnalysis(failed.id).status, 'failed');
        assert.equal(api.reportsFor(stock.code, 'chips').length, 2);
    });
    await t.test('公告和新闻未核验不阻断买卖建议，技术价位保持原口径', async (t) => {
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        await api.login('admin', 'demo1234');
        const candidate = STOCKS.find((item) => item.strategies.includes('short'))!;
        const task = api.startAnalysis(candidate, 'trade', { strategy: 'short', scenario: 'partial' });
        t.mock.timers.tick(ANALYSIS_DURATION);
        assert.equal(api.getAnalysis(task.id).status, 'succeeded');
        const result = api.reportsFor(candidate.code, 'trade', 'short')[0]!;
        assert.ok(result.overview.pricePlan);
        assert.match(result.missingInformation[0]!, /不影响.*技术分析/);
    });
    await t.test('重新加载模块从本地存储恢复同一任务，不额外提交', async (t) => {
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        await api.login('admin', 'demo1234');
        const task = api.startAnalysis(stock, 'fundamental');
        t.mock.timers.tick(3000);
        const modulePath = `../src/mock/service.ts?reload=${task.id}`;
        const restored: typeof api = await import(modulePath);
        assert.equal(restored.analysesFor(stock.code, 'fundamental')[0]?.id, task.id);
        assert.equal(restored.getAnalysis(task.id).stage, 'researching');
        t.mock.timers.tick(ANALYSIS_DURATION);
        assert.equal(restored.getAnalysis(task.id).status, 'succeeded');
        assert.equal(restored.reportsFor(stock.code, 'fundamental').length, 1);
    });
    await t.test('退出不取消分析，其他账号不能读取任务或报告', async (t) => {
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        await api.login('admin', 'demo1234');
        const task = api.startAnalysis(stock, 'fundamental');
        api.logout();
        await api.login('viewer', 'demo1234');
        assert.equal(api.analysesFor(stock.code, 'fundamental').length, 0);
        assert.throws(() => api.getAnalysis(task.id), /不存在/);
        t.mock.timers.tick(ANALYSIS_DURATION);
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 0);
        api.logout();
        await api.login('admin', 'demo1234');
        assert.equal(api.getAnalysis(task.id).status, 'succeeded');
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 1);
        assert.equal('scenario' in api.getAnalysis(task.id), false);
        assert.equal('input' in api.getAnalysis(task.id), false);
    });
});
