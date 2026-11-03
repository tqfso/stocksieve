// 本文件统一图表支持的均线周期和计算口径，避免图例与实际曲线不一致。

export const MA_PERIODS = [5, 10, 20, 60, 120] as const;
export type MovingAveragePeriod = typeof MA_PERIODS[number];
