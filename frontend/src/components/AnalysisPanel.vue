<!-- 本文件通过后端受理和查询模拟分析任务，不在浏览器生成报告、来源或图形数值。 -->
<script setup lang="ts">
import { randomId } from '../ids.ts';
import { computed, onUnmounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import AppIcon from './AppIcon.vue';
import StatePanel from './StatePanel.vue';
import ChipDistributionChart from './ChipDistributionChart.vue';
import { ApiError, analysesFor, getAnalysis, startAnalysis } from '../api.ts';
import type { AnalysisMode, AnalysisTask, LiveStock } from '../api.ts';
import { STRATEGIES } from '../strategies.ts';
import type { AnalysisKind, Strategy } from '../types.ts';
const props = defineProps<{ stock: LiveStock; strategy?: Strategy; initialKind?: AnalysisKind }>();
const emit = defineEmits<{ modeChange: [value: AnalysisMode]; kindChange: [value: AnalysisKind] }>();
const kind = ref<AnalysisKind>(props.initialKind ?? 'fundamental');
const mode = ref<AnalysisMode>(props.strategy && props.stock.strategies.includes(props.strategy) ? props.strategy : 'general');
const error = ref('');
const tasks = ref<AnalysisTask[]>([]);
const selected = ref('');
const detail = ref<AnalysisTask | null>(null);
const report = computed(() => detail.value?.report);
const reports = computed(() => tasks.value.filter(item => item.status === 'succeeded'));
const task = computed(() => tasks.value.find(item => item.snapshotId === props.stock.snapshotId && ['queued', 'running'].includes(item.status)) ?? tasks.value.find(item => item.snapshotId === props.stock.snapshotId));
const running = computed(() => task.value?.status === 'queued' || task.value?.status === 'running');
const submitting = ref(false);
const loadingReport = ref(false);
const overview = computed(() => report.value?.overview);
const kinds: { key: AnalysisKind; name: string; description: string }[] = [
    { key: 'fundamental', name: '基本面', description: '看机会，识别风险' }, { key: 'chips', name: '筹码', description: '用图看估算成本' }, { key: 'trade', name: '买卖建议', description: '看参考价与参与条件' },
];
const stages = { queued: '排队中', preparing: '准备资料', researching: '准备资料', analyzing: '模拟分析', writing: '整理报告', completed: '报告已生成' };
let timer: ReturnType<typeof setTimeout> | undefined;
let contextVersion = 0;
let reportVersion = 0;
let submissionKey = '';
function formatTime(value: string): string { return new Date(value).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai', hour12: false }); }
function sourceUrl(value: string | null): string | undefined { try { if (!value) return; const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined; } catch { return; } }
async function refresh(reset = false): Promise<void> {
    const version = contextVersion; clearTimeout(timer);
    try {
        const previous = task.value;
        const items = await analysesFor(props.stock, kind.value, mode.value);
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
        await startAnalysis(props.stock, kind.value, mode.value, submissionKey);
        if (version === contextVersion) { submissionKey = ''; await refresh(); }
    } catch (cause) {
        if (version === contextVersion) {
            error.value = (cause as Error).message;
            if (cause instanceof ApiError && cause.status > 0 && cause.status < 500) submissionKey = '';
            await refresh();
        }
    } finally { if (version === contextVersion) submitting.value = false; }
}
async function copy(full = false): Promise<void> {
    if (!report.value || !detail.value) return;
    const content = report.value, brief = content.overview;
    const prices = brief.pricePlan ? `\n${brief.pricePlan.entryLabel}：${brief.pricePlan.entryPrice}\n退出参考：${brief.pricePlan.exitPrice}\n${brief.pricePlan.exitCondition}` : '';
    const sources = content.sources.map((source, index) => `[${index + 1}] ${source.title}\n${sourceUrl(source.url) ?? '无外部链接'}\n发布：${source.publishedAt ? formatTime(source.publishedAt) : '未知'}；获取：${formatTime(source.fetchedAt)}`).join('\n\n');
    const extra = full ? `\n\n${content.metrics.map(item => `${item.label}：${item.value}`).join('\n')}\n${content.summary}\n${content.sections.map(item => `${item.title}\n${item.text}`).join('\n\n')}` : '';
    try {
        await navigator.clipboard.writeText(`${content.title}\n${content.simulated ? '模拟分析，未调用真实模型。\n' : ''}行情截至：${detail.value.dataDate}\n资料获取截至：${formatTime(content.materialsAsOfAt)}\n当前建议：${brief.decision.label}\n${brief.decision.reason}\n${brief.opportunities.join('\n')}\n${brief.risks.join('\n')}${prices}${extra}\n\n缺失资料\n${content.missingInformation.join('\n')}\n\n引用资料\n${sources}\n生成时间：${formatTime(content.generatedAt)}`);
        ElMessage.success('报告内容及引用资料已复制。');
    } catch { ElMessage.info('当前浏览器无法自动复制，可以选择报告文字复制。'); }
}
watch(selected, () => { void loadReport(); });
watch(mode, value => emit('modeChange', value));
watch(kind, value => emit('kindChange', value));
watch(() => props.initialKind, value => { if (value) kind.value = value; });
watch(() => props.strategy, value => { mode.value = value && props.stock.strategies.includes(value) ? value : 'general'; });
watch([kind, () => props.stock.stockId, () => props.stock.snapshotId, mode], () => {
    ++contextVersion; ++reportVersion; clearTimeout(timer); tasks.value = []; selected.value = ''; detail.value = null; error.value = ''; submitting.value = false; submissionKey = ''; void refresh(true);
}, { immediate: true });
onUnmounted(() => { ++contextVersion; ++reportVersion; clearTimeout(timer); });
</script>
<template>
  <section id="ai-panel" class="panel analysis-panel">
    <div class="section-caption"><div><strong><AppIcon name="spark" :size="19" />AI 分析</strong><span class="muted">当前使用后端模拟分析</span></div><span class="tag tag-amber">模拟分析</span></div>
    <div class="analysis-types"><button v-for="item in kinds" :key="item.key" :class="{ active: kind === item.key }" :aria-pressed="kind === item.key" @click="kind = item.key"><strong>{{ item.name }}</strong><span>{{ item.description }}</span></button></div>
    <div v-if="kind === 'trade'" class="report-toolbar analysis-mode"><span class="muted">{{ mode === 'general' ? '通用分析，无需入选策略。' : '结合已入选策略的参考价和条件。' }}</span><div><el-select v-model="mode" aria-label="买卖建议分析方式" style="width: 220px"><el-option label="通用分析" value="general" /><el-option v-for="key in stock.strategies" :key="key" :label="`${STRATEGIES[key].name}策略`" :value="key" /></el-select></div></div>
    <p class="analysis-intro">当前不调用真实模型或联网检索。图表、财务指标和参考价采用后端实际取得的数据与计算结果，缺失内容会明确说明。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="analysis-error" />
    <div class="report-toolbar"><span class="muted">报告按用户保存，刷新不会重新生成。</span><div><el-select v-if="reports.length" v-model="selected" aria-label="报告历史" style="width: 240px"><el-option v-for="item in reports" :key="item.id" :label="`${item.dataDate} · ${formatTime(item.createdAt).slice(5)}${item.simulated ? ' · 模拟' : ''}`" :value="item.id" /></el-select><el-button v-if="report" @click="copy()">复制摘要</el-button><el-button type="primary" :loading="submitting" :disabled="running" @click="generate">{{ running ? '生成中' : reports.length ? '重新生成' : '生成分析' }}</el-button></div></div>
    <div v-if="task && task.status !== 'succeeded'" class="analysis-task" :class="{ 'task-failed': task.status === 'failed' }" role="status" :aria-busy="running"><div class="analysis-task-heading"><strong>{{ task.status === 'failed' ? '本次分析未完成' : stages[task.stage] }}</strong><span class="tag">{{ task.progress }}%</span></div><p v-if="task.error">{{ task.error.message }}</p><p v-else>任务已由服务器受理，切换页面后可回来继续查看。</p><el-button v-if="task.status === 'failed'" @click="generate">重试生成</el-button></div>
    <StatePanel v-if="loadingReport" kind="loading" title="正在读取报告" />
    <StatePanel v-else-if="!report" title="尚无分析报告" description="点击生成分析后，服务器会保存本次结果。" />
    <div v-else-if="overview && detail" class="report-content">
      <div class="report-heading"><h2>{{ report.title }}</h2><div class="report-context"><span>{{ stock.code }} · {{ stock.industry }}</span><span>行情截至 {{ detail.dataDate }}</span><span>资料获取截至 {{ formatTime(report.materialsAsOfAt) }}（北京时间）</span><span v-if="detail.strategy">{{ STRATEGIES[detail.strategy].name }}策略</span><span v-else-if="kind === 'trade'">通用分析</span></div></div>
      <p class="report-sample-note"><AppIcon name="info" :size="15" />{{ report.simulated ? '模拟分析文本；不是实际模型研判。' : '请结合来源与资料覆盖范围阅读。' }}<span v-if="report.dataQuality === 'partial'" class="tag tag-amber">部分资料待核验</span></p>
      <div class="report-decision" :class="overview.decision.action"><span>当前建议</span><strong>{{ overview.decision.label }}</strong><p>{{ overview.decision.reason }}</p></div>
      <div v-if="kind === 'fundamental'" class="report-overview"><article><h3>机会</h3><p>{{ overview.opportunities[0] ?? '暂无已核验的机遇。' }}</p></article><article class="overview-risks"><h3>风险</h3><p>{{ overview.risks[0] ?? '请查看资料覆盖范围。' }}</p></article></div>
      <template v-else-if="kind === 'chips'"><ChipDistributionChart v-if="report.chipDistribution" :distribution="report.chipDistribution" /><p class="report-brief-note">{{ overview.risks[0] }}</p></template>
      <div v-else-if="overview.pricePlan" class="report-price-plan" :class="{ waiting: overview.pricePlan.waiting }"><article><span>{{ overview.pricePlan.entryLabel }}</span><strong>{{ overview.pricePlan.entryPrice }}</strong><p>{{ overview.pricePlan.entryCondition }}</p></article><article class="price-exit"><span>风险复查参考</span><strong>{{ overview.pricePlan.exitPrice }}</strong><p>{{ overview.pricePlan.exitCondition }}</p></article></div>
      <div v-if="report.missingInformation.length" class="report-quality partial"><strong>待核验</strong><p>{{ report.missingInformation[0] }}</p></div>
      <details class="report-details"><summary>为什么这样建议</summary><div class="report-metrics"><div v-for="metric in report.metrics" :key="metric.label"><span>{{ metric.label }}</span><strong>{{ metric.value }}</strong><small>{{ metric.detail }}</small></div></div><div class="report-summary"><p>{{ report.summary }}</p></div><div v-if="overview.pricePlan" class="report-summary"><p>{{ overview.pricePlan.considerIf }}</p><p>{{ overview.pricePlan.avoidIf }}</p><p>{{ overview.pricePlan.entryCondition }}</p></div><div class="report-sections"><article v-for="section in report.sections" :key="section.title"><div><h3>{{ section.title }}</h3><p>{{ section.text }}</p></div></article></div><ul><li v-for="missing in report.missingInformation" :key="missing">{{ missing }}</li></ul><el-button @click="copy(true)">复制全文及来源</el-button></details>
      <details class="report-details"><summary>查看引用资料（{{ report.sources.length }} 条）</summary><ol class="report-source-list"><li v-for="source in report.sources" :key="source.title"><div><strong>{{ source.title }}</strong><a v-if="sourceUrl(source.url)" :href="sourceUrl(source.url)" target="_blank" rel="noopener noreferrer">打开原文 ↗</a><p>发布：{{ source.publishedAt ? formatTime(source.publishedAt) : '未知' }}；获取：{{ formatTime(source.fetchedAt) }}（北京时间）</p></div></li></ol><p class="muted">生成于 {{ formatTime(report.generatedAt) }}（北京时间）</p></details>
    </div>
  </section>
</template>
<style scoped>
.analysis-mode { padding: 0 24px; }
</style>
