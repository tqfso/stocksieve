// 本文件生成仅用于 AI 筹码报告的模拟价格分布，图表、指标与正文使用同一份数据。
import type { ChipBin, ChipDistribution } from '../types.ts';

/** 创建双峰示例分布，并从实际分布中选取现价两侧的主要成本参考区。 */
export function createChipDistribution(currentPrice: number, seed: number): ChipDistribution {
    if (!Number.isFinite(currentPrice) || currentPrice <= 0) throw new Error('筹码样例需要有效的正价格。');
    // 使用整数分划定连续区间，避免浮点误差导致区间重叠或出现空隙。
    const priceCents = Math.round(currentPrice * 100);
    const start = Math.max(1, Math.round(priceCents * 0.76));
    const step = Math.max(1, Math.round(priceCents * 0.02));
    const count = Math.max(24, Math.ceil((priceCents - start) / step) + 3);
    const bins = Array.from({ length: count }, (_, index) => {
        const lower = (start + step * index) / 100;
        const upper = (start + step * (index + 1)) / 100;
        return { lower, upper, price: +((lower + upper) / 2).toFixed(2), ratio: 0 };
    });
    const weights = bins.map((bin) => {
        const ratio = bin.price / currentPrice;
        const supportPeak = 0.94 + seed % 3 * 0.005;
        const resistancePeak = 1.07 + seed % 4 * 0.005;
        return 1.3 * Math.exp(-((ratio - supportPeak) ** 2) / 0.0018) + 0.7 * Math.exp(-((ratio - resistancePeak) ** 2) / 0.0012) + 0.015;
    });
    // 占比保留两位小数，按小数余量分配剩余基点，使总和保持 100%。
    const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
    const exact = weights.map((weight) => weight / totalWeight * 10000);
    const points = exact.map(Math.floor);
    const remaining = 10000 - points.reduce((sum, value) => sum + value, 0);
    const order = exact.map((value, index) => ({ index, fraction: value - points[index]! })).sort((a, b) => b.fraction - a.fraction);
    for (let index = 0; index < remaining; index++) points[order[index]!.index]! += 1;
    bins.forEach((bin, index) => { bin.ratio = points[index]! / 100; });

    function percentile(percent: number): number {
        let accumulated = 0;
        for (const bin of bins) {
            if (bin.ratio > 0 && accumulated + bin.ratio >= percent) return +(bin.lower + (percent - accumulated) / bin.ratio * (bin.upper - bin.lower)).toFixed(2);
            accumulated += bin.ratio;
        }
        return bins.at(-1)!.upper;
    }
    function largest(items: ChipBin[]): ChipBin {
        return items.reduce((peak, item) => item.ratio > peak.ratio ? item : peak, items[0]!);
    }
    const supportCandidates = bins.filter((bin) => bin.upper <= currentPrice);
    const resistanceCandidates = bins.filter((bin) => bin.lower > currentPrice);
    const support = largest(supportCandidates.length ? supportCandidates : [bins[0]!]);
    const resistance = largest(resistanceCandidates.length ? resistanceCandidates : [bins.at(-1)!]);
    return {
        currentPrice, bins,
        averageCost: +(bins.reduce((sum, bin) => sum + bin.price * bin.ratio, 0) / 100).toFixed(2),
        profitableRatio: +bins.reduce((sum, bin) => sum + bin.ratio * Math.max(0, Math.min(1, (currentPrice - bin.lower) / (bin.upper - bin.lower))), 0).toFixed(2),
        cost70: [percentile(15), percentile(85)],
        cost90: [percentile(5), percentile(95)],
        support, resistance,
    };
}
