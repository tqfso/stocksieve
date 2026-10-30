// 本文件统一图表支持的均线周期和计算口径，避免图例与实际曲线不一致。
import type { Candle } from './types.ts';

export const MA_PERIODS = [5, 10, 20, 60, 120] as const;
export type MovingAveragePeriod = typeof MA_PERIODS[number];

/** 使用包含当前根在内的完整收盘窗口计算；历史不足或窗口内有无效值时返回 null。 */
export function calculateMovingAverages(candles: Candle[]): Record<MovingAveragePeriod, (number | null)[]> {
    return Object.fromEntries(MA_PERIODS.map((size) => {
        let sum = 0;
        let missing = 0;
        const values = candles.map((candle, index) => {
            if (Number.isFinite(candle.close)) sum += candle.close;
            else missing++;
            if (index >= size) {
                const previous = candles[index - size]!.close;
                if (Number.isFinite(previous)) sum -= previous;
                else missing--;
            }
            return index < size - 1 || missing > 0 ? null : sum / size;
        });
        return [size, values];
    })) as Record<MovingAveragePeriod, (number | null)[]>;
}
