<!-- 本文件组织个股概览、模拟 K 线、策略快照和按需 AI 分析。 -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import StockChart from '../components/StockChart.vue';
import AnalysisPanel from '../components/AnalysisPanel.vue';
import { BOARDS } from '../boards.ts';
import { DATA_DATE, STRATEGIES, distanceToUpper, isWaitingStrategy, selectionReason, strategyMetric } from '../mock/fixtures.ts';
import { getStock } from '../mock/service.ts';
import type { Stock, Strategy } from '../types.ts';
const route = useRoute();
const router = useRouter();
const stock = ref<Stock>();
const loading = ref(true);
const error = ref('');
const activeStrategy = ref<Strategy>('short');
const strategy = computed(() => STRATEGIES[activeStrategy.value]);
/** 将现价映射到固定整理区间，轨道仅展示价格位置。 */
const rangePosition = computed(() => {
    const value = stock.value;
    if (!value?.waiting) return 0;
    return Math.max(0, Math.min(100, (value.price - value.waiting.lower) / (value.waiting.upper - value.waiting.lower) * 100));
});
/** 在脚本中计算策略名称，保留模板表达式的准确类型。 */
const strategyNames = computed(() => stock.value?.strategies.map((key) => STRATEGIES[key].name).join(' / ') ?? '');
const tab = ref<'rules' | 'ai'>('rules');
const back = computed(() => typeof route.query.back === 'string' && route.query.back.startsWith('/opportunities') ? route.query.back : '/opportunities');
async function load(): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
        stock.value = await getStock(String(route.params.code));
        activeStrategy.value = stock.value.strategies.includes(route.query.strategy as Strategy) ? route.query.strategy as Strategy : stock.value.strategies[0]!;
    }
    catch (cause) {
        error.value = (cause as Error).message;
    }
    finally {
        loading.value = false;
    }
}
async function openAi(): Promise<void> { tab.value = 'ai'; await nextTick(); document.getElementById('ai-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
watch(activeStrategy, () => { void router.replace({ query: { ...route.query, strategy: activeStrategy.value } }); });
void load();
</script>
<template>
  <router-link :to="back" class="back-link">
    <AppIcon name="back" :size="17" />
    返回机会列表
  </router-link>
  <StatePanel v-if="loading" kind="loading" title="正在读取个股详情" />
  <StatePanel
    v-else-if="error || !stock"
    kind="error"
    title="无法打开这只股票"
    :description="error"
    action="返回机会列表"
    @action="router.push(back)"
  />
  <template v-else>
    <div class="stock-heading">
      <div>
        <div class="stock-title-row">
          <h1>{{ stock.name }}</h1>
          <span class="tag">{{ BOARDS[stock.board].name }} {{ stock.code }}</span>
          <span class="tag">{{ stock.industry }}</span>
        </div>
        <div class="concept-tags">
          <span v-for="concept in stock.concepts" :key="concept">{{ concept }}</span>
          <span>数据日期 {{ DATA_DATE }}</span>
        </div>
      </div>
      <el-button type="primary" @click="openAi">
        <AppIcon name="spark" :size="17" />
        发起 AI 分析
      </el-button>
    </div>
    <div class="quote-row">
      <div class="main-quote" :class="stock.change >= 0 ? 'rise' : 'fall'">
        <strong>{{ stock.price.toFixed(2) }}</strong>
        <span>{{ stock.change >= 0 ? '+' : '' }}{{ stock.change.toFixed(2) }}%</span>
        <small>收盘价 / 元</small>
      </div>
      <div>
        <span>换手率</span>
        <strong>{{ stock.turnover.toFixed(2) }}%</strong>
      </div>
      <div>
        <span>成交额</span>
        <strong>
          {{ stock.amount.toFixed(1) }}
          <small>亿元</small>
        </strong>
      </div>
      <div>
        <span>总市值</span>
        <strong>
          {{ stock.totalMarketCap === null ? '—' : stock.totalMarketCap.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }}
          <small v-if="stock.totalMarketCap !== null">亿元</small>
        </strong>
      </div>
      <div>
        <span>所属策略</span>
        <strong class="quote-strategies">{{ strategyNames }}</strong>
      </div>
      <div>
        <span>数据口径</span>
        <strong class="quote-strategies">前复权 · 模拟</strong>
      </div>
    </div>
    <StockChart :stock="stock" />
    <div class="detail-tabs">
      <button :class="{ active: tab === 'rules' }" @click="tab = 'rules'">规则筛选结果</button>
      <button :class="{ active: tab === 'ai' }" @click="tab = 'ai'">
        AI 分析报告
        <span class="tiny-label">按需生成</span>
      </button>
    </div>
    <section v-if="tab === 'rules'" class="panel signal-panel">
      <div class="section-caption">
        <div>
          <strong>入选信号快照</strong>
          <span class="muted">{{ DATA_DATE }} · 示例信号</span>
        </div>
        <div class="segment-control">
          <button
            v-for="key in stock.strategies"
            :key="key"
            :class="{ active: activeStrategy === key }"
            @click="activeStrategy = key"
          >{{ STRATEGIES[key].name }}</button>
        </div>
      </div>
      <div class="signal-layout">
        <div>
          <span class="eyebrow">{{ strategy.name }} · {{ strategy.subtitle }}</span>
          <h2>为什么进入候选列表？</h2>
          <p class="signal-reason">{{ selectionReason(stock, activeStrategy) }}</p>
          <div v-if="isWaitingStrategy(activeStrategy) && stock.waiting" class="waiting-range">
            <div class="waiting-range-heading">
              <strong>整理区间与现价</strong>
              <span class="tag tag-amber">等待中</span>
            </div>
            <div class="waiting-range-labels">
              <span>下沿 P <b>{{ stock.waiting.lower.toFixed(2) }}</b></span>
              <span>上沿 B <b>{{ stock.waiting.upper.toFixed(2) }}</b></span>
            </div>
            <div class="waiting-range-track" role="img" :aria-label="`区间${stock.waiting.lower.toFixed(2)}至${stock.waiting.upper.toFixed(2)}元，现价${stock.price.toFixed(2)}元`">
              <span :style="{ width: `${rangePosition}%` }" />
              <i :style="{ left: `${rangePosition}%` }" />
            </div>
            <p>现价 {{ stock.price.toFixed(2) }} 元 · 距上沿 {{ distanceToUpper(stock).toFixed(1) }}% · 区间宽度 {{ ((stock.waiting.upper / stock.waiting.lower - 1) * 100).toFixed(1) }}%</p>
            <p>突破观察：收盘超过 {{ stock.waiting.upper.toFixed(2) }} 元后，再检查短线全部条件。</p>
          </div>
          <div class="signal-metrics">
            <div v-if="activeStrategy === 'short'">
              <span>突破参考价 B</span>
              <strong>{{ stock.reference.toFixed(2) }}</strong>
            </div>
            <div v-if="activeStrategy === 'swing'">
              <span>回调参考低点 P</span>
              <strong>{{ stock.reference.toFixed(2) }}</strong>
            </div>
            <div>
              <span>{{ strategy.metricName }}</span>
              <strong>{{ strategyMetric(stock, activeStrategy) }}</strong>
            </div>
            <div v-if="isWaitingStrategy(activeStrategy) && stock.waiting">
              <span>近5日／近20日均额</span>
              <strong>{{ (stock.waiting.amountRatio * 100).toFixed(0) }}%</strong>
            </div>
            <div>
              <span>观察周期</span>
              <strong class="small-metric">{{ strategy.period }}</strong>
            </div>
          </div>
          <div class="warning-note">
            <strong>
              <AppIcon name="info" :size="16" />
              何时复查该次信号
            </strong>
            <p>{{ strategy.invalid }}</p>
          </div>
        </div>
        <div class="conditions-card">
          <h3>策略入选条件</h3>
          <ul>
            <li v-for="condition in strategy.conditions" :key="condition">
              <AppIcon name="check" :size="16" />
              <span>{{ condition }}</span>
            </li>
          </ul>
          <p>此处演示条件展示结构，样例未经过真实策略计算或回测。</p>
        </div>
      </div>
    </section>
    <AnalysisPanel v-else :stock="stock" :strategy="activeStrategy" />
    <p class="page-footnote">
      <AppIcon name="info" :size="14" />
      示例价格、走势、信号和报告仅用于交互评审。
    </p>
  </template>
</template>
