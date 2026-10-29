// 本文件定义数值筛选的预设区间，并兼容网址中已保存的其他范围。
export interface RangeOption {
    value: string;
    label: string;
    min?: number;
    max?: number;
}

/** 换手率使用百分数，预设区间包含显示的上下边界。 */
export const TURNOVER_RANGES: readonly RangeOption[] = [
    { value: 'all', label: '不限' },
    { value: 'under-1', label: '1%及以下', max: 1 },
    { value: '1-3', label: '1%～3%', min: 1, max: 3 },
    { value: '3-5', label: '3%～5%', min: 3, max: 5 },
    { value: '5-10', label: '5%～10%', min: 5, max: 10 },
    { value: '10-20', label: '10%～20%', min: 10, max: 20 },
    { value: 'over-20', label: '20%及以上', min: 20 },
];

/** 市值区间沿用页面模型的亿元单位，正式 API 对接时统一换算。 */
export const MARKET_CAP_RANGES: readonly RangeOption[] = [
    { value: 'all', label: '不限' },
    { value: 'under-100', label: '100亿元及以下', max: 100 },
    { value: '100-300', label: '100～300亿元', min: 100, max: 300 },
    { value: '300-500', label: '300～500亿元', min: 300, max: 500 },
    { value: '500-1000', label: '500～1000亿元', min: 500, max: 1000 },
    { value: '1000-3000', label: '1000～3000亿元', min: 1000, max: 3000 },
    { value: '3000-5000', label: '3000～5000亿元', min: 3000, max: 5000 },
    { value: 'over-5000', label: '5000亿元及以上', min: 5000 },
];

/** 旧网址的非预设区间作为额外选项展示，确保当前范围可见且能切回不限。 */
export function withSavedRange(presets: readonly RangeOption[], min: number | undefined, max: number | undefined, unit: string): readonly RangeOption[] {
    if (presets.some((option) => option.min === min && option.max === max)) return presets;
    const range = min === undefined ? `${max}${unit}及以下` : max === undefined ? `${min}${unit}及以上` : `${min}～${max}${unit}`;
    return [...presets, { value: 'saved', label: `已保存：${range}`, min, max }];
}
