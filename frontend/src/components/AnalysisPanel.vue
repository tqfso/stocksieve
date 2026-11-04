<!-- 本文件展示精简报告、本人历史及Word/PDF下载，不在浏览器生成分析结论。 -->
<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { BOARDS } from '../boards.ts';
import { randomId } from '../ids.ts';
import AppIcon from './AppIcon.vue';
import StatePanel from './StatePanel.vue';
import { ApiError, analysesFor, downloadReport, formatNumber, formatTime, getAnalysis, startAnalysis } from '../api.ts';
import type { AnalysisTask, LiveStock } from '../api.ts';
import type { ReportSource } from '../types.ts';
const props = defineProps<{ stock: LiveStock }>();
const tasks = ref<AnalysisTask[]>([]);
const selected = ref('');
const detail = ref<AnalysisTask | null>(null);
const report = computed(() => detail.value?.report);
const threshold = computed(() => { const value = report.value?.calculationDetails?.find(item => item.operation === 'risk_reward')?.result.breakEvenWinRatePercent; return typeof value === 'number' ? value : null; });
const reports = computed(() => tasks.value.filter(item => item.status === 'succeeded' && !item.simulated));
const task = computed(() => tasks.value.find(item => !item.simulated && item.snapshotId === props.stock.snapshotId && ['queued', 'running'].includes(item.status)) ?? tasks.value.find(item => !item.simulated && item.snapshotId === props.stock.snapshotId));
const running = computed(() => task.value?.status === 'queued' || task.value?.status === 'running');
const submitting = ref(false);
const loadingReport = ref(false);
const exporting = ref<'docx' | 'pdf' | null>(null);
const error = ref('');
const stages = { queued: '排队中', preparing: '准备本地资料', researching: '准备资料', analyzing: '综合分析', writing: '整理报告', completed: '报告已生成' };
let timer: ReturnType<typeof setTimeout> | undefined;
let contextVersion = 0;
let reportVersion = 0;
let submissionKey = '';
function sourceUrl(value: string | null): string | undefined { try { if (!value) return; const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; } catch { return; } }
function source(id: string): ReportSource | undefined { return report.value?.sources.find(item => item.id === id); }
function taskError(item: AnalysisTask): string { return item.error?.message.includes('真实分析尚无核验') ? '公告资料暂未就绪，请稍后重试。' : item.error?.message ?? ''; }
function sourceNumber(id: string): number { return (report.value?.sources.findIndex(item => item.id === id) ?? -1) + 1; }
function followSource(event: MouseEvent, id: string): void {
    if (sourceUrl(source(id)?.url ?? null)) return;
    event.preventDefault();
    const target = document.getElementById('ai-' + id);
    const parent = target?.closest('details');
    if (parent) parent.open = true;
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function refresh(reset = false): Promise<void> {
    const version = contextVersion; clearTimeout(timer);
    try {
        const previous = task.value;
        const items = await analysesFor(props.stock);
        if (version !== contextVersion) return;
        tasks.value = items;
        const completed = task.value?.status === 'succeeded' && (task.value.id !== previous?.id || previous.status !== 'succeeded');
        if (reset || completed || !reports.value.some(item => item.id === selected.value)) selected.value = completed ? task.value!.id : reports.value[0]?.id ?? '';
    } catch (cause) { if (version === contextVersion) error.value = (cause as Error).message; }
    finally { if (version === contextVersion && running.value) timer = setTimeout(() => { void refresh(); }, 2500); }
}
async function loadReport(): Promise<void> {
    const version = ++reportVersion; const id = selected.value;
    detail.value = null; loadingReport.value = !!id;
    if (!id) return;
    try { const result = await getAnalysis(id); if (version === reportVersion) detail.value = result; }
    catch (cause) { if (version === reportVersion) error.value = (cause as Error).message; }
    finally { if (version === reportVersion) loadingReport.value = false; }
}
async function generate(): Promise<void> {
    if (running.value || submitting.value) return;
    const version = contextVersion; submitting.value = true; error.value = '';
    submissionKey ||= randomId();
    try {
        await startAnalysis(props.stock, submissionKey);
        if (version === contextVersion) { submissionKey = ''; await refresh(); }
    } catch (cause) {
        if (version === contextVersion) {
            error.value = (cause as Error).message;
            if (cause instanceof ApiError && cause.status > 0 && cause.status < 500) submissionKey = '';
            await refresh();
        }
    } finally { if (version === contextVersion) submitting.value = false; }
}
async function exportReport(format: 'docx' | 'pdf'): Promise<void> {
    if (!detail.value || exporting.value) return;
    const version = contextVersion; exporting.value = format;
    try { await downloadReport(detail.value.id, format); }
    catch (cause) { if (version === contextVersion) ElMessage.error((cause as Error).message); }
    finally { if (version === contextVersion) exporting.value = null; }
}
async function copy(full = false): Promise<void> {
    if (!report.value || !detail.value) return;
    const content = report.value, overview = content.overview;
    const parts = [content.title, `行情截至 ${detail.value.dataDate}；资料获取截至 ${formatTime(content.materialsAsOfAt)}`, content.summary,
        `${overview.holding.label}：${overview.holding.reason}`, `${overview.decision.label}：${overview.decision.reason}`];
    if (content.shareholders) parts.push('股东户数与筹码集中观察：' + content.shareholders.trendLabel + '；' + content.shareholders.explanation);
    if (full) parts.push(...content.sections.map(item => `${item.title}\n${item.headline}\n${item.text}`),
        ...content.metrics.map(item => `${item.label}：${item.value}`));
    parts.push('引用资料：', ...content.sources.map((item, index) =>
        `[${index + 1}] ${item.title}\n${sourceUrl(item.url) ?? '本地计算资料，无外部链接'}\n发布：${formatTime(item.publishedAt)}；获取：${formatTime(item.fetchedAt)}`));
    try { await navigator.clipboard.writeText(parts.join('\n\n')); ElMessage.success('报告与引用资料已复制。'); }
    catch { ElMessage.info('当前浏览器无法自动复制，可导出Word或PDF保存报告。'); }
}
watch(selected, () => { void loadReport(); });
watch([() => props.stock.stockId, () => props.stock.snapshotId], () => {
    ++contextVersion; ++reportVersion; clearTimeout(timer); tasks.value = []; selected.value = ''; detail.value = null;
    error.value = ''; submitting.value = false; exporting.value = null; submissionKey = ''; void refresh(true);
}, { immediate: true });
onUnmounted(() => { ++contextVersion; ++reportVersion; clearTimeout(timer); });
</script>
<template>
  <section id="ai-panel" class="panel analysis-panel">
    <div class="section-caption"><div><strong><AppIcon name="spark" :size="19" />AI 分析报告</strong><span class="muted">一次了解公司、风险与参与条件</span></div></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="analysis-error" />
    <div class="report-toolbar report-actions-toolbar"><div><el-select v-if="reports.length" v-model="selected" aria-label="报告历史" style="width: 240px"><el-option v-for="item in reports" :key="item.id" :label="`${item.dataDate} · ${formatTime(item.createdAt).slice(5)}`" :value="item.id" /></el-select><el-button v-if="report" @click="copy()">复制摘要</el-button><el-button type="primary" :loading="submitting" :disabled="running" @click="generate">{{ running ? '生成中' : reports.length ? '重新生成' : '生成报告' }}</el-button></div></div>
    <div v-if="task && task.status !== 'succeeded'" class="analysis-task" :class="{ 'task-failed': task.status === 'failed' }" role="status" :aria-busy="running"><div class="analysis-task-heading"><strong>{{ task.status === 'failed' ? '本次报告未完成' : stages[task.stage] }}</strong><span class="tag">{{ task.progress }}%</span></div><p v-if="task.error">{{ taskError(task) }}</p><p v-else>报告正在生成，离开页面后也可以回来查看。</p><el-button v-if="task.status === 'failed'" @click="generate">重试生成</el-button></div>
    <StatePanel v-if="loadingReport" kind="loading" title="正在读取报告" />
    <StatePanel v-else-if="!report" title="尚无AI分析报告" description="点击生成报告，查看公司类型、优势、风险及持有和买卖条件。" />
    <div v-else-if="detail" class="report-content unified-report">
      <div class="report-heading"><h2>{{ report.title }}</h2><div class="report-context"><span>{{ report.stock.code }} · {{ report.stock.industry?.name ?? '未知行业' }}</span><span>行情截至 {{ detail.dataDate }}</span><span>资料获取截至 {{ formatTime(report.materialsAsOfAt) }}（北京时间）</span></div></div>
      <el-alert v-if="detail.snapshotId !== stock.snapshotId" title="正在查看历史报告，其行情日期与当前图表不同；导出仍使用这一版报告。" type="info" :closable="false" />
      <div class="report-decision" :class="report.overview.decision.action"><span>当前建议</span><strong>{{ report.overview.decision.label }}</strong><p>{{ report.overview.decision.reason }}</p></div>
      <p class="report-summary">{{ report.summary }}</p>
      <div class="report-overview brief-grid"><article><h3>已有持仓</h3><strong>{{ report.overview.holding.label }}</strong><p>{{ report.overview.holding.reason }}</p></article><article><h3>接下来关注</h3><ul v-if="report.overview.watch?.length"><li v-for="item in report.overview.watch" :key="item">{{ item }}</li></ul><p v-else>{{ report.overview.risks[0] }}</p></article></div>
      <p v-if="report.criticalNotes?.length" class="report-critical">尚需核实：{{ report.criticalNotes.join('；') }}。</p>
      <div class="report-chapters"><details v-for="(section, index) in report.sections" :key="section.id" class="chapter"><summary><span class="chapter-number">{{ index + 1 }}</span><span><strong>{{ section.title }}</strong><small>{{ section.headline }}</small></span></summary><div v-if="section.id === 'company'" class="company-profile"><span>股票名称：{{ report.stock.name }}</span><span>股票代码：{{ report.stock.code }}</span><span>上市板块：{{ report.stock.board ? BOARDS[report.stock.board].name : '未知' }}</span><span>所属行业：{{ report.stock.industry?.name ?? '未知' }}</span></div><p>{{ section.text }}</p>      <section v-if="section.id === 'company' && report.shareholders?.latestCount != null" class="report-ownership"><div class="ownership-heading"><strong>股东户数</strong><span class="tag">{{ report.shareholders.basis === 'A' ? 'A股' : '普通股' }}</span></div><div class="ownership-metrics"><div><span>本期股东户数</span><strong>{{ report.shareholders.latestCount.toLocaleString('zh-CN') }} 户</strong></div><div><span>统计日</span><strong>{{ report.shareholders.asOf }}</strong></div><div v-if="report.shareholders.previousCount != null"><span>对比期（{{ report.shareholders.previousAsOf }}）</span><strong>{{ report.shareholders.previousCount.toLocaleString('zh-CN') }} 户</strong></div><div v-if="report.shareholders.changePercent != null"><span>户数变化</span><strong>{{ formatNumber(report.shareholders.changePercent) }}%</strong></div></div><p>{{ report.shareholders.explanation }} <a v-if="sourceUrl(report.shareholders.sourceUrl)" :href="sourceUrl(report.shareholders.sourceUrl)" target="_blank" rel="noopener noreferrer">查看来源</a></p></section><div class="chapter-citations"><a v-for="id in section.sourceIds" :key="id" :title="source(id)?.title" @click="followSource($event, id)" :href="sourceUrl(source(id)?.url ?? null) ?? `#ai-${id}`" :target="sourceUrl(source(id)?.url ?? null) ? '_blank' : undefined" rel="noopener noreferrer">[{{ sourceNumber(id) }}] 查看资料</a></div></details></div>
      <details class="report-details"><summary>查看关键指标</summary><div class="report-metrics"><div v-for="metric in report.metrics" :key="metric.label"><span>{{ metric.label }}</span><strong>{{ metric.value }}</strong><small>{{ metric.detail }}</small></div></div></details>
      <details v-if="report.coverageNotes?.length" class="report-details"><summary>查看资料范围与缺项</summary><ul><li v-for="item in report.coverageNotes" :key="item">{{ item }}</li></ul></details>
      <details v-if="report.calculationDetails?.length" class="report-details"><summary>查看计算说明</summary><p>盈亏比＝（上方参考－观察价－费用）÷（观察价－风险参考＋费用）。</p><p v-for="(item, index) in report.calculationDetails" :key="index">{{ item.limitation }}</p><p v-if="threshold != null">此情景盈亏平衡所需胜率为 {{ formatNumber(threshold) }}%，仅为数学门槛，不是实际胜率。</p></details>
      <details class="report-details"><summary>查看引用资料（{{ report.sources.length }} 条）</summary><p v-if="report.researchStatus === 'unavailable'" class="muted">本次引用已有行情和已同步资料，公司公告尚未补齐。</p><p v-else-if="report.researchStatus === 'not_searched'" class="muted">当前未联网检索，暂无联网参考资料；以下仅为实际使用的本地资料。</p><ol class="report-source-list"><li v-for="item in report.sources" :id="`ai-${item.id}`" :key="item.id"><div><strong>{{ item.title }}</strong><a v-if="sourceUrl(item.url)" :href="sourceUrl(item.url)" target="_blank" rel="noopener noreferrer">打开原文 ↗</a><p>{{ item.publisher !== item.title ? item.publisher + ' · ' : '' }}披露：{{ item.publishedAt ? formatTime(item.publishedAt) : '未知' }}；获取：{{ formatTime(item.fetchedAt) }}<span v-if="item.pages?.length"> · 原文第 {{ item.pages.join('、') }} 页</span></p></div></li></ol></details>
      <div class="report-toolbar export-toolbar"><span class="muted">生成于 {{ formatTime(report.generatedAt) }} · 导出当前选中的报告</span><div><el-button @click="copy(true)">复制全文</el-button><el-button :loading="exporting === 'docx'" :disabled="!!exporting" @click="exportReport('docx')">导出 Word</el-button><el-button :loading="exporting === 'pdf'" :disabled="!!exporting" @click="exportReport('pdf')">导出 PDF</el-button></div></div>
    </div>
  </section>
</template>
<style scoped>
.report-summary { line-height: 1.8; font-size: 15px; }
.report-critical { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.brief-grid ul { padding-left: 20px; line-height: 1.8; }
.report-actions-toolbar { justify-content: flex-end; }
.brief-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.company-profile { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px 0 12px 40px; color: var(--text-secondary); font-size: 13px; }
.report-ownership { margin: 22px 0; padding: 20px; border: 1px solid var(--line); border-radius: 12px; }
.ownership-heading { display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }
.ownership-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 18px 0; }
.ownership-metrics span, .report-ownership small { display: block; color: var(--text-secondary); font-size: 12px; }
.ownership-metrics strong { display: block; margin-top: 8px; font-size: 15px; }
.report-ownership p { font-size: 13px; line-height: 1.8; }
.report-chapters { display: grid; gap: 10px; margin: 22px 0; }
.chapter { border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
.chapter summary { cursor: pointer; display: flex; gap: 12px; align-items: flex-start; list-style: none; }
.chapter summary::after { content: '+'; margin-left: auto; color: var(--text-secondary); }
.chapter[open] summary::after { content: '−'; }
.chapter-number { background: var(--surface-soft, #f1f5f9); border-radius: 8px; min-width: 28px; height: 28px; display: grid; place-items: center; color: var(--text-secondary); }
.chapter strong { font-size: 14px; }
.chapter small { display: block; margin-top: 6px; color: var(--text-secondary); line-height: 1.7; }
.chapter p { margin: 14px 0 4px 40px; line-height: 1.9; font-size: 14px; }
.chapter-citations { margin: 8px 0 0 40px; display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; }
.valuation-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 18px 0; }
.valuation-grid span { display: block; color: var(--text-secondary); font-size: 13px; }
.valuation-grid strong { display: block; margin-top: 10px; font-size: 22px; }
.export-toolbar { padding: 20px 0 0; margin-top: 18px; border-top: 1px solid var(--line); }
@media (max-width: 600px) { .company-profile { grid-template-columns: 1fr; margin-left: 0; } .ownership-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .brief-grid { grid-template-columns: 1fr; } .chapter { padding: 14px; } .chapter p, .chapter-citations { margin-left: 0; } }
</style>
