<!-- 本文件展示五类策略机会，支持筛选、排序、分页和完整请求状态。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import { BOARDS, isBoard } from '../boards.ts';
import { MARKET_CAP_RANGES, TURNOVER_RANGES, withSavedRange } from '../filter-ranges.ts';
import type { RangeOption } from '../filter-ranges.ts';
import { DATA_DATE, STOCKS, STRATEGIES, STRATEGY_ORDER, isWaitingStrategy, selectionReason, strategyMetric } from '../mock/fixtures.ts';
import { listStocks } from '../mock/service.ts';
import { useReview } from '../stores.ts';
import type { Stock, StockQuery, Strategy } from '../types.ts';
const route = useRoute();
const router = useRouter();
const review = useReview();
const query = reactive<StockQuery>({ strategy: 'short', search: '', industry: '', concept: '', board: '', minTurnover: undefined, maxTurnover: undefined, minMarketCap: undefined, maxMarketCap: undefined, sort: 'default', descending: true });
const rows = ref<Stock[]>([]);
const loading = ref(true);
const error = ref('');
const page = ref(1);
const pageSize = 6;
const strategyOpen = ref(false);
let requestId = 0;
let debounce: ReturnType<typeof setTimeout> | undefined;
const strategies = STRATEGY_ORDER;
const industries = [...new Set(STOCKS.map((stock) => stock.industry))];
const concepts = [...new Set(STOCKS.flatMap((stock) => stock.concepts))];
const strategy = computed(() => STRATEGIES[query.strategy]);
const visibleRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize));
const industryCount = computed(() => new Set(rows.value.map((stock) => stock.industry)).size);
const invalidTurnoverRange = computed(() => query.minTurnover !== undefined && query.maxTurnover !== undefined && query.minTurnover > query.maxTurnover);
const invalidMarketCapRange = computed(() => query.minMarketCap !== undefined && query.maxMarketCap !== undefined && query.minMarketCap > query.maxMarketCap);
const invalidRange = computed(() => invalidTurnoverRange.value || invalidMarketCapRange.value);
const metricName = computed(() => strategy.value.metricName);
const metricHint = computed(() => strategy.value.metricHint);
/** 将下拉选项与现有的范围参数双向关联，清空时恢复为不限。 */
function useRangeSelection(presets: readonly RangeOption[], minField: 'minTurnover' | 'minMarketCap', maxField: 'maxTurnover' | 'maxMarketCap', unit: string) {
    const options = computed(() => withSavedRange(presets, query[minField], query[maxField], unit));
    const selection = computed({
        get: () => options.value.find((option) => option.min === query[minField] && option.max === query[maxField])!.value,
        set: (value: string) => {
            const option = options.value.find((item) => item.value === value);
            query[minField] = option?.min;
            query[maxField] = option?.max;
        },
    });
    return { options, selection };
}
const { options: turnoverOptions, selection: turnoverRange } = useRangeSelection(TURNOVER_RANGES, 'minTurnover', 'maxTurnover', '%');
const { options: marketCapOptions, selection: marketCapRange } = useRangeSelection(MARKET_CAP_RANGES, 'minMarketCap', 'maxMarketCap', '亿元');
/** 从 URL 恢复筛选，支持详情返回、浏览器前进后退与刷新。 */
function restore(): void {
    const read = (key: string): string => typeof route.query[key] === 'string' ? route.query[key] as string : '';
    const numeric = (key: string): number | undefined => { const value = read(key); return value && Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : undefined; };
    query.strategy = strategies.includes(read('strategy') as Strategy) ? read('strategy') as Strategy : 'short';
    query.search = read('search');
    query.industry = read('industry');
    query.concept = read('concept');
    const board = read('board');
    query.board = isBoard(board) ? board : '';
    query.minTurnover = numeric('min');
    query.maxTurnover = numeric('max');
    query.minMarketCap = numeric('minMarketCap');
    query.maxMarketCap = numeric('maxMarketCap');
    query.sort = ['change', 'turnover', 'amount', 'totalMarketCap'].includes(read('sort')) ? read('sort') as StockQuery['sort'] : 'default';
    query.descending = read('order') !== 'asc';
    page.value = Math.max(1, Number.parseInt(read('page'), 10) || 1);
}
function syncUrl(): void {
    const values: Record<string, string> = { strategy: query.strategy };
    for (const key of ['search', 'industry', 'concept', 'board'] as const)
        if (query[key])
            values[key] = query[key];
    if (query.minTurnover !== undefined)
        values.min = String(query.minTurnover);
    if (query.maxTurnover !== undefined)
        values.max = String(query.maxTurnover);
    if (query.minMarketCap !== undefined)
        values.minMarketCap = String(query.minMarketCap);
    if (query.maxMarketCap !== undefined)
        values.maxMarketCap = String(query.maxMarketCap);
    if (query.sort !== 'default') {
        values.sort = query.sort;
        values.order = query.descending ? 'desc' : 'asc';
    }
    if (page.value > 1)
        values.page = String(page.value);
    void router.replace({ query: values });
}
async function load(): Promise<void> {
    const id = ++requestId;
    loading.value = true;
    error.value = '';
    try {
        if (invalidRange.value) {
            rows.value = [];
            return;
        }
        const result = await listStocks({ ...query }, review.listScenario);
        if (id !== requestId)
            return;
        rows.value = result;
        page.value = Math.min(page.value, Math.max(1, Math.ceil(result.length / pageSize)));
    }
    catch (cause) {
        if (id === requestId)
            error.value = (cause as Error).message;
    }
    finally {
        if (id === requestId)
            loading.value = false;
    }
}
function resetFilters(): void { Object.assign(query, { search: '', industry: '', concept: '', board: '', minTurnover: undefined, maxTurnover: undefined, minMarketCap: undefined, maxMarketCap: undefined, sort: 'default', descending: true }); page.value = 1; review.listScenario = 'normal'; }
function sortBy(key: StockQuery['sort']): void { query.descending = query.sort === key ? !query.descending : true; query.sort = key; }
function openStock(stock: Stock): void { void router.push({ path: `/stocks/${stock.code}`, query: { strategy: query.strategy, back: route.fullPath } }); }
restore();
void load();
watch(query, () => { page.value = 1; syncUrl(); clearTimeout(debounce); ++requestId; loading.value = true; debounce = setTimeout(() => { void load(); }, 220); });
watch(page, syncUrl);
watch(() => route.query, () => {
    const before = JSON.stringify(query);
    restore();
    if (before !== JSON.stringify(query)) {
        clearTimeout(debounce);
        void load();
    }
});
watch(() => review.listScenario, () => { page.value = 1; void load(); });
onBeforeUnmount(() => { ++requestId; clearTimeout(debounce); });
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">OPPORTUNITY SCREENER</span>
      <h1>发现值得研究的机会</h1>
      <p>从规则出发，让下一步研究更有方向。</p>
    </div>
    <div class="date-chip">
      <span class="status-dot" />
      <span>
        收盘数据
        <small>{{ DATA_DATE }} · 固定示例</small>
      </span>
    </div>
  </div>
  <div class="strategy-tabs" role="tablist" aria-label="选择策略">
    <button
      v-for="key in strategies"
      :key="key"
      role="tab"
      :aria-selected="query.strategy === key"
      :class="{ active: query.strategy === key }"
      @click="query.strategy = key"
    >
      <strong>{{ STRATEGIES[key].name }}机会</strong>
      <span>{{ STRATEGIES[key].subtitle }}</span>
      <b>{{ STOCKS.filter(stock => stock.strategies.includes(key)).length }}</b>
    </button>
  </div>
  <section class="strategy-banner">
    <span class="banner-icon">
      <AppIcon :name="strategy.icon" :size="25" />
    </span>
    <div>
      <strong>{{ strategy.subtitle }}</strong>
      <p>
        {{ strategy.description }}
        <span class="period-inline">观察周期 {{ strategy.period }}</span>
      </p>
    </div>
    <span v-if="isWaitingStrategy(query.strategy)" class="tag tag-amber">等待中</span>
    <el-button text @click="strategyOpen = true">
      查看策略规则
      <AppIcon name="arrow" :size="16" />
    </el-button>
  </section>
  <div class="summary-grid">
    <div class="summary-card">
      <span>筛选结果</span>
      <strong>
        {{ loading ? '—' : rows.length }}
        <small>只股票</small>
      </strong>
      <p>同一股票可符合多个策略</p>
    </div>
    <div class="summary-card">
      <span>覆盖行业</span>
      <strong>
        {{ loading ? '—' : industryCount }}
        <small>个行业</small>
      </strong>
      <p>按行业进一步缩小研究范围</p>
    </div>
    <div class="summary-card">
      <span>当前排序</span>
      <strong class="summary-text">{{ query.sort === 'default' ? metricHint : ({ change: '涨跌幅', turnover: '换手率', amount: '成交额', totalMarketCap: '总市值' }[query.sort] + (query.descending ? '从高到低' : '从低到高')) }}</strong>
      <p>{{ query.sort === 'default' ? '使用策略默认排序' : '点击表头可切换排序方向' }}</p>
    </div>
  </div>
  <section class="panel filter-panel">
    <div class="section-caption">
      <strong>
        <AppIcon name="sliders" :size="17" />
        筛选条件
      </strong>
      <el-button text @click="resetFilters">重置筛选</el-button>
    </div>
    <div class="filter-grid">
      <label>
        股票搜索
        <el-input v-model="query.search" placeholder="股票名称或代码" clearable>
          <template #prefix>
            <AppIcon name="search" :size="16" />
          </template>
        </el-input>
      </label>
      <label>
        行业
        <el-select v-model="query.industry" placeholder="全部行业" clearable>
          <el-option v-for="industry in industries" :key="industry" :label="industry" :value="industry" />
        </el-select>
      </label>
      <label>
        题材 / 概念
        <el-select v-model="query.concept" placeholder="全部概念" clearable>
          <el-option v-for="concept in concepts" :key="concept" :label="concept" :value="concept" />
        </el-select>
      </label>
      <label>
        上市板块
        <el-select v-model="query.board" placeholder="全部板块" clearable>
          <el-option v-for="(board, id) in BOARDS" :key="id" :label="board.name" :value="id" />
        </el-select>
      </label>
      <label>
        换手率
        <el-select v-model="turnoverRange" placeholder="不限" clearable aria-label="换手率区间">
          <el-option v-for="option in turnoverOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </label>
      <label>
        总市值
        <el-select v-model="marketCapRange" placeholder="不限" clearable aria-label="总市值区间">
          <el-option v-for="option in marketCapOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </label>
    </div>
    <p v-if="invalidTurnoverRange" class="field-error">最低换手率不能大于最高换手率。</p>
    <p v-if="invalidMarketCapRange" class="field-error">最低总市值不能大于最高总市值。</p>
  </section>
  <section class="panel results-panel">
    <div class="section-caption results-caption">
      <div>
        <strong>{{ strategy.name }}机会列表</strong>
        <span class="muted">{{ loading ? '正在筛选' : `${rows.length} 只符合当前条件` }}</span>
      </div>
      <span class="tiny-label">人民币 / 示例数据</span>
    </div>
    <StatePanel v-if="loading" kind="loading" title="正在筛选机会" description="正在读取模拟收盘数据…" />
    <StatePanel
      v-else-if="error"
      kind="error"
      title="列表加载失败"
      :description="error"
      action="重试"
      @action="review.listScenario = 'normal'"
    />
    <StatePanel
      v-else-if="!rows.length"
      title="暂时没有符合条件的股票"
      :description="invalidRange ? '请先调整总市值或换手率范围。' : '试试调整板块、行业、概念或数值范围。'"
      action="重置筛选"
      @action="resetFilters"
    />
    <template v-else>
      <div class="table-scroll">
        <table class="stock-table">
          <thead>
            <tr>
              <th>股票 / 行业</th>
              <th>收盘价</th>
              <th>
                <button @click="sortBy('change')">涨跌幅 {{ query.sort === 'change' ? (query.descending ? '↓' : '↑') : '↕' }}</button>
              </th>
              <th>
                <button @click="sortBy('turnover')">换手率 {{ query.sort === 'turnover' ? (query.descending ? '↓' : '↑') : '↕' }}</button>
              </th>
              <th>
                <button @click="sortBy('amount')">成交额 {{ query.sort === 'amount' ? (query.descending ? '↓' : '↑') : '↕' }}</button>
              </th>
              <th>
                <button @click="sortBy('totalMarketCap')">总市值（亿元） {{ query.sort === 'totalMarketCap' ? (query.descending ? '↓' : '↑') : '↕' }}</button>
              </th>
              <th>{{ metricName }}</th>
              <th>入选理由</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="stock in visibleRows" :key="stock.code">
              <td>
                <button class="stock-name" @click="openStock(stock)">{{ stock.name }}</button>
                <div class="stock-meta">
                  <span>{{ stock.code }}</span>
                  <span class="board-tag">{{ BOARDS[stock.board].name }}</span>
                  <span>{{ stock.industry }}</span>
                </div>
                <div class="strategy-tags" aria-label="所属策略">
                  <span v-for="key in stock.strategies" :key="key" :class="{ selected: key === query.strategy }">{{ STRATEGIES[key].name }}</span>
                </div>
              </td>
              <td class="numeric">{{ stock.price.toFixed(2) }}</td>
              <td class="numeric" :class="stock.change >= 0 ? 'rise' : 'fall'">{{ stock.change >= 0 ? '+' : '' }}{{ stock.change.toFixed(2) }}%</td>
              <td class="numeric">{{ stock.turnover.toFixed(2) }}%</td>
              <td class="numeric">
                {{ stock.amount.toFixed(1) }}
                <small>亿</small>
              </td>
              <td class="numeric">{{ stock.totalMarketCap === null ? '—' : stock.totalMarketCap.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }}</td>
              <td>
                <span class="metric-pill">{{ strategyMetric(stock, query.strategy) }}</span>
              </td>
              <td class="reason-cell">
                <span>{{ selectionReason(stock, query.strategy) }}</span>
                <div class="concept-tags">
                  <span v-for="concept in stock.concepts" :key="concept">{{ concept }}</span>
                </div>
              </td>
              <td>
                <button class="icon-button" :aria-label="`查看${stock.name}详情`" @click="openStock(stock)">
                  <AppIcon name="arrow" :size="17" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-footer">
        <span>规则入选结果与 AI 分析分别展示</span>
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="rows.length"
          layout="prev, pager, next"
          small
        />
      </div>
    </template>
  </section>
  <p class="page-footnote">
    <AppIcon name="info" :size="14" />
    信号按收盘数据观察。当前指标未经真实策略计算，仅供产品交互评审。
  </p>
  <el-dialog v-model="strategyOpen" :title="`${strategy.name}策略 · ${strategy.subtitle}`" width="min(600px, 92vw)">
    <p class="muted">观察周期：{{ strategy.period }}。真实策略需满足全部入选条件。</p>
    <ol class="condition-list">
      <li v-for="condition in strategy.conditions" :key="condition">{{ condition }}</li>
    </ol>
    <p v-if="isWaitingStrategy(query.strategy)" class="muted">后续收盘超过本次区间上沿 B 时结束区间内等待，再检查短线全部条件。等待策略与短线同日互斥；趋势埋伏可与波段、长线同时入选。</p>
    <div class="warning-note">
      <strong>失效条件</strong>
      <p>{{ strategy.invalid }}</p>
    </div>
    <p class="muted">共用规则：剔除当日停牌、ST、退市整理、历史不足等情况；近 20 个交易日日均成交额不低于 3,000 万元。原型使用固定样例，不执行这些计算。</p>
  </el-dialog>
</template>
