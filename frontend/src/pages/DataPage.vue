<!-- 本文件提交真实同步任务并展示服务器进度、来源、等待时间和持久化日志。 -->
<script setup lang="ts">
import { randomId } from '../ids.ts';
import { computed, onUnmounted, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import { BOARDS } from '../boards.ts';
import { ApiError, syncLogs } from '../api.ts';
import type { SyncJob } from '../api.ts';
import { useMarket, useSync } from '../stores.ts';
const sync = useSync();
const market = useMarket();
const kinds: SyncJob['kind'][] = ['initialize', 'catalog', 'daily'];
const sources = reactive<Record<SyncJob['kind'], string>>({ initialize: '', catalog: '', daily: '' });
const labels = { initialize: '股票基础信息', catalog: '行业与题材', daily: '收盘行情与策略' };
const descriptions = { initialize: '更新股票代码、名称、交易所及板块。', catalog: '更新分类后，再更新收盘行情即可在筛选中使用。', daily: '同步行情并计算五类策略，完成后发布新快照。' };
function taskLabel(job: SyncJob): string { return job.historyBackfill ? '历史行情回补' : labels[job.kind]; }
function taskCounts(job: SyncJob): string { const counts = job.stockProgress ?? job.result; return counts ? `${counts.processedCount} / ${counts.totalCount}` : '—'; }
const statusLabels = { queued: '等待执行', running: '进行中', succeeded: '已完成', failed: '失败' };
/** 区分就绪检查、等待资料、渠道等待及真正同步，不把排队一概显示为更新中。 */
function taskStatus(job: SyncJob): string {
    if (job.status === 'queued' && job.waitReason === 'SOURCE_REQUEST_INTERVAL') return '按频控继续同步';
    if (job.status === 'queued' && job.waitReason === 'BOARD_DATA_NOT_READY') return '数据暂未就绪';
    if (job.status === 'queued' && job.waitReason === 'SOURCE_ACCESS_WAIT') return '等待数据源访问';
    if (job.status === 'queued' && job.waitReason === 'FOREGROUND_SYNC_PENDING') return '等待日常更新';
    if (job.status === 'running' && !job.historyBackfill && job.phase === 'checking') return '检查数据就绪情况';
    if (job.status === 'running' && job.phase === 'calculating') return '计算策略并保存';
    if (job.status === 'running' && job.phase === 'syncing') return '同步行情';
    return statusLabels[job.status];
}
const boardStatusLabels = { ready: '已就绪', pending: '暂未就绪', market_closed: '休市', unknown: '尚未确认', unsupported: '渠道不支持' };
const target = ref('');
const selected = ref<SyncJob>();
const detailId = ref('');
const detailsOpen = ref(false);
const detail = computed(() => sync.jobs.find(job => job.id === detailId.value));
const logsOpen = ref(false);
const logs = ref<string[]>([]);
const logsError = ref('');
let logTimer: ReturnType<typeof setTimeout> | undefined;
let logRequest = 0;
const submissions = new Map<string, { signature: string; key: string }>();
const latestDaily = computed(() => sync.jobs.find(job => job.kind === 'daily' && job.status === 'succeeded'));
function choices(kind: SyncJob['kind']) { return (market.status?.sources ?? []).filter(source => source.available && source.supportedSyncKinds.includes(kind)); }
function formatTime(value: string | null | undefined): string { return value ? new Date(value).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' }) : '—'; }
watch(() => market.status, () => {
    for (const kind of kinds) if (!choices(kind).some(source => source.id === sources[kind])) sources[kind] = choices(kind).find(source => source.id === market.status?.defaultSource)?.id ?? choices(kind)[0]?.id ?? '';
    if (!target.value) target.value = market.status?.latestTradingDate ?? '';
}, { immediate: true });
async function start(kind: SyncJob['kind'], retry?: SyncJob): Promise<void> {
    try {
        if (!retry && kind === 'initialize' && sync.initialized) await ElMessageBox.confirm('更新股票基础信息，保留已有分类、行情快照和报告。', '更新基础信息');
        if (!retry && kind === 'catalog' && sync.catalogUpdatedAt) await ElMessageBox.confirm('刷新行业与题材归属，保留已有行情快照和报告。', '刷新分类');
        const source = retry?.source ?? sources[kind];
        const day = kind === 'daily' ? retry?.dataDate ?? target.value : undefined;
        const signature = JSON.stringify([kind, source, day, retry?.id]);
        const previous = submissions.get(kind);
        const key = previous?.signature === signature ? previous.key : randomId();
        submissions.set(kind, { signature, key });
        let completed = false;
        if (retry) await sync.retry(retry.id, key);
        else completed = (await sync.start(kind, source, day || undefined, key)).status === 'succeeded';
        submissions.delete(kind);
        ElMessage.success(completed ? `${day ?? ''}已成功更新，无需重复执行。` : retry ? '已从原任务检查点继续，已保存的数据保留。' : '任务已受理，关闭页面后仍由服务器执行。');
    } catch (cause) {
        if (cause instanceof Error) {
            if (cause instanceof ApiError && cause.status > 0 && cause.status < 500) submissions.delete(kind);
            ElMessage.error(cause.message);
            await sync.refresh();
        }
    }
}
async function loadLogs(): Promise<void> {
    const id = ++logRequest; clearTimeout(logTimer);
    if (!selected.value || !logsOpen.value) return;
    try { const result = await syncLogs(selected.value.id); if (id === logRequest) { logs.value = result; logsError.value = ''; } }
    catch (cause) { if (id === logRequest) logsError.value = (cause as Error).message; }
    if (id === logRequest && logsOpen.value && sync.jobs.some(job => job.id === selected.value?.id && ['queued', 'running'].includes(job.status))) logTimer = setTimeout(() => { void loadLogs(); }, 3000);
}
function showDetails(job: SyncJob): void { detailId.value = job.id; detailsOpen.value = true; }
function showLogs(job: SyncJob): void { detailsOpen.value = false; selected.value = job; logs.value = []; logsOpen.value = true; void loadLogs(); }
watch(logsOpen, (open) => { if (!open) { ++logRequest; clearTimeout(logTimer); } });
onUnmounted(() => { ++logRequest; clearTimeout(logTimer); });
void sync.refresh();
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">DATA OPERATIONS</span><h1>让研究从完整数据开始</h1><p>基础信息、行业题材分别按需更新，行情校验成功后发布策略结果。</p></div><el-button @click="sync.refresh()">刷新状态</el-button></div>
  <el-alert v-if="sync.error" :title="sync.error" type="error" show-icon :closable="false" />
  <div class="summary-grid">
    <div class="summary-card"><span>股票基础信息</span><strong class="summary-text">{{ sync.initialized ? '已初始化' : '等待初始化' }}</strong><p>最近成功 {{ formatTime(sync.stockUpdatedAt) }}</p></div>
    <div class="summary-card"><span>行业与题材</span><strong class="summary-text">{{ sync.catalogUpdatedAt ? '已更新' : '尚未更新' }}</strong><p>最近成功 {{ formatTime(sync.catalogUpdatedAt) }}</p></div>
    <div class="summary-card"><span>已发布收盘行情</span><strong class="summary-text">{{ market.status?.publishedSnapshot?.dataDate ?? '尚未发布' }}</strong><p>最近任务 {{ formatTime(latestDaily?.createdAt) }}</p></div>
  </div>
  <section class="panel"><div class="section-caption"><strong>更新数据</strong><span class="muted">每次任务固定一个数据渠道</span></div>
    <div class="summary-grid">
      <article v-for="kind in kinds" :key="kind" class="summary-card"><h3>{{ labels[kind] }}</h3><p>{{ descriptions[kind] }}</p>
        <el-select v-model="sources[kind]" :placeholder="choices(kind).length ? '选择渠道' : '暂无可用渠道'" :disabled="sync.busy || sync.loading || !choices(kind).length" :aria-label="`${labels[kind]}数据渠道`">
          <el-option v-for="source in choices(kind)" :key="source.id" :label="source.name" :value="source.id" />
        </el-select>
        <el-date-picker v-if="kind === 'daily'" v-model="target" type="date" value-format="YYYY-MM-DD" placeholder="目标收盘日期" :disabled="sync.busy || sync.loading" style="width: 100%; margin-top: 10px" />
        <p v-if="kind === 'daily'" class="muted">本次目标 {{ target || '请选择日期' }}；通过数据就绪检查后才开始同步。</p>
        <p v-if="!choices(kind).length" class="muted">当前渠道暂不可用，请稍后刷新状态。</p>
        <el-button type="primary" :loading="sync.loading" :disabled="sync.busy || !sources[kind] || (kind !== 'initialize' && !sync.initialized)" style="margin-top: 12px" @click="start(kind)">{{ kind === 'initialize' && !sync.initialized ? '初始化股票' : '开始更新' }}</el-button>
      </article>
    </div>
  </section>
  <section class="panel"><div class="section-caption"><strong>任务记录</strong><span class="muted">最近100条</span></div>
    <StatePanel v-if="!sync.jobs.length" title="还没有同步任务" description="先初始化股票基础信息，再同步收盘行情。" />
    <div v-else class="table-scroll"><table class="data-table"><thead><tr><th>任务</th><th>渠道</th><th>发起时间（北京时间）</th><th>状态</th><th>已处理 / 总数</th><th>操作</th></tr></thead><tbody>
      <tr v-for="job in sync.jobs" :key="job.id" :data-task-id="job.id"><td>{{ taskLabel(job) }}<small class="cell-subtext">{{ job.dataDate ?? job.id.slice(0, 8) }}</small></td><td>{{ market.status?.sources.find(source => source.id === job.source)?.name ?? job.source }}</td><td>{{ formatTime(job.createdAt) }}</td>
        <td><span class="tag" :class="job.status === 'succeeded' ? 'tag-green' : job.status === 'failed' ? 'tag-red' : 'tag-amber'">{{ taskStatus(job) }}<template v-if="job.kind !== 'daily' || job.stockProgress || job.status === 'succeeded'"> {{ job.progress }}%</template></span><small v-if="job.error" class="cell-subtext">{{ job.error.message }}</small><small v-if="job.message" class="cell-subtext">{{ job.message }}</small></td>
        <td>{{ taskCounts(job) }}</td><td><el-button text @click="showDetails(job)">查看详情</el-button><el-button v-if="job.status === 'failed'" text type="primary" :disabled="sync.busy || sync.loading || !choices(job.kind).some(source => source.id === job.source)" @click="start(job.kind, job)">{{ job.stockProgress ? '从断点继续' : '重试' }}</el-button></td></tr>
    </tbody></table></div>
  </section>
  <el-drawer v-model="detailsOpen" title="任务详情" size="min(680px, 96vw)">
    <section v-if="detail" class="active-sync"><div class="section-caption"><strong>{{ taskLabel(detail) }} · {{ taskStatus(detail) }}</strong><el-button text @click="showLogs(detail)">查看日志</el-button></div>
      <p v-if="detail.kind === 'daily'" class="sync-target">目标日期 {{ detail.dataDate }} · 已发布 {{ market.status?.publishedSnapshot?.dataDate ?? '尚无快照' }}</p>
      <el-progress v-if="(detail.kind !== 'daily' || detail.stockProgress) && !['checking', 'waiting_data'].includes(detail.phase ?? '')" :percentage="detail.progress" />
      <el-alert v-if="detail.waitReason === 'BOARD_DATA_NOT_READY'" title="目标日期收盘数据暂未就绪" description="保持已有行情与策略结果，稍后自动重新检查，无需重复提交。" type="warning" :closable="false" show-icon />
      <p v-else-if="detail.phase === 'checking' && detail.status === 'running'" class="muted">正在检查目标日期及板块覆盖，尚未开始行情同步。</p>
      <p v-if="detail.message">{{ detail.message }}</p>
      <p v-if="detail.error">{{ detail.error.message }}</p>
      <p v-if="detail.waitReason === 'SOURCE_ACCESS_WAIT'" class="muted">{{ detail.waitMessage ?? '正在等待请求间隔、额度或渠道冷却，尚不能继续访问数据源。' }}</p>
      <div v-if="!detail.historyBackfill && detail.precheck" class="board-readiness" aria-label="板块数据就绪情况">
        <article v-for="item in detail.precheck.boards" :key="item.board"><strong>{{ BOARDS[item.board].name }}</strong><span class="tag" :class="item.status === 'ready' ? 'tag-green' : 'tag-amber'">{{ boardStatusLabels[item.status] }}</span><small>本次返回数据日期：{{ item.latestDataDate ?? '尚未取得' }}</small><p>{{ item.message }}</p></article>
      </div>
      <p v-if="detail.waitReason === 'FOREGROUND_SYNC_PENDING'" class="muted">历史回补已保存进度，等待日常更新完成后继续。</p><p v-else-if="detail.nextRetryAt" class="muted">下次继续执行 {{ formatTime(detail.nextRetryAt) }}。</p><p v-else-if="['queued', 'running'].includes(detail.status)" class="muted">任务在服务器运行，刷新或退出账号不会取消已受理任务。</p>
    </section>
  </el-drawer>
  <el-dialog v-model="logsOpen" title="任务日志" width="min(700px, 94vw)"><el-alert v-if="logsError" :title="logsError" type="error" :closable="false" /><div class="task-logs"><p v-for="(line, index) in logs" :key="index"><span>{{ index + 1 }}</span>{{ line }}</p><p v-if="!logs.length">暂无日志。</p></div></el-dialog>
</template>
<style scoped>
.sync-target { color: var(--muted); font-size: var(--font-size-small); }
.board-readiness { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 12px; margin: 16px 0; }
.board-readiness article { padding: 14px; border: 1px solid var(--line); border-radius: 5px; }
.board-readiness .tag { margin-left: 8px; }
.board-readiness small { display: block; margin-top: 8px; color: var(--muted); }
.board-readiness p { font-size: var(--font-size-small); margin-bottom: 0; }
</style>
