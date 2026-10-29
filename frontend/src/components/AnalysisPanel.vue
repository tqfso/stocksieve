<!-- 本文件提供三类按需模拟分析、生成状态、失败重试及本账号报告历史。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import AppIcon from './AppIcon.vue';
import StatePanel from './StatePanel.vue';
import { analyze, reportsFor } from '../mock/service.ts';
import { useReview } from '../stores.ts';
import type { AnalysisKind, Report, Stock } from '../types.ts';
const props = defineProps<{
    stock: Stock;
}>();
const review = useReview();
const kind = ref<AnalysisKind>('fundamental');
const loading = ref(false);
const error = ref('');
const reports = ref<Report[]>([]);
const selected = ref('');
const report = computed(() => reports.value.find((item) => item.id === selected.value));
const kinds: {
    key: AnalysisKind;
    name: string;
    description: string;
}[] = [
    { key: 'fundamental', name: '基本面', description: '业务、盈利与现金流' },
    { key: 'chips', name: '筹码', description: '成交活跃度与成本分布' },
    { key: 'trade', name: '买卖条件', description: '观察条件与信号失效' },
];
function refresh(): void { reports.value = reportsFor(props.stock.code, kind.value); selected.value = reports.value[0]?.id ?? ''; error.value = ''; }
async function generate(retry = false): Promise<void> {
    if (loading.value)
        return;
    if (retry)
        review.aiFail = false;
    error.value = '';
    loading.value = true;
    try {
        await analyze(props.stock, kind.value, review.aiFail);
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
    if (!report.value)
        return;
    try {
        await navigator.clipboard.writeText(`[模拟报告] ${props.stock.name}\n${report.value.summary}\n${report.value.sections.map((item) => `${item.title}\n${item.text}`).join('\n\n')}\n生成时间：${report.value.createdAt}`);
        ElMessage.success('模拟报告已复制。');
    }
    catch {
        ElMessage.info('当前浏览器无法自动复制，可以直接选择报告文字复制。');
    }
}
watch(kind, refresh);
watch(() => props.stock.code, refresh);
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
      <span class="tag tag-amber">模拟生成</span>
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
    <StatePanel v-if="loading" kind="loading" title="正在生成模拟报告" description="演示资料整理与分析过程，不会调用真实 AI 服务。" />
    <div v-else-if="!report" class="analysis-empty">
      <span class="analysis-empty-icon">
        <AppIcon name="spark" :size="30" />
      </span>
      <h3>为这只股票补充研究视角</h3>
      <p>
        选择分析类型后发起生成，报告将保存在当前浏览器，
        <br />
        仅当前演示账号可查看自己的报告记录。
      </p>
      <el-button type="primary" @click="generate()">
        <AppIcon name="spark" :size="16" />
        生成模拟报告
      </el-button>
    </div>
    <div v-else class="report-content">
      <div class="report-toolbar">
        <span class="muted">生成于 {{ report.createdAt }}（北京时间）</span>
        <div>
          <el-select v-model="selected" aria-label="报告历史" style="width: 180px">
            <el-option
              v-for="(item, index) in reports"
              :key="item.id"
              :label="`${index === 0 ? '最新' : '历史'} · ${item.createdAt.slice(5)}`"
              :value="item.id"
            />
          </el-select>
          <el-button @click="copy">复制</el-button>
          <el-button type="primary" plain @click="generate()">重新生成</el-button>
        </div>
      </div>
      <div class="report-summary">{{ report.summary }}</div>
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
        <strong>资料来源（模拟）</strong>
        <span v-for="source in report.sources" :key="source">{{ source }}</span>
        <p>当前没有获取真实网络资料；正式对接后将在此展示来源链接、资料日期及缺失信息。</p>
      </div>
    </div>
  </section>
</template>
