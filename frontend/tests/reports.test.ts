// 本文件验证完整报告样例、行业差异和策略参考价的一致性。
import test from 'node:test';
import assert from 'node:assert/strict';
import { createReportContent, REPORT_SAMPLE_VERSION } from '../src/mock/reports.ts';
import { DATA_DATE, STOCKS, STRATEGIES } from '../src/mock/fixtures.ts';
import type { Stock, Strategy } from '../src/types.ts';

test('三类报告包含可阅读的具体正文和指标，而不是占位说明', () => {
    for (const stock of STOCKS) {
        for (const kind of ['fundamental', 'chips', 'trade'] as const) {
            const report = createReportContent(stock, kind);
            assert.equal(report.sampleVersion, REPORT_SAMPLE_VERSION);
            assert.equal(report.dataDate, DATA_DATE);
            assert.ok(report.title.includes(stock.name));
            assert.equal(report.metrics.length, 4);
            assert.equal(report.sections.length, 4);
            assert.ok(report.sections.every((section) => section.text.length > 45));
            assert.doesNotMatch(report.sections.map((section) => section.text).join(''), /此处展示|正式报告将在|当前仅展示|示例正文/);
            assert.ok(report.sources.some((source) => /模拟|虚构/.test(source)));
        }
    }
});

test('银行报告使用净息差与资产质量，制造业报告体现现金流指标', () => {
    const bank = createReportContent(STOCKS.find((stock) => stock.code === '600036')!, 'fundamental');
    assert.ok(bank.metrics.some((metric) => metric.label === '净息差'));
    assert.ok(bank.metrics.some((metric) => metric.label === '不良贷款率'));
    const manufacturer = createReportContent(STOCKS.find((stock) => stock.code === '300750')!, 'fundamental');
    assert.ok(manufacturer.metrics.some((metric) => metric.label === '现金流／净利润'));
});

test('买卖条件与所选策略的参考价、失效条件一致', () => {
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
