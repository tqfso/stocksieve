// 本文件验证均线窗口完整性、长周期计算与缺失值恢复，确保曲线不使用未来数据。
import test from 'node:test';
import assert from 'node:assert/strict';
import { calculateMovingAverages, MA_PERIODS } from '../src/moving-averages.ts';
import type { Candle } from '../src/types.ts';

function candles(length: number): Candle[] {
    return Array.from({ length }, (_, index) => ({ time: String(index), open: index + 1, close: index + 1, high: index + 1, low: index + 1, volume: 1 }));
}
test('MA5、MA10 及所有策略均线按包含当前根的完整窗口计算', () => {
    const values = calculateMovingAverages(candles(140));
    for (const size of MA_PERIODS) {
        assert.ok(values[size].slice(0, size - 1).every((value) => value === null));
        assert.equal(values[size][size - 1], (size + 1) / 2);
        assert.equal(values[size][139], (141 - size + 140) / 2);
    }
    assert.equal(values[5][4], 3);
    assert.equal(values[10][9], 5.5);
});
test('无效值所在窗口返回空，离开窗口后均线恢复计算', () => {
    const source = candles(12);
    source[2]!.close = Number.NaN;
    const values = calculateMovingAverages(source);
    assert.equal(values[5][4], null);
    assert.equal(values[5][6], null);
    assert.equal(values[5][7], 6);
    assert.ok(values[20].every((value) => value === null));
});
