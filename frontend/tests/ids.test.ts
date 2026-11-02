// 本文件验证非安全上下文缺少randomUUID时仍能生成有效且独立的请求编号。
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomId } from '../src/ids.ts';

test('普通HTTP缺少randomUUID时使用随机字节生成UUID v4', () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto')!;
    const native = globalThis.crypto;
    let calls = 0;
    try {
        Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {
            getRandomValues: (array: Uint8Array) => { calls++; return native.getRandomValues(array); },
        } });
        const ids = Array.from({ length: 100 }, randomId);
        assert.equal(new Set(ids).size, 100);
        assert.ok(ids.every(id => /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id)));
        assert.equal(calls, 100);
    } finally { Object.defineProperty(globalThis, 'crypto', original); }
});

test('没有随机字节能力时给出可读错误，不退回伪随机数', () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto')!;
    try {
        Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {} });
        assert.throws(randomId, /当前浏览器无法生成请求编号/);
    } finally { Object.defineProperty(globalThis, 'crypto', original); }
});
