<!-- 本文件展示管理员的模拟初始化、收盘同步、任务进度与失败重试。 -->
<script setup lang="ts">
import { computed, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import { DATA_DATE } from '../mock/fixtures.ts';
import { useReview, useSync } from '../stores.ts';
import type { SyncJob } from '../types.ts';
const sync = useSync();
const review = useReview();
const source = ref<SyncJob['source']>('Sina');
const selected = ref('');
const logsOpen = ref(false);
const selectedJob = computed(() => sync.jobs.find((job) => job.id === selected.value));
const latestDaily = computed(() => sync.jobs.find((job) => job.kind === 'daily' && job.status === 'success'));
const statusLabels = { running: '进行中', success: '已完成', failed: '失败' };
const kindLabels = { initialize: '初始化基础数据', daily: '收盘数据同步' };
sync.refresh();
async function start(kind: SyncJob['kind'], retry = false): Promise<void> {
    try {
        if (kind === 'initialize' && sync.initialized && !retry)
            await ElMessageBox.confirm('将重新演示基础数据初始化。固定机会样例和已生成的报告不受影响。', '重新初始化', { confirmButtonText: '开始', cancelButtonText: '取消' });
        if (retry)
            review.syncFail = false;
        sync.start(kind, review.syncFail, source.value);
        ElMessage.success('模拟任务已启动，切换页面后仍会继续。');
    }
    catch (cause) {
        if (cause instanceof Error)
            ElMessage.error(cause.message);
    }
}
function showLogs(job: SyncJob): void { selected.value = job.id; logsOpen.value = true; }
function retryJob(job: SyncJob): void { source.value = job.source; void start(job.kind, true); }
</script>
<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">DATA OPERATIONS</span>
      <h1>让研究从完整数据开始</h1>
      <p>管理股票基础数据与收盘同步，查看每次任务的处理结果。</p>
    </div>
    <span class="tag">仅管理员可用</span>
  </div>
  <div class="summary-grid">
    <div class="summary-card">
      <span>基础数据状态</span>
      <strong class="summary-text">{{ sync.initialized ? '已初始化' : '等待初始化' }}</strong>
      <p>{{ sync.initialized ? '已完成模拟基础数据准备' : '先完成初始化，再体验收盘同步' }}</p>
    </div>
    <div class="summary-card">
      <span>示例市场覆盖</span>
      <strong>
        5,348
        <small>只股票</small>
      </strong>
      <p>沪 / 深 / 北 · 数量为虚构示例</p>
    </div>
    <div class="summary-card">
      <span>最近收盘同步</span>
      <strong class="summary-text">{{ latestDaily ? DATA_DATE : '尚未同步' }}</strong>
      <p>{{ latestDaily ? '固定示例日期，不随当前时间变更' : '不影响机会列表的固定样例展示' }}</p>
    </div>
  </div>
  <section class="panel data-actions-panel">
    <div class="section-caption">
      <strong>数据任务</strong>
      <span class="tag tag-amber">本地模拟，不连接数据源</span>
    </div>
    <div class="data-source">
      <label>
        示例数据源
        <el-select v-model="source" :disabled="!!sync.running" style="width: 170px">
          <el-option label="Sina" value="Sina" />
          <el-option label="Baostock" value="Baostock" />
        </el-select>
      </label>
      <span>用于演示选择数据渠道的交互</span>
    </div>
    <div class="data-action-grid">
      <article>
        <span class="action-icon">
          <AppIcon name="database" :size="25" />
        </span>
        <div>
          <h3>初始化基础数据</h3>
          <p>模拟获取股票代码、名称、交易所、上市板块和行业等基础信息。</p>
          <el-button :disabled="!!sync.running" @click="start('initialize')">
            {{ sync.initialized ? '重新初始化' : '开始初始化' }}
            <AppIcon name="arrow" :size="16" />
          </el-button>
        </div>
      </article>
      <article>
        <span class="action-icon">
          <AppIcon name="refresh" :size="25" />
        </span>
        <div>
          <h3>同步收盘数据</h3>
          <p>模拟处理 {{ DATA_DATE }} 的收盘数据并展示处理进度。</p>
          <el-button type="primary" :disabled="!sync.initialized || !!sync.running" @click="start('daily')">
            同步示例收盘数据
            <AppIcon name="arrow" :size="16" />
          </el-button>
          <small v-if="!sync.initialized">请先完成基础数据初始化</small>
        </div>
      </article>
    </div>
  </section>
  <section v-if="sync.running" class="panel running-panel">
    <div>
      <span class="status-dot" />
      <strong>{{ kindLabels[sync.running.kind] }}进行中</strong>
      <el-button text @click="showLogs(sync.running)">查看日志</el-button>
    </div>
    <el-progress :percentage="sync.running.progress" :stroke-width="8" />
    <p class="muted">{{ sync.running.logs.at(-1) }} 刷新或退出账号会中断模拟任务。</p>
  </section>
  <section class="panel">
    <div class="section-caption">
      <strong>任务记录</strong>
      <span class="muted">保留最近 20 次模拟任务</span>
    </div>
    <StatePanel v-if="!sync.jobs.length" title="还没有同步任务" description="从上方初始化基础数据开始，体验完整任务流程。" />
    <div v-else class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>任务</th>
            <th>数据源</th>
            <th>发起时间（北京时间）</th>
            <th>状态</th>
            <th>处理数量</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in sync.jobs" :key="job.id">
            <td>
              <strong>{{ kindLabels[job.kind] }}</strong>
              <small class="cell-subtext">{{ job.id.slice(0, 8) }}</small>
            </td>
            <td>{{ job.source }}</td>
            <td>{{ job.createdAt }}</td>
            <td>
              <span
                class="tag"
                :class="job.status === 'success' ? 'tag-green' : job.status === 'failed' ? 'tag-red' : 'tag-amber'"
              >{{ statusLabels[job.status] }}{{ job.status === 'running' ? ` ${job.progress}%` : '' }}</span>
            </td>
            <td>{{ job.status === 'success' ? `${job.count.toLocaleString()} 条（模拟）` : '—' }}</td>
            <td>
              <el-button text @click="showLogs(job)">查看日志</el-button>
              <el-button
                v-if="job.status === 'failed'"
                text
                type="primary"
                :disabled="!!sync.running"
                @click="retryJob(job)"
              >重试</el-button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <el-dialog v-model="logsOpen" title="模拟任务日志" width="min(700px, 94vw)">
    <template v-if="selectedJob">
      <div class="log-heading">
        <strong>{{ kindLabels[selectedJob.kind] }}</strong>
        <span class="tag">{{ statusLabels[selectedJob.status] }}</span>
      </div>
      <div class="task-logs">
        <p v-for="(line, index) in selectedJob.logs" :key="index">
          <span>{{ String(index + 1).padStart(2, '0') }}</span>
          {{ line }}
        </p>
      </div>
      <p class="muted">这些日志由前端计时器生成，不表示已获取或更新真实市场数据。</p>
    </template>
  </el-dialog>
</template>
