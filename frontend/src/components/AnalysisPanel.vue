<!-- 本文件展示三类模拟分析的可恢复任务、引用资料、资料质量及个人报告历史。 -->
<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import AppIcon from './AppIcon.vue';
import ChipDistributionChart from './ChipDistributionChart.vue';
import { analysesFor, reportsFor, startAnalysis } from '../mock/service.ts';
import { createReportContent } from '../mock/reports.ts';
import { STRATEGIES } from '../mock/fixtures.ts';
import { useReview } from '../stores.ts';
import type { AnalysisKind, AnalysisStage, AnalysisTask, Report, ReportOverview, Stock, Strategy } from '../types.ts';
const props = defineProps<{ stock: Stock; strategy: Strategy }>();
const review = useReview();
const kind = ref<AnalysisKind>('fundamental');
const error = ref('');
const reports = ref<Report[]>([]);
const tasks = ref<AnalysisTask[]>([]);
const selected = ref('');
const task = computed(() => tasks.value[0]);
const running = computed(() => task.value?.status === 'queued' || task.value?.status === 'running');
const savedReport = computed(() => reports.value.find((item) => item.id === selected.value));
const report = computed(() => savedReport.value ?? createReportContent(props.stock, kind.value, props.strategy));
const overview = computed<ReportOverview>(() => report.value.overview?.decision ? report.value.overview : { decision: { action: 'wait', label: '先观察', reason: '该历史样例可展开查看原文。' }, opportunities: [], risks: ['该历史样例尚未提供精简摘要。'] });
const kinds: { key: AnalysisKind; name: string; description: string }[] = [
    { key: 'fundamental', name: '基本面', description: '看机会，识别风险' },
    { key: 'chips', name: '筹码', description: '用图看估算成本' },
    { key: 'trade', name: '买卖建议', description: '看参考价与参与条件' },
];
const steps: { key: AnalysisStage; label: string; description: string }[] = [
    { key: 'queued', label: '排队中', description: '任务已受理，等待开始处理。' },
    { key: 'researching', label: '获取资料', description: '按需查询公告、财报和相关新闻，读取并核对资料。' },
    { key: 'analyzing', label: '分析研判', description: '结合行情、策略信号和已取得的资料形成分析。' },
    { key: 'writing', label: '整理报告', description: '整理结论、引用来源及缺失说明，保存本次结果。' },
];
const stageIndex = computed(() => task.value?.stage === 'completed' ? steps.length : steps.findIndex((step) => step.key === task.value?.stage));
const taskTitle = computed(() => task.value?.status === 'failed' ? '本次分析未完成' : task.value?.status === 'succeeded' ? '本次报告已生成' : steps[stageIndex.value]?.label ?? '任务已受理');
let timer: ReturnType<typeof setInterval> | undefined;

/** 所有时间固定按北京时间展示，行情日期与资料获取时间分别呈现。 */
function formatTime(value: string): string {
    return new Date(value).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai', hour12: false });
}
/** 仅允许 HTTP(S) 来源链接；无有效地址时保留文本和未知状态。 */
function sourceUrl(value: string | null): string | undefined {
    if (!value) return undefined;
    try {
        const url = new URL(value);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined;
    }
    catch { return undefined; }
}
/** 读取任务和历史；任务完成时展示新报告，之后保留用户主动选择的历史。 */
function refresh(resetSelection = false): void {
    try {
        const previous = task.value;
        tasks.value = analysesFor(props.stock.code, kind.value, props.strategy);
        reports.value = reportsFor(props.stock.code, kind.value, props.strategy);
        const justCompleted = task.value?.status === 'succeeded' && (task.value.id !== previous?.id || previous.status !== 'succeeded');
        if (resetSelection || justCompleted || !reports.value.some((item) => item.id === selected.value)) selected.value = reports.value[0]?.id ?? '';
        if (running.value && !timer) timer = setInterval(() => refresh(), 1000);
        if (!running.value && timer) { clearInterval(timer); timer = undefined; }
    }
    catch (cause) {
        tasks.value = [];
        reports.value = [];
        selected.value = '';
        error.value = (cause as Error).message;
        if (timer) { clearInterval(timer); timer = undefined; }
    }
}
/** 只有用户主动提交才生成新任务；切换页面或轮询不会重复受理。 */
function generate(retry = false): void {
    if (running.value) return;
    error.value = '';
    if (retry) review.aiScenario = 'normal';
    try {
        startAnalysis(props.stock, kind.value, { strategy: props.strategy, scenario: review.aiScenario, idempotencyKey: crypto.randomUUID() });
        refresh();
    }
    catch (cause) { error.value = (cause as Error).message; }
}
async function copy(full = false): Promise<void> {
    try {
        const content = report.value;
        const metrics = content.metrics.map((item) => `${item.label}：${item.value}`).join('\n');
        const sources = content.sources.map((source, index) => `[${index + 1}] ${source.title}\n${sourceUrl(source.url) ?? '无可用链接'}\n发布：${source.publishedAt ? formatTime(source.publishedAt) : '未知'}；获取：${formatTime(source.fetchedAt)}`).join('\n\n');
        const brief = overview.value;
        const prices = brief.pricePlan ? `\n${brief.pricePlan.entryLabel}：${brief.pricePlan.entryPrice}\n${brief.pricePlan.entryCondition}\n失效／退出参考：${brief.pricePlan.exitPrice}\n${brief.pricePlan.exitCondition}` : '';
        const body = `当前建议：${brief.decision.label}\n${brief.decision.reason}\n机会／当前状态\n${brief.opportunities.join('\n')}\n主要风险\n${brief.risks.join('\n')}${prices}`;
        const details = full ? `\n\n${metrics}\n\n${content.summary}\n\n${content.sections.map((item) => `${item.title}\n${item.text}`).join('\n\n')}` : '';
        await navigator.clipboard.writeText(`[模拟报告] ${content.title}\n财务、来源时间与筹码数据为虚构样例；链接仅为公开披露入口。\n行情截至：${content.dataDate}\n资料获取截至：${formatTime(content.materialsAsOfAt)}（北京时间）\n资料覆盖：${content.dataQuality === 'partial' ? '资料不完整' : '资料完整'}\n${content.missingInformation.join('\n')}\n\n${body}${details}\n\n引用资料\n${sources}\n\n生成时间：${savedReport.value ? formatTime(savedReport.value.createdAt) : '样例预览，尚未保存'}`);
        ElMessage.success('模拟报告及引用资料已复制。');
    }
    catch { ElMessage.info('当前浏览器无法自动复制，可以直接选择报告文字复制。'); }
}
watch([kind, () => props.stock.code, () => props.strategy], () => { error.value = ''; refresh(true); }, { immediate: true });
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<template>
  <section id="ai-panel" class="panel analysis-panel">
    <div class="section-caption">
      <div>
        <strong><AppIcon name="spark" :size="19" />AI 分析</strong>
        <span class="muted">按需补充资料 · 与规则筛选分别展示</span>
      </div>
      <span class="tag tag-amber">模拟评审</span>
    </div>
    <div class="analysis-types">
      <button v-for="item in kinds" :key="item.key" :class="{ active: kind === item.key }" :aria-pressed="kind === item.key" @click="kind = item.key">
        <strong>{{ item.name }}</strong><span>{{ item.description }}</span>
      </button>
    </div>
    <p class="analysis-intro">分析会按需联网补充资料，并列明来源和判断范围。当前为本地模拟，不执行真实检索或模型调用。</p>
    <el-alert v-if="error" type="error" :closable="false" show-icon class="analysis-error" :title="error" />
    <div v-if="task && task.status !== 'succeeded'" class="analysis-task" :class="{ 'task-failed': task.status === 'failed' }" role="status" :aria-busy="running">
      <div class="analysis-task-heading">
        <strong><AppIcon :name="task.status === 'failed' ? 'info' : 'clock'" :size="18" />{{ taskTitle }}</strong>
        <span class="tag" :class="task.status === 'failed' ? 'tag-red' : 'tag-blue'">{{ task.status === 'failed' ? '生成失败' : '模拟任务' }}</span>
      </div>
      <ol class="analysis-steps" aria-label="分析进度">
        <li v-for="(step, index) in steps" :key="step.key" :class="{ done: index < stageIndex, current: index === stageIndex, failed: index === stageIndex && task.status === 'failed' }" :aria-current="index === stageIndex ? 'step' : undefined">
          <span>{{ index < stageIndex ? '✓' : index + 1 }}</span>{{ step.label }}
        </li>
      </ol>
      <p v-if="task.error">{{ task.error.message }}</p>
      <p v-else-if="running">{{ steps[stageIndex]?.description }}切换页面或刷新后，可回到这里继续查看。</p>
      <div class="analysis-task-footer">
        <span>受理于 {{ formatTime(task.createdAt) }}（北京时间）</span>
        <el-button v-if="task.status === 'failed'" type="primary" plain size="small" @click="generate(true)">重试生成</el-button>
      </div>
    </div>
    <div class="report-content">
      <div class="report-toolbar">
        <span class="muted">{{ savedReport ? `生成于 ${formatTime(savedReport.createdAt)}（北京时间）` : '完整样例预览 · 尚未保存' }}</span>
        <div>
          <el-select v-if="reports.length" v-model="selected" aria-label="报告历史" style="width: 210px">
            <el-option v-for="(item, index) in reports" :key="item.id" :label="`${index === 0 ? '最新' : '历史'} · ${formatTime(item.createdAt).slice(5)}${item.dataQuality === 'partial' ? ' · 资料不完整' : ''}`" :value="item.id" />
          </el-select>
          <el-button @click="copy()">复制摘要</el-button>
          <el-button type="primary" :disabled="running" @click="generate()">{{ running ? '生成中' : reports.length ? '重新生成' : '生成分析' }}</el-button>
        </div>
      </div>
      <p v-if="running && savedReport" class="analysis-history-note">正在生成新的分析，下方保留已保存的报告供查看。</p>
      <div class="report-heading">
        <h2>{{ report.title }}</h2>
        <div class="report-context">
          <span>{{ stock.code }} · {{ stock.industry }}</span>
          <span>行情截至 {{ report.dataDate }}</span>
          <span>资料获取截至 {{ formatTime(report.materialsAsOfAt) }}（北京时间）</span>
          <span v-if="kind === 'trade'">{{ STRATEGIES[report.strategy].name }}策略</span>
        </div>
      </div>
      <p class="report-sample-note"><AppIcon name="info" :size="15" />以下为虚构演示，仅用于产品评审。<span v-if="report.dataQuality === 'partial'" class="tag tag-amber">部分资料待核验</span></p>
      <div class="report-decision" :class="overview.decision.action">
        <span>当前建议</span><strong>{{ overview.decision.label }}</strong><p>{{ overview.decision.reason }}</p>
      </div>
      <div v-if="kind === 'fundamental'" class="report-overview">
        <article><h3>机会</h3><p>{{ overview.opportunities[0] }}</p></article>
        <article class="overview-risks"><h3>风险</h3><p>{{ overview.risks[0] }}</p></article>
      </div>
      <template v-else-if="kind === 'chips'">
        <ChipDistributionChart v-if="report.chipDistribution" :distribution="report.chipDistribution" />
        <p class="report-brief-note">{{ overview.risks[0] }}</p>
      </template>
      <template v-else-if="overview.pricePlan">
        <div class="report-price-plan" :class="{ waiting: overview.pricePlan.waiting }">
          <article><span>{{ overview.pricePlan.entryLabel }}</span><strong>{{ overview.pricePlan.entryPrice }}</strong></article>
          <article class="price-exit"><span>退出参考</span><strong>{{ overview.pricePlan.exitPrice }}</strong><p>{{ overview.pricePlan.exitCondition }}</p></article>
        </div>
        <p v-if="report.dataQuality === 'partial'" class="report-brief-note">事件风险未核验，技术建议基于已同步行情。</p>
      </template>
      <details class="report-details">
        <summary>为什么这样建议</summary>
        <div v-if="report.missingInformation.length" class="report-quality partial"><strong>缺失资料</strong><ul><li v-for="missing in report.missingInformation" :key="missing">{{ missing }}</li></ul></div>
        <div v-if="overview.pricePlan" class="report-summary"><strong>适合与暂不适合的情况</strong><p>{{ overview.pricePlan.considerIf }}</p><p>{{ overview.pricePlan.avoidIf }}</p><p>{{ overview.pricePlan.entryCondition }}</p><p>{{ overview.pricePlan.exitCondition }}</p></div>
        <div class="report-metrics">
        <div v-for="metric in report.metrics" :key="metric.label"><span>{{ metric.label }}</span><strong>{{ metric.value }}</strong><small>{{ metric.detail }}</small></div>
      </div>
      <div class="report-summary"><strong>分析判断</strong><p>{{ report.summary }}</p></div>
      <div class="report-sections">
        <article v-for="(section, index) in report.sections" :key="section.title">
          <span class="report-number">0{{ index + 1 }}</span><div><h3>{{ section.title }}</h3><p>{{ section.text }}</p></div>
        </article>
      </div>
        <el-button size="small" @click="copy(true)">复制全文及来源</el-button>
      </details>
      <details class="report-details report-source-details">
        <summary>查看引用资料（{{ report.sources.length }} 条）</summary>
        <div class="report-sources">
        <strong>引用资料 · 模拟样例</strong>
        <p>链接仅指向公开披露入口，不能用于核验以上虚构内容。正式报告将引用实际取得的资料原文。</p>
        <ol class="report-source-list">
          <li v-for="(source, index) in report.sources" :key="source.title">
            <span class="report-source-number">{{ index + 1 }}</span>
            <div>
              <div class="report-source-title"><b>{{ source.title }}</b><a v-if="sourceUrl(source.url)" :href="sourceUrl(source.url)" target="_blank" rel="noopener noreferrer">打开披露入口 ↗</a><span v-else>无外部链接</span></div>
              <div class="report-source-dates"><span>发布：{{ source.publishedAt ? formatTime(source.publishedAt) : '未知' }}</span><span>获取：{{ formatTime(source.fetchedAt) }}（北京时间）</span></div>
            </div>
          </li>
        </ol>
        </div>
      </details>
    </div>
  </section>
</template>
