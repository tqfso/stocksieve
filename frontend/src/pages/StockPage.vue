<!-- 本文件按股票稳定ID和冻结快照读取真实行情、策略信号、图表及后端模拟分析。 -->
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import StockChart from '../components/StockChart.vue';
import AnalysisPanel from '../components/AnalysisPanel.vue';
import ChipsPanel from '../components/ChipsPanel.vue';
import { BOARDS } from '../boards.ts';
import { STRATEGIES, isWaitingStrategy, selectionReason, signalMetrics, strategyMetric } from '../strategies.ts';
import { ApiError, formatNumber, getStock, quoteClass } from '../api.ts';
import type { LiveStock } from '../api.ts';
import type { Strategy } from '../types.ts';
const route = useRoute();
const router = useRouter();
const stock = ref<LiveStock>();
const loading = ref(true);
const error = ref('');
const needsData = ref(false);
const activeStrategy = ref<Strategy>('short');
const strategy = computed(() => STRATEGIES[activeStrategy.value]);
const strategyNames = computed(() => stock.value?.strategies.map(key => STRATEGIES[key].name).join(' / ') || '未入选策略');
const metrics = computed(() => stock.value ? signalMetrics(stock.value, activeStrategy.value) : {});
const rangePosition = computed(() => {
    const lower = metrics.value.rangeLower, upper = metrics.value.rangeUpper, price = stock.value?.price;
    return lower == null || upper == null || upper <= lower || price == null ? 0 : Math.max(0, Math.min(100, (price - lower) / (upper - lower) * 100));
});
const metricLabels: Record<string, string> = { breakoutReference: '突破参考价', amountMultiple: '成交额放大倍数', ma20: '20日均价', ma60: '60日均价', ma120: '120日均价', ma20Distance: '距20日均价（%）', ma60Distance: '距60日均价（%）', pullbackLow: '回调低点', pullbackAmountRatio: '回调成交额比例', rangeLower: '区间下沿', rangeUpper: '区间上沿', rangeWidth: '区间宽度（%）', amountRatio: '近5日/20日均额', distanceToUpper: '距区间上沿（%）', position120: '120日区间位置（%）' };
const displayedMetrics = computed(() => Object.entries(metrics.value).filter(([key]) => metricLabels[key]));
const tab = computed<'rules' | 'chips' | 'ai'>({
    get: () => route.query.tab === 'ai' ? 'ai' : route.query.tab === 'chips' ? 'chips' : 'rules',
    set: value => { void router.replace({ query: { ...route.query, tab: value } }); },
});
const fromAnalysis = computed(() => route.query.entry === 'analysis');
const back = computed(() => typeof route.query.back === 'string' && (route.query.back.startsWith('/opportunities') || route.query.back.startsWith('/analysis')) ? route.query.back : fromAnalysis.value ? '/analysis' : '/opportunities');
let requestId = 0;
async function load(): Promise<void> {
    const id = ++requestId; loading.value = true; error.value = ''; needsData.value = false;
    try {
        const result = await getStock(String(route.params.code), typeof route.query.snapshotId === 'string' ? route.query.snapshotId : undefined);
        if (id !== requestId) return;
        stock.value = result;
        activeStrategy.value = result.strategies.includes(route.query.strategy as Strategy) ? route.query.strategy as Strategy : result.strategies[0] ?? 'short';
    } catch (cause) { if (id === requestId) { error.value = (cause as Error).message; needsData.value = cause instanceof ApiError && ['DATA_NOT_READY', 'STOCK_DATA_NOT_READY'].includes(cause.code); } }
    finally { if (id === requestId) loading.value = false; }
}
async function openAi(): Promise<void> { await router.replace({ query: { ...route.query, tab: 'ai' } }); await nextTick(); document.getElementById('ai-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
watch(activeStrategy, (value) => { if (stock.value?.strategies.includes(value) && route.query.strategy !== value) void router.replace({ query: { ...route.query, strategy: value, snapshotId: stock.value.snapshotId } }); });
watch([() => route.params.code, () => route.query.snapshotId], () => { void load(); }, { immediate: true });
onUnmounted(() => { ++requestId; });
</script>
<template>
  <router-link :to="back" class="back-link"><AppIcon name="back" :size="17" />{{ fromAnalysis ? '返回个股搜索' : '返回机会列表' }}</router-link>
  <StatePanel v-if="loading" kind="loading" title="正在读取个股详情" />
  <StatePanel v-else-if="error || !stock" :kind="needsData ? 'empty' : 'error'" :title="needsData ? '收盘资料尚未就绪' : '无法打开这只股票'" :description="error" :action="needsData ? '刷新资料状态' : '重试'" @action="load" />
  <template v-else>
    <div class="stock-heading"><div><div class="stock-title-row"><h1>{{ stock.name }}</h1><span class="tag">{{ stock.board ? BOARDS[stock.board].name : '未知板块' }} {{ stock.code }}</span><span class="tag">{{ stock.industry }}</span></div><div class="concept-tags"><span v-for="concept in stock.concepts" :key="concept">{{ concept }}</span><span>数据日期 {{ stock.dataDate }}</span></div></div><el-button type="primary" @click="openAi"><AppIcon name="spark" :size="17" />发起 AI 分析</el-button></div>
    <div class="quote-row">
      <div class="main-quote" :class="quoteClass(stock.change)"><strong>{{ formatNumber(stock.price) }}</strong><span>{{ stock.change !== null && stock.change >= 0 ? '+' : '' }}{{ formatNumber(stock.change) }}%</span><small>收盘价 / 元</small></div>
      <div><span>换手率</span><strong>{{ formatNumber(stock.turnover) }}%</strong></div><div><span>成交额</span><strong>{{ formatNumber(stock.amount, 1) }}<small>亿元</small></strong></div><div><span>总市值</span><strong>{{ formatNumber(stock.totalMarketCap) }}<small v-if="stock.totalMarketCap !== null">亿元</small></strong></div>
      <div><span>所属策略</span><strong class="quote-strategies">{{ strategyNames }}</strong></div><div><span>数据口径</span><strong class="quote-strategies">前复权</strong></div>
    </div>
    <StockChart :stock="stock" />
    <div class="detail-tabs"><button :class="{ active: tab === 'rules' }" @click="tab = 'rules'">规则筛选结果</button><button :class="{ active: tab === 'chips' }" @click="tab = 'chips'">筹码分布</button><button :class="{ active: tab === 'ai' }" @click="tab = 'ai'">AI 分析报告<span class="tiny-label">按需生成</span></button></div>
    <section v-if="tab === 'rules'" class="panel signal-panel">
      <div class="section-caption"><div><strong>入选信号快照</strong><span class="muted">{{ stock.dataDate }}</span></div><div class="segment-control"><button v-for="key in stock.strategies" :key="key" :class="{ active: activeStrategy === key }" @click="activeStrategy = key">{{ STRATEGIES[key].name }}</button></div></div>
      <StatePanel v-if="!stock.strategies.length" title="所选快照未入选任何策略" description="仍可查看行情和筹码分布，并生成完整的AI模拟分析报告。" />
      <div v-else class="signal-layout"><div><span class="eyebrow">{{ strategy.name }} · {{ strategy.subtitle }}</span><h2>为什么进入候选列表？</h2><p class="signal-reason">{{ selectionReason(stock, activeStrategy) }}</p>
        <div v-if="isWaitingStrategy(activeStrategy) && metrics.rangeLower != null && metrics.rangeUpper != null" class="waiting-range"><div class="waiting-range-heading"><strong>整理区间与现价</strong><span class="tag tag-amber">等待中</span></div><div class="waiting-range-labels"><span>下沿 <b>{{ formatNumber(metrics.rangeLower) }}</b></span><span>上沿 <b>{{ formatNumber(metrics.rangeUpper) }}</b></span></div><div class="waiting-range-track"><span :style="{ width: `${rangePosition}%` }" /><i :style="{ left: `${rangePosition}%` }" /></div><p>收盘超过 {{ formatNumber(metrics.rangeUpper) }} 元后，再检查短线全部条件。</p></div>
        <div class="signal-metrics"><div><span>{{ strategy.metricName }}</span><strong>{{ strategyMetric(stock, activeStrategy) }}</strong></div><div v-for="[key, value] in displayedMetrics" :key="key"><span>{{ metricLabels[key] }}</span><strong>{{ formatNumber(value) }}</strong></div></div>
        <div class="warning-note"><strong>何时复查该次信号</strong><p>{{ strategy.invalid }}</p></div>
      </div><div class="conditions-card"><h3>策略入选条件</h3><ul><li v-for="condition in strategy.conditions" :key="condition"><AppIcon name="check" :size="16" /><span>{{ condition }}</span></li></ul><p>数值和入选理由采用本次快照保存的计算结果。</p></div></div>
    </section>
    <ChipsPanel v-else-if="tab === 'chips'" :stock="stock" />
    <AnalysisPanel v-else :stock="stock" />
    <p class="page-footnote"><AppIcon name="info" :size="14" />不同快照的价格、信号和报告分别保留，请以当前数据日期为准。</p>
  </template>
</template>
