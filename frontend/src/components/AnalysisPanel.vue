<!-- 本文件提供三类按需模拟分析、生成状态、失败重试及本账号报告历史。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import AppIcon from './AppIcon.vue';
import StatePanel from './StatePanel.vue';
import ChipDistributionChart from './ChipDistributionChart.vue';
import { analyze, reportsFor } from '../mock/service.ts';
import { createReportContent } from '../mock/reports.ts';
import { STRATEGIES } from '../mock/fixtures.ts';
import { useReview } from '../stores.ts';
import type { AnalysisKind, Report, Stock, Strategy } from '../types.ts';
const props = defineProps<{
    stock: Stock;
    strategy: Strategy;
}>();
const review = useReview();
const kind = ref<AnalysisKind>('fundamental');
const loading = ref(false);
const error = ref('');
const reports = ref<Report[]>([]);
const selected = ref('');
const savedReport = computed(() => reports.value.find((item) => item.id === selected.value));
const report = computed(() => savedReport.value ?? createReportContent(props.stock, kind.value, props.strategy));
const kinds: {
    key: AnalysisKind;
    name: string;
    description: string;
}[] = [
    { key: 'fundamental', name: '基本面', description: '业务、盈利与现金流' },
    { key: 'chips', name: '筹码', description: '成交活跃度与成本分布' },
    { key: 'trade', name: '买卖条件', description: '观察条件与信号失效' },
];
function refresh(): void { reports.value = reportsFor(props.stock.code, kind.value, props.strategy); selected.value = reports.value[0]?.id ?? ''; error.value = ''; }
async function generate(retry = false): Promise<void> {
    if (loading.value)
        return;
    if (retry)
        review.aiFail = false;
    error.value = '';
    loading.value = true;
    try {
        await analyze(props.stock, kind.value, review.aiFail, props.strategy);
        refresh();
    }
    catch (cause) {
        error.value = (cause as Error).message;
    }
    finally {
        loading.value = false;
    }
}
async function copy(): Promise<void> {
    try {
        const metrics = report.value.metrics.map((item) => `${item.label}：${item.value}`).join('\n');
        await navigator.clipboard.writeText(`[模拟报告] ${report.value.title}\n财务、估值及筹码数据为虚构样例，非真实研究结论。\n数据日期：${report.value.dataDate}\n${metrics}\n\n${report.value.summary}\n\n${report.value.sections.map((item) => `${item.title}\n${item.text}`).join('\n\n')}\n生成时间：${savedReport.value?.createdAt ?? '样例预览，尚未保存'}`);
        ElMessage.success('模拟报告已复制。');
    }
    catch {
        ElMessage.info('当前浏览器无法自动复制，可以直接选择报告文字复制。');
    }
}
watch([kind, () => props.stock.code, () => props.strategy], refresh);
refresh();
</script>
<template>
  <section id="ai-panel" class="panel analysis-panel">
    <div class="section-caption">
      <div>
        <strong>
          <AppIcon name="spark" :size="19" />
          AI 分析
        </strong>
        <span class="muted">按需发起 · 与规则筛选分别展示</span>
      </div>
      <span class="tag tag-amber">模拟数据</span>
    </div>
    <div class="analysis-types">
      <button
        v-for="item in kinds"
        :key="item.key"
        :class="{ active: kind === item.key }"
        :disabled="loading"
        @click="kind = item.key"
      >
        <strong>{{ item.name }}</strong>
        <span>{{ item.description }}</span>
      </button>
    </div>
    <el-alert v-if="error" type="error" :closable="false" show-icon class="analysis-error">
      <template #title>{{ error }}</template>
      <el-button size="small" type="danger" plain @click="generate(true)">重试生成</el-button>
    </el-alert>
    <StatePanel v-if="loading" kind="loading" title="正在生成分析报告" />
    <div v-else class="report-content">
      <div class="report-toolbar">
        <span class="muted">{{ savedReport ? `生成于 ${savedReport.createdAt}（北京时间）` : '完整样例预览 · 尚未保存' }}</span>
        <div>
          <el-select v-if="reports.length" v-model="selected" aria-label="报告历史" style="width: 180px">
            <el-option
              v-for="(item, index) in reports"
              :key="item.id"
              :label="`${index === 0 ? '最新' : '历史'} · ${item.createdAt.slice(5)}`"
              :value="item.id"
            />
          </el-select>
          <el-button @click="copy">复制</el-button>
          <el-button type="primary" @click="generate()">{{ savedReport ? '重新生成' : '生成报告' }}</el-button>
        </div>
      </div>
      <div class="report-heading">
        <h2>{{ report.title }}</h2>
        <div class="report-context">
          <span>{{ stock.code }} · {{ stock.industry }}</span>
          <span>数据截至 {{ report.dataDate }}</span>
          <span v-if="kind === 'trade'">{{ STRATEGIES[report.strategy].name }}策略</span>
        </div>
      </div>
      <p class="report-sample-note">
        <AppIcon name="info" :size="15" />
        财务、估值与筹码数值为虚构样例，仅用于产品评审。
      </p>
      <div class="report-metrics">
        <div v-for="metric in report.metrics" :key="metric.label">
          <span>{{ metric.label }}</span>
          <strong>{{ metric.value }}</strong>
          <small>{{ metric.detail }}</small>
        </div>
      </div>
      <div class="report-summary">
        <strong>核心判断</strong>
        <p>{{ report.summary }}</p>
      </div>
      <ChipDistributionChart v-if="report.chipDistribution" :distribution="report.chipDistribution" />
      <div class="report-sections">
        <article v-for="(section, index) in report.sections" :key="section.title">
          <span class="report-number">0{{ index + 1 }}</span>
          <div>
            <h3>{{ section.title }}</h3>
            <p>{{ section.text }}</p>
          </div>
        </article>
      </div>
      <div class="report-sources">
        <strong>参考依据 · 模拟样例</strong>
        <span v-for="source in report.sources" :key="source">{{ source }}</span>
      </div>
    </div>
  </section>
</template>
