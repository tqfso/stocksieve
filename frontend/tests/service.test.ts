// 本文件验证模拟账号权限、报告隔离、密码修改和同步任务的完整业务流程。
import test from 'node:test';
import assert from 'node:assert/strict';
import * as api from '../src/mock/service.ts';
import { STOCKS } from '../src/mock/fixtures.ts';
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
        await api.analyze(stock, 'fundamental', false);
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
    await t.test('初始化前置、重复任务阻止、失败重试与成功结果', async () => {
        await api.login('admin', 'demo1234');
        assert.throws(() => api.startSync('daily'), /先初始化/);
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
        assert.equal(api.syncSnapshot().jobs[0]?.count, 5348);
        const daily = api.startSync('daily');
        for (let i = 0; i < 5; i++)
            api.advanceSync(daily.id, false);
        assert.equal(api.syncSnapshot().jobs[0]?.status, 'success');
    });
});
