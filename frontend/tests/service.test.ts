// 本文件验证模拟账号权限、报告隔离、密码修改和同步任务的完整业务流程。
import test from 'node:test';
import assert from 'node:assert/strict';
import * as api from '../src/mock/service.ts';
import { STOCKS } from '../src/mock/fixtures.ts';
import { createReportContent } from '../src/mock/reports.ts';
const storage = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', { value: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => { storage.set(key, value); },
    } });
test('模拟业务流程', async (t) => {
    t.beforeEach(() => { api.resetDemo(); });
    await t.test('登录校验、新增用户、重复账号校验与角色限制', async () => {
        await assert.rejects(api.login('admin', 'wrong'), /账号或密码错误/);
        await api.login('admin', 'demo1234');
        const user = await api.addUser('reviewer', '评审用户', 'sample123');
        assert.equal(user.role, 'user');
        assert.equal('password' in user, false);
        await assert.rejects(api.addUser('reviewer', '重复用户', 'sample123'), /已存在/);
        await assert.rejects(api.addUser('x', '用户', 'sample123'), /3～20/);
        api.logout();
        await api.login('reviewer', 'sample123');
        assert.throws(api.listUsers, /权限/);
        assert.throws(() => api.startSync('initialize'), /权限/);
        assert.throws(() => api.startSync('catalog'), /权限/);
        await assert.rejects(api.changePassword('sample123', 'newpass123'), /权限/);
    });
    await t.test('管理员修改密码后退出，旧密码失效且新密码可用', async () => {
        await api.login('admin', 'demo1234');
        await assert.rejects(api.changePassword('wrong', 'newpass123'), /当前密码不正确/);
        await api.changePassword('demo1234', 'newpass123');
        assert.equal(api.currentUser(), null);
        await assert.rejects(api.login('admin', 'demo1234'), /账号或密码错误/);
        await api.login('admin', 'newpass123');
        assert.equal(api.currentUser()?.role, 'admin');
    });
    await t.test('报告生成失败不新增记录，成功报告按账号隔离', async () => {
        await api.login('admin', 'demo1234');
        const stock = STOCKS[0]!;
        await assert.rejects(api.analyze(stock, 'fundamental', true), /超时/);
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 0);
        const preview = createReportContent(stock, 'fundamental');
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 0);
        const generated = await api.analyze(stock, 'fundamental', false);
        assert.equal(generated.summary, preview.summary);
        assert.deepEqual(generated.metrics, preview.metrics);
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 1);
        api.logout();
        await api.login('viewer', 'demo1234');
        assert.equal(api.reportsFor(stock.code, 'fundamental').length, 0);
        await api.analyze(stock, 'chips', false);
        assert.equal(api.reportsFor(stock.code, 'chips').length, 1);
        api.logout();
        await api.login('admin', 'demo1234');
        assert.equal(api.reportsFor(stock.code, 'chips').length, 0);
    });
    await t.test('同一股票的买卖条件历史按策略分别读取', async () => {
        await api.login('admin', 'demo1234');
        const stock = STOCKS.find((item) => item.code === '300750')!;
        await api.analyze(stock, 'trade', false, 'short');
        await api.analyze(stock, 'trade', false, 'long');
        assert.equal(api.reportsFor(stock.code, 'trade', 'short').length, 1);
        assert.equal(api.reportsFor(stock.code, 'trade', 'long').length, 1);
        assert.equal(api.reportsFor(stock.code, 'trade', 'short')[0]?.strategy, 'short');
        assert.equal(api.reportsFor(stock.code, 'trade', 'long')[0]?.strategy, 'long');
    });
    await t.test('初始化前置、重复任务阻止、失败重试与成功结果', async () => {
        await api.login('admin', 'demo1234');
        assert.throws(() => api.startSync('daily'), /先初始化/);
        assert.throws(() => api.startSync('catalog'), /先初始化/);
        const failed = api.startSync('initialize', 'Baostock');
        assert.throws(() => api.startSync('initialize'), /正在运行/);
        for (let i = 0; i < 3; i++)
            api.advanceSync(failed.id, true);
        assert.equal(api.syncSnapshot().jobs[0]?.status, 'failed');
        assert.equal(api.syncSnapshot().initialized, false);
        const successful = api.startSync('initialize');
        for (let i = 0; i < 5; i++)
            api.advanceSync(successful.id, false);
        assert.equal(api.syncSnapshot().initialized, true);
        assert.equal(api.syncSnapshot().catalogUpdatedAt, null);
        assert.equal(api.syncSnapshot().jobs[0]?.count, 5348);
        const daily = api.startSync('daily');
        for (let i = 0; i < 5; i++)
            api.advanceSync(daily.id, false);
        assert.equal(api.syncSnapshot().jobs[0]?.status, 'success');
    });
    await t.test('手动刷新基础数据失败时保留已成功的初始化状态，允许继续同步行情', async () => {
        await api.login('admin', 'demo1234');
        const initial = api.startSync('initialize');
        for (let i = 0; i < 5; i++) api.advanceSync(initial.id, false);
        const refresh = api.startSync('initialize');
        for (let i = 0; i < 3; i++) api.advanceSync(refresh.id, true);
        const snapshot = api.syncSnapshot();
        assert.equal(snapshot.initialized, true);
        assert.equal(snapshot.jobs.find((job) => job.id === refresh.id)?.status, 'failed');
        assert.equal(snapshot.jobs.find((job) => job.id === initial.id)?.status, 'success');
        const daily = api.startSync('daily');
        for (let i = 0; i < 5; i++) api.advanceSync(daily.id, false);
        assert.equal(api.syncSnapshot().jobs.find((job) => job.id === daily.id)?.status, 'success');
    });
    await t.test('股票、分类和行情任务隔离成功时间，失败不覆盖原状态', async (t) => {
        await api.login('admin', 'demo1234');
        t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 30, 8) });
        const complete = (kind: Parameters<typeof api.startSync>[0]): void => {
            const job = api.startSync(kind);
            for (let i = 0; i < 5; i++) api.advanceSync(job.id, false);
        };
        complete('initialize');
        const firstStockTime = api.syncSnapshot().stockUpdatedAt;
        assert.ok(firstStockTime);
        assert.equal(api.syncSnapshot().catalogUpdatedAt, null);
        t.mock.timers.tick(1000);
        complete('catalog');
        const catalogTime = api.syncSnapshot().catalogUpdatedAt;
        assert.ok(catalogTime);
        assert.notEqual(catalogTime, firstStockTime);
        assert.equal(api.syncSnapshot().stockUpdatedAt, firstStockTime);
        assert.equal(api.syncSnapshot().jobs[0]?.source, '分类样例');
        t.mock.timers.tick(1000);
        complete('initialize');
        const stockTime = api.syncSnapshot().stockUpdatedAt;
        assert.notEqual(stockTime, firstStockTime);
        assert.equal(api.syncSnapshot().catalogUpdatedAt, catalogTime);
        t.mock.timers.tick(1000);
        const failed = api.startSync('catalog');
        assert.throws(() => api.startSync('daily'), /正在运行/);
        for (let i = 0; i < 3; i++) api.advanceSync(failed.id, true);
        assert.equal(api.syncSnapshot().jobs[0]?.status, 'failed');
        assert.equal(api.syncSnapshot().catalogUpdatedAt, catalogTime);
        assert.equal(api.syncSnapshot().stockUpdatedAt, stockTime);
        // 即使旧任务被最近20条记录淘汰，两个基础数据的成功时间仍应保留。
        for (let i = 0; i < 21; i++) {
            t.mock.timers.tick(1000);
            complete('daily');
        }
        assert.equal(api.syncSnapshot().jobs.length, 20);
        assert.ok(api.syncSnapshot().jobs.every((job) => job.kind === 'daily'));
        assert.equal(api.syncSnapshot().catalogUpdatedAt, catalogTime);
        assert.equal(api.syncSnapshot().stockUpdatedAt, stockTime);
    });
    await t.test('趋势埋伏报告独立保存，不混入同一股票的波段历史', async () => {
        await api.login('admin', 'demo1234');
        const stock = STOCKS.find((item) => item.code === '600036')!;
        await api.analyze(stock, 'trade', false, 'trend_wait');
        assert.equal(api.reportsFor(stock.code, 'trade', 'trend_wait').length, 1);
        assert.equal(api.reportsFor(stock.code, 'trade', 'swing').length, 0);
        assert.equal(api.reportsFor(stock.code, 'trade', 'long').length, 0);
    });
});
