<!-- 本文件展示五类策略机会，支持筛选、排序、分页和完整请求状态。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import { BOARDS, isBoard } from '../boards.ts';
import { MARKET_CAP_RANGES, TURNOVER_RANGES, withSavedRange } from '../filter-ranges.ts';
import type { RangeOption } from '../filter-ranges.ts';
import { STRATEGIES, STRATEGY_ORDER, isWaitingStrategy, selectionReason, strategyMetric } from '../strategies.ts';
import { ApiError, filters, formatNumber, listStocks, quoteClass } from '../api.ts';
import type { LiveStock as Stock } from '../api.ts';
import type { Board, StockQuery, Strategy } from '../types.ts';
const route = useRoute();
const router = useRouter();
const query = reactive<StockQuery>({ strategy: 'short', search: '', industry: '', concept: '', board: '', minTurnover: undefined, maxTurnover: undefined, minMarketCap: undefined, maxMarketCap: undefined, sort: 'default', descending: true });
const rows = ref<Stock[]>([]);
const loading = ref(true);
const error = ref('');
const page = ref(1);
const pageSize = 6;
const total = ref(0);
const snapshotId = ref('');
const dataDate = ref('');
const catalogAsOfAt = ref<string | null>(null);
const needsData = ref(false);
const counts = ref<Partial<Record<Strategy, number>>>({});
const industryCount = ref(0);
let loadedFilters = '';
let restoring = false;
let leaving = false;
const strategyOpen = ref(false);
let requestId = 0;
let debounce: ReturnType<typeof setTimeout> | undefined;
const strategies = STRATEGY_ORDER;
const industries = ref<{ id: string; name: string }[]>([]);
const concepts = ref<{ id: string; name: string }[]>([]);
const activeBoards = ref<Board[]>([]);
const coverage = ref<Record<string, string>>({});
const strategy = computed(() => STRATEGIES[query.strategy]);
const visibleRows = computed(() => rows.value);
const invalidTurnoverRange = computed(() => query.minTurnover !== undefined && query.maxTurnover !== undefined && query.minTurnover > query.maxTurnover);
const invalidMarketCapRange = computed(() => query.minMarketCap !== undefined && query.maxMarketCap !== undefined && query.minMarketCap > query.maxMarketCap);
const invalidRange = computed(() => invalidTurnoverRange.value || invalidMarketCapRange.value);
/** 区分用户筛选后的空结果与当日策略本身没有入选股票。 */
const hasFilters = computed(() => Boolean(query.search || query.industry || query.concept || query.board)
    || [query.minTurnover, query.maxTurnover, query.minMarketCap, query.maxMarketCap].some(value => value !== undefined));
const noStrategyMatches = computed(() => !hasFilters.value && !invalidRange.value && total.value === 0);
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
    restoring = true;
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
    snapshotId.value = read('snapshotId');
    restoring = false;
}
function syncUrl(): void {
    if (leaving || route.path !== '/opportunities') return;
    const values: Record<string, string> = { strategy: query.strategy };
    if (snapshotId.value) values.snapshotId = snapshotId.value;
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
    if (leaving) return;
    const id = ++requestId;
    loading.value = true;
    error.value = '';
    needsData.value = false;
    try {
        if (invalidRange.value) {
            rows.value = [];
            return;
        }
        const result = await listStocks({ ...query }, page.value, pageSize, snapshotId.value || undefined);
        if (id !== requestId)
            return;
        rows.value = result.items;
        total.value = result.total;
        counts.value = result.summary.strategyCounts;
        industryCount.value = result.summary.industryCount;
        snapshotId.value = result.snapshot.id;
        dataDate.value = result.snapshot.dataDate;
        catalogAsOfAt.value = result.snapshot.catalogAsOfAt ?? null;
        if (loadedFilters !== result.snapshot.id) {
            const options = await filters(result.snapshot.id);
            if (id !== requestId) return;
            industries.value = options.industries; concepts.value = options.concepts; activeBoards.value = options.boards.map(board => board.id); coverage.value = options.coverage; loadedFilters = result.snapshot.id;
        }
        syncUrl();
    }
    catch (cause) {
        if (id === requestId) {
            if (cause instanceof ApiError && cause.code === 'SNAPSHOT_NOT_FOUND' && snapshotId.value) {
                // 清库后的旧链接自动读取最新快照，避免持续请求已不存在的数据。
                snapshotId.value = '';
                loadedFilters = '';
                await load();
                return;
            }
            error.value = (cause as Error).message;
            needsData.value = cause instanceof ApiError && ['DATA_NOT_READY', 'DATA_NOT_INITIALIZED'].includes(cause.code);
        }
    }
    finally {
        if (id === requestId)
            loading.value = false;
    }
}
function resetFilters(): void { Object.assign(query, { search: '', industry: '', concept: '', board: '', minTurnover: undefined, maxTurnover: undefined, minMarketCap: undefined, maxMarketCap: undefined, sort: 'default', descending: true }); page.value = 1; void load(); }
/** 列表最多显示三个题材，优先保留当前选中的题材，完整归属可悬浮查看。 */
function visibleConcepts(stock: Stock): string[] {
    const selected = concepts.value.find(item => item.id === query.concept)?.name;
    return (selected && stock.concepts.includes(selected) ? [selected, ...stock.concepts.filter(name => name !== selected)] : stock.concepts).slice(0, 3);
}
function sortBy(key: StockQuery['sort']): void { query.descending = query.sort === key ? !query.descending : true; query.sort = key; }
function openStock(stock: Stock): void { void router.push({ path: `/stocks/${stock.stockId}`, query: { strategy: query.strategy, snapshotId: stock.snapshotId, back: route.fullPath } }); }
restore();
void load();
watch(query, () => { if (restoring) return; page.value = 1; syncUrl(); clearTimeout(debounce); ++requestId; loading.value = true; debounce = setTimeout(() => { void load(); }, 220); }, { flush: 'sync' });
watch(page, () => { if (!restoring) { syncUrl(); void load(); } }, { flush: 'sync' });
watch(() => route.query, () => {
    if (leaving || route.path !== '/opportunities') return;
    const before = JSON.stringify([query, page.value, snapshotId.value]);
    restore();
    if (before !== JSON.stringify([query, page.value, snapshotId.value])) {
        clearTimeout(debounce);
        void load();
    }
});
// 路由守卫可能异步等待会话；提前终止旧页回写，避免迟到请求取消新导航。
onBeforeRouteLeave(() => { leaving = true; ++requestId; clearTimeout(debounce); });
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
        <small>{{ dataDate || '等待发布' }}</small>
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
      <b>{{ counts[key] ?? '—' }}</b>
    </button>
  </div>
  <p class="muted">各策略数量随筛选条件变化；当日没有符合规则的股票时显示 0。</p>
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
        {{ loading || error ? '—' : total }}
        <small>只股票</small>
      </strong>
      <p>同一股票可符合多个策略</p>
    </div>
    <div class="summary-card">
      <span>覆盖行业</span>
      <strong>
        {{ loading || error ? '—' : industryCount }}
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
        <el-select v-model="query.industry" :placeholder="coverage.industry === 'unavailable' ? '此快照行业未知' : '全部行业（可搜索）'" :disabled="coverage.industry === 'unavailable'" clearable filterable aria-label="行业">
          <el-option v-for="industry in industries" :key="industry.id" :label="industry.name" :value="industry.id" />
        </el-select>
      </label>
      <label>
        题材 / 概念
        <el-select v-model="query.concept" :placeholder="coverage.concepts === 'unavailable' ? '题材尚未同步' : '全部概念（可搜索）'" :disabled="coverage.concepts === 'unavailable'" clearable filterable aria-label="题材 / 概念">
          <el-option v-for="concept in concepts" :key="concept.id" :label="concept.name" :value="concept.id" />
        </el-select>
      </label>
      <label>
        上市板块
        <el-select v-model="query.board" placeholder="全部板块" clearable>
          <el-option v-for="id in activeBoards" :key="id" :label="BOARDS[id].name" :value="id" />
        </el-select>
      </label>
      <label>
        换手率
        <el-select v-model="turnoverRange" placeholder="不限" :disabled="coverage.turnover === 'unavailable'" clearable aria-label="换手率区间">
          <el-option v-for="option in turnoverOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </label>
      <label>
        总市值
        <el-select v-model="marketCapRange" :placeholder="coverage.totalMarketCap === 'unavailable' ? '此快照市值未知' : '不限'" :disabled="coverage.totalMarketCap === 'unavailable'" clearable aria-label="总市值区间">
          <el-option v-for="option in marketCapOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </label>
    </div>
    <p v-if="coverage.concepts === 'unavailable'" class="muted">题材数据尚未同步，暂不能按题材筛选。</p>
    <p v-else-if="coverage.concepts === 'partial'" class="muted">部分股票的题材仍在补齐，筛选结果仅包含已同步的归属。</p>
    <p v-if="invalidTurnoverRange" class="field-error">最低换手率不能大于最高换手率。</p>
    <p v-if="invalidMarketCapRange" class="field-error">最低总市值不能大于最高总市值。</p>
  </section>
  <section class="panel results-panel">
    <div class="section-caption results-caption">
      <div>
        <strong>{{ strategy.name }}机会列表</strong>
        <span class="muted">{{ loading ? '正在筛选' : `${total} 只符合当前条件` }}</span>
      </div>
      <span class="tiny-label">人民币 / 收盘数据</span>
    </div>
    <StatePanel v-if="loading" kind="loading" title="正在筛选机会" description="正在读取已发布收盘快照…" />
    <StatePanel
      v-else-if="error"
      kind="error"
      :title="needsData ? '收盘数据尚未准备好' : '列表加载失败'"
      :description="error"
      action="重试"
      @action="load"
    />
    <StatePanel
      v-else-if="!rows.length"
      :title="noStrategyMatches ? `当日暂无${strategy.name}机会` : '暂时没有符合条件的股票'"
      :description="invalidRange ? '请先调整总市值或换手率范围。' : noStrategyMatches ? '当日没有股票同时满足这类策略的全部条件，可查看其他策略或等待下次数据更新。' : '可重置筛选，或查看其他策略。'"
      :action="noStrategyMatches ? undefined : '重置筛选'"
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
                  <span class="board-tag">{{ stock.board ? BOARDS[stock.board].name : '未知板块' }}</span>
                  <span>{{ stock.industry }}</span>
                </div>
                <div class="strategy-tags" aria-label="所属策略">
                  <span v-for="key in stock.strategies" :key="key" :class="{ selected: key === query.strategy }">{{ STRATEGIES[key].name }}</span>
                </div>
              </td>
              <td class="numeric">{{ formatNumber(stock.price) }}</td>
              <td class="numeric" :class="quoteClass(stock.change)">{{ stock.change !== null && stock.change >= 0 ? '+' : '' }}{{ formatNumber(stock.change) }}%</td>
              <td class="numeric">{{ formatNumber(stock.turnover) }}%</td>
              <td class="numeric">
                {{ formatNumber(stock.amount, 1) }}
                <small>亿</small>
              </td>
              <td class="numeric">{{ stock.totalMarketCap === null ? '—' : stock.totalMarketCap.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }}</td>
              <td>
                <span class="metric-pill">{{ strategyMetric(stock, query.strategy) }}</span>
              </td>
              <td class="reason-cell">
                <span>{{ selectionReason(stock, query.strategy) }}</span>
                <div class="concept-tags">
                  <span v-for="concept in visibleConcepts(stock)" :key="concept">{{ concept }}</span>
                  <el-tooltip v-if="stock.concepts.length > 3" :content="stock.concepts.join('、')" placement="top">
                    <span>+{{ stock.concepts.length - 3 }}</span>
                  </el-tooltip>
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
          :total="total"
          layout="prev, pager, next"
          small
        />
      </div>
    </template>
  </section>
  <p class="page-footnote">
    <AppIcon name="info" :size="14" />
    信号基于所选收盘快照计算；新快照发布不会改写当前快照的历史结果。
    <span v-if="catalogAsOfAt">分类资料获取截至 {{ new Date(catalogAsOfAt).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' }) }}（北京时间），可能晚于行情日期。</span>
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
    <p class="muted">共用规则：剔除当日停牌、ST、退市整理、历史不足等情况；近 20 个交易日日均成交额不低于 3,000 万元。所有条件与参考价由后端计算。</p>
  </el-dialog>
</template>
