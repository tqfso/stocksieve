// 本文件验证完整报告样例、行业差异和策略参考价的一致性。
import test from 'node:test';
import assert from 'node:assert/strict';
import { calculatePeTtm, createReportContent, REPORT_SAMPLE_VERSION } from '../src/mock/reports.ts';
import { DATA_DATE, STOCKS, STRATEGIES } from '../src/mock/fixtures.ts';
import type { Stock, Strategy } from '../src/types.ts';

test('三类报告包含可阅读的具体正文和指标，而不是占位说明', () => {
    for (const stock of STOCKS) {
        for (const kind of ['fundamental', 'chips', 'trade'] as const) {
            const report = createReportContent(stock, kind);
            assert.equal(report.sampleVersion, REPORT_SAMPLE_VERSION);
            assert.equal(report.dataDate, DATA_DATE);
            assert.ok(report.title.includes(stock.name));
            assert.equal(report.metrics.length, kind === 'fundamental' ? 8 : 4);
            assert.equal(report.sections.length, kind === 'fundamental' ? 5 : 4);
            assert.ok(report.sections.every((section) => section.text.length > 45));
            assert.doesNotMatch(report.sections.map((section) => section.text).join(''), /此处展示|正式报告将在|当前仅展示|示例正文/);
            assert.ok(report.sources.some((source) => /模拟|虚构/.test(source.title)));
            assert.ok(report.sources.every((source) => source.fetchedAt && (source.url === null || /^https:\/\//.test(source.url))));
            assert.ok(report.materialsAsOfAt.startsWith('2026-10-29'));
            assert.equal(report.dataQuality, 'complete');
            assert.deepEqual(report.missingInformation, []);
            assert.ok(report.overview.decision.label);
            assert.ok(report.overview.decision.reason.length <= 60);
            assert.ok(report.overview.opportunities.length <= 2 && report.overview.risks.length <= 2);
        }
    }
});

test('基本面展示 TTM 收入、归母净利润及有效 PE，缺失市值不输出估值倍数', () => {
    const stock = STOCKS[1]!;
    const report = createReportContent(stock, 'fundamental');
    const metric = (label: string): string => report.metrics.find((item) => item.label === label)!.value;
    assert.ok(metric('营业收入（TTM）').endsWith('亿元'));
    const profit = Number(metric('归母净利润（TTM）').replaceAll(',', '').replace('亿元', ''));
    assert.equal(metric('PE（TTM）'), `${(stock.totalMarketCap! / profit).toFixed(1)}倍`);
    const missing = createReportContent({ ...stock, totalMarketCap: null }, 'fundamental');
    assert.equal(missing.metrics.find((item) => item.label === 'PE（TTM）')?.value, '—');
    assert.equal(calculatePeTtm(100, 10), 10);
    for (const profit of [null, 0, -10, Number.NaN, Number.POSITIVE_INFINITY]) assert.equal(calculatePeTtm(100, profit), null);
});

test('筹码图合计百分之百，参考位与图中档位及正文一致', () => {
    for (const stock of STOCKS) {
        const report = createReportContent(stock, 'chips');
        const chart = report.chipDistribution!;
        assert.equal(chart.currentPrice, stock.price);
        assert.ok(Math.abs(chart.bins.reduce((sum, bin) => sum + bin.ratio, 0) - 100) < 1e-8);
        assert.ok(chart.bins.every((bin, index) => bin.ratio >= 0 && bin.lower < bin.upper && (index === 0 || chart.bins[index - 1]!.upper === bin.lower)));
        assert.ok(chart.support.upper <= stock.price && chart.support.price < stock.price);
        assert.ok(chart.resistance.lower > stock.price && chart.resistance.price > stock.price);
        assert.ok(chart.bins.some((bin) => bin.lower <= stock.price && bin.upper > stock.price));
        assert.ok(chart.bins.some((bin) => bin.price === chart.support.price && bin.ratio === chart.support.ratio));
        assert.ok(chart.bins.some((bin) => bin.price === chart.resistance.price && bin.ratio === chart.resistance.ratio));
        assert.ok(chart.cost90[0] <= chart.cost70[0] && chart.cost70[0] <= chart.cost70[1] && chart.cost70[1] <= chart.cost90[1]);
        assert.ok(chart.profitableRatio >= 0 && chart.profitableRatio <= 100);
        assert.equal(report.metrics[0]?.value, `${chart.averageCost.toFixed(2)}元`);
        assert.ok(report.sections.some((section) => section.text.includes(`支撑参考价${chart.support.price.toFixed(2)}`)));
        assert.ok(report.sections.some((section) => section.text.includes(`压力参考价${chart.resistance.price.toFixed(2)}`)));
    }
});

test('银行报告使用净息差与资产质量，制造业报告体现现金流指标', () => {
    const bank = createReportContent(STOCKS.find((stock) => stock.code === '600036')!, 'fundamental');
    assert.ok(bank.metrics.some((metric) => metric.label === '净息差'));
    assert.ok(bank.metrics.some((metric) => metric.label === '不良贷款率'));
    const manufacturer = createReportContent(STOCKS.find((stock) => stock.code === '300750')!, 'fundamental');
    assert.ok(manufacturer.metrics.some((metric) => metric.label === '现金流／净利润'));
});

test('基本面直接给出当前建议，并突出虚构的股东减持风险', () => {
    const report = createReportContent(STOCKS[0]!, 'fundamental');
    assert.equal(report.overview.decision.action, 'wait');
    assert.match(report.overview.risks[0]!, /虚构.*减持/);
    assert.ok(report.sections.some((section) => /计划上限.*实际|实际.*计划上限/.test(section.text)));
});

test('买卖建议给出参考位置和退出价，等待策略明确不立即买入', () => {
    for (const strategy of ['short', 'swing', 'long', 'trend_wait', 'base_wait'] as const) {
        const stock = STOCKS.find((item) => item.strategies.includes(strategy))!;
        const report = createReportContent(stock, 'trade', strategy);
        const plan = report.overview.pricePlan!;
        assert.ok(plan.entryPrice.includes('元') && plan.exitPrice.includes('元'));
        assert.ok(plan.considerIf && plan.avoidIf);
        if (strategy === 'trend_wait' || strategy === 'base_wait') {
            assert.equal(plan.waiting, true);
            assert.equal(plan.entryPrice, `${stock.waiting!.upper.toFixed(2)} 元`);
            assert.equal(plan.exitPrice, `${stock.waiting!.lower.toFixed(2)} 元`);
            assert.equal(report.overview.decision.action, 'wait');
            assert.match(report.overview.decision.reason, /先不买入/);
        }
        else {
            assert.equal(plan.waiting, false);
            assert.equal(plan.exitPrice.split(' 元')[0], report.metrics[2]!.value.replace('元', ''));
        }
    }
});

test('买卖建议与所选策略的参考价、失效条件一致', () => {
    const cases: [Stock, Strategy][] = [
        [STOCKS.find((stock) => stock.code === '300750')!, 'short'],
        [STOCKS.find((stock) => stock.code === '600036')!, 'swing'],
        [STOCKS.find((stock) => stock.code === '300750')!, 'long'],
    ];
    for (const [stock, strategy] of cases) {
        const report = createReportContent(stock, 'trade', strategy);
        const reference = strategy === 'long' ? stock.price / (1 + stock.ma60Distance / 100) : stock.reference;
        assert.equal(report.strategy, strategy);
        assert.equal(report.metrics[2]?.value, `${reference.toFixed(2)}元`);
        assert.ok(report.sections.some((section) => section.text.includes(STRATEGIES[strategy].invalid)));
        assert.ok(report.summary.includes(stock.price.toFixed(2)) || report.sections[0]!.text.includes(stock.price.toFixed(2)));
    }
});

test('两类等待报告复用详情区间，区分趋势整理与低位修复', () => {
    for (const strategy of ['trend_wait', 'base_wait'] as const) {
        const stock = STOCKS.find((item) => item.strategies.includes(strategy))!;
        const report = createReportContent(stock, 'trade', strategy);
        assert.equal(report.strategy, strategy);
        assert.equal(report.metrics[0]?.value, `${stock.waiting!.lower.toFixed(2)}元`);
        assert.equal(report.metrics[1]?.value, `${stock.waiting!.upper.toFixed(2)}元`);
        assert.equal(report.metrics[2]?.value, `${(stock.waiting!.amountRatio * 100).toFixed(0)}%`);
        assert.match(report.summary, /等待阶段/);
        assert.ok(report.sections.some((item) => item.text.includes(STRATEGIES[strategy].invalid)));
        assert.match(report.sections[1]!.text, /重新满足短线全部条件/);
        assert.ok(report.sections[0]!.text.includes(strategy === 'trend_wait' ? 'MA60是否保持平稳或上行' : 'MA20仍低于MA60'));
    }
});
