<!-- 本文件从完整股票目录搜索个股，名称和代码共用一个入口，与策略入选结果无关。 -->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import { BOARDS } from '../boards.ts';
import { searchStocks } from '../api.ts';
import type { StockMatch } from '../api.ts';
const route = useRoute();
const router = useRouter();
const search = ref(typeof route.query.search === 'string' ? route.query.search : '');
const rows = ref<StockMatch[]>([]);
const page = ref(Math.max(1, Number.parseInt(String(route.query.page ?? '1'), 10) || 1));
const total = ref(0);
const loading = ref(false);
const error = ref('');
let requestId = 0;
let leaving = false;
let timer: ReturnType<typeof setTimeout> | undefined;
/** 请求只查询完整目录；通过请求序号丢弃快速输入后迟到的旧结果。 */
async function load(): Promise<void> {
    if (leaving) return;
    clearTimeout(timer);
    const id = ++requestId;
    const text = search.value.trim();
    rows.value = []; total.value = 0; error.value = '';
    if (!text) { loading.value = false; return; }
    loading.value = true;
    try {
        const result = await searchStocks(text, page.value);
        if (id !== requestId) return;
        rows.value = result.items; total.value = result.total;
    } catch (cause) { if (id === requestId) error.value = (cause as Error).message; }
    finally { if (id === requestId) loading.value = false; }
}
/** 后续详情和报告只使用已确认的股票ID，保留搜索位置供返回。 */
function openStock(stock: StockMatch): void {
    void router.push({ path: `/stocks/${stock.stockId}`, query: { entry: 'analysis', tab: 'ai', back: route.fullPath } });
}
/** 搜索词和页码保存到URL，刷新或从详情返回时恢复查询。 */
function syncUrl(): void {
    if (leaving || route.path !== '/analysis') return;
    void router.replace({ query: { ...(search.value.trim() ? { search: search.value } : {}), ...(page.value > 1 ? { page: String(page.value) } : {}) } });
}
watch(search, () => { page.value = 1; ++requestId; loading.value = !!search.value.trim(); syncUrl(); clearTimeout(timer); timer = setTimeout(load, 250); });
watch(page, () => { syncUrl(); void load(); });
watch(() => route.query, () => {
    if (leaving || route.path !== '/analysis') return;
    const text = typeof route.query.search === 'string' ? route.query.search : '';
    const nextPage = Math.max(1, Number.parseInt(String(route.query.page ?? '1'), 10) || 1);
    if (search.value !== text) search.value = text;
    if (page.value !== nextPage) page.value = nextPage;
});
void load();
// 离开搜索页后停止请求和URL回写，详情页的tab及快照参数由详情自身维护。
onBeforeRouteLeave(() => { leaving = true; ++requestId; clearTimeout(timer); });
onBeforeUnmount(() => { ++requestId; clearTimeout(timer); });
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">STOCK RESEARCH</span><h1>分析你关注的股票</h1><p>输入名称或代码，无需先入选策略。当前覆盖沪深四板块。</p></div><span class="tag tag-amber">报告保留资料来源与日期</span></div>
  <section class="panel research-search">
    <label for="stock-research-search"><strong>股票名称或代码</strong></label>
    <el-input id="stock-research-search" v-model="search" maxlength="50" placeholder="输入股票名称或代码，如浦发银行、600000" clearable size="large" @keyup.enter="load"><template #prefix><AppIcon name="search" /></template></el-input>
    <p class="muted">搜索完整股票目录；报告使用已同步的收盘数据，详细依据和来源按需展开。</p>
    <StatePanel v-if="!search.trim()" title="你想了解哪只股票？" description="找到股票后，可以查看独立筹码分布，并生成完整AI分析报告。" />
    <StatePanel v-else-if="loading" kind="loading" title="正在查找股票" />
    <StatePanel v-else-if="error" kind="error" title="股票搜索失败" :description="error" action="重试" @action="load" />
    <StatePanel v-else-if="!rows.length" title="没有找到这只股票" description="请检查名称或代码；北交所、港股和美股暂未开放。" />
    <template v-else>
      <p class="muted" role="status">找到 {{ total }} 只股票</p>
      <div class="research-matches">
        <article v-for="stock in rows" :key="stock.stockId" class="research-match">
          <div><strong>{{ stock.name }}</strong><p>{{ stock.code }} · {{ stock.exchange === 'SSE' ? '上交所' : '深交所' }} · {{ BOARDS[stock.board].name }}</p><small class="muted">{{ stock.dataDate ? `行情截至 ${stock.dataDate}` : '行情资料待同步，可进入查看准备状态' }}</small></div>
          <el-button type="primary" plain @click="openStock(stock)">查看分析</el-button>
        </article>
      </div>
      <el-pagination v-if="total > 20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" />
    </template>
  </section>
</template>
<style scoped>
.research-search { max-width: 900px; }
.research-search label { display: block; margin-bottom: 12px; }
.research-search > .muted { margin-top: 14px; }
.research-matches { display: grid; gap: 10px; margin: 18px 0; }
.research-match { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px; border: 1px solid var(--line); border-radius: 12px; }
.research-match strong { font-size: 17px; }
.research-match p { margin: 6px 0; font-size: 13px; }
@media (max-width: 600px) { .research-match { align-items: flex-start; flex-direction: column; gap: 12px; } }
</style>
