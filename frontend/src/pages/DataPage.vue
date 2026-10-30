<!-- 本文件独立展示股票基础信息、行业题材、收盘行情三类模拟任务及其状态、进度和重试。 -->
<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import { DATA_DATE } from '../mock/fixtures.ts';
import { useReview, useSync } from '../stores.ts';
import type { SyncJob } from '../types.ts';
const sync = useSync();
const review = useReview();
/** 不同任务分别保留来源选择，重试时恢复原任务的来源。 */
const sources = reactive<Record<SyncJob['kind'], SyncJob['source']>>({ initialize: 'Sina', catalog: '分类样例', daily: 'Sina' });
const selected = ref('');
const logsOpen = ref(false);
const selectedJob = computed(() => sync.jobs.find((job) => job.id === selected.value));
const latestDaily = computed(() => sync.jobs.find((job) => job.kind === 'daily' && job.status === 'success'));
const statusLabels = { running: '进行中', success: '已完成', failed: '失败' };
const kindLabels = { initialize: '股票基础信息同步', catalog: '行业与题材同步', daily: '收盘行情与策略同步' };
sync.refresh();
async function start(kind: SyncJob['kind'], retry = false): Promise<void> {
    try {
        if (!retry && kind === 'initialize' && sync.initialized)
            await ElMessageBox.confirm('仅模拟更新股票代码、名称、交易所、上市板块等基础信息，保留已有行业、题材关系及快照和报告。', '更新股票基础信息', { confirmButtonText: '开始更新', cancelButtonText: '取消' });
        if (!retry && kind === 'catalog' && sync.catalogUpdatedAt)
            await ElMessageBox.confirm('仅模拟刷新行业、题材及股票归属，保留股票基础信息和已有快照、报告。失败保留上次成功的分类状态。', '刷新行业、题材数据', { confirmButtonText: '开始刷新', cancelButtonText: '取消' });
        if (retry)
            review.syncFail = false;
        sync.start(kind, review.syncFail, sources[kind]);
        ElMessage.success('模拟任务已启动，切换页面后仍会继续。');
    }
    catch (cause) {
        if (cause instanceof Error)
            ElMessage.error(cause.message);
    }
}
function showLogs(job: SyncJob): void { selected.value = job.id; logsOpen.value = true; }
function retryJob(job: SyncJob): void { sources[job.kind] = job.source; void start(job.kind, true); }
</script>
<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">DATA OPERATIONS</span>
      <h1>让研究从完整数据开始</h1>
      <p>股票基础信息、行业题材分别按需更新，收盘行情与策略每日同步。</p>
    </div>
    <span class="tag">仅管理员可用</span>
  </div>
  <div class="summary-grid">
    <div class="summary-card">
      <span>股票基础信息</span>
      <strong class="summary-text">{{ sync.initialized ? '已初始化' : '等待初始化' }}</strong>
      <p>{{ sync.stockUpdatedAt ? `最近成功 ${sync.stockUpdatedAt}` : sync.initialized ? '已初始化，暂未记录更新时间' : '先同步股票清单，再同步分类和行情' }}</p>
      <p>沪 / 深 / 北 · 5,348只（模拟）</p>
    </div>
    <div class="summary-card">
      <span>行业与题材</span>
      <strong class="summary-text">{{ sync.catalogUpdatedAt ? '已同步' : '等待同步' }}</strong>
      <p>{{ sync.catalogUpdatedAt ? `最近成功 ${sync.catalogUpdatedAt}` : '独立手动同步，不随股票基础信息自动更新' }}</p>
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
    <div class="data-action-grid">
      <article>
        <span class="action-icon">
          <AppIcon name="database" :size="25" />
        </span>
        <div>
          <h3>股票基础信息</h3>
          <p>同步股票清单、代码、名称、交易所和上市板块，保留已有行业、题材关系。</p>
          <label class="task-source">
            股票数据源
            <el-select v-model="sources.initialize" :disabled="!!sync.running" aria-label="股票基础信息数据源">
              <el-option label="Sina" value="Sina" />
              <el-option label="Baostock" value="Baostock" />
            </el-select>
          </label>
          <el-button :disabled="!!sync.running" @click="start('initialize')">
            {{ sync.initialized ? '更新股票基础信息' : '初始化股票基础信息' }}
            <AppIcon name="arrow" :size="16" />
          </el-button>
          <small>首次先执行此任务，之后可单独更新股票清单。</small>
        </div>
      </article>
      <article>
        <span class="action-icon">
          <AppIcon name="layers" :size="25" />
        </span>
        <div>
          <h3>行业与题材</h3>
          <p>同步行业、题材字典与股票归属，仅按需手动执行，保留股票基础信息。</p>
          <label class="task-source">
            分类数据源
            <span class="tag">分类样例 · 本地模拟</span>
          </label>
          <el-button :disabled="!sync.initialized || !!sync.running" @click="start('catalog')">
            <AppIcon name="refresh" :size="16" />
            刷新行业、题材数据
          </el-button>
          <small>{{ sync.initialized ? '首次同步或后续刷新均使用此入口，失败保留上次成功数据。' : '请先初始化股票清单，再关联行业和题材。' }}</small>
        </div>
      </article>
      <article>
        <span class="action-icon">
          <AppIcon name="refresh" :size="25" />
        </span>
        <div>
          <h3>收盘行情与策略</h3>
          <p>模拟处理 {{ DATA_DATE }} 的收盘行情与策略结果，复用已有行业、题材关系，不重新获取分类数据。</p>
          <label class="task-source">
            行情数据源
            <el-select v-model="sources.daily" :disabled="!!sync.running" aria-label="收盘行情数据源">
              <el-option label="Sina" value="Sina" />
              <el-option label="Baostock" value="Baostock" />
            </el-select>
          </label>
          <el-button type="primary" :disabled="!sync.initialized || !!sync.running" @click="start('daily')">
            同步示例收盘数据
            <AppIcon name="arrow" :size="16" />
          </el-button>
          <small v-if="!sync.initialized">请先完成基础数据初始化</small>
          <small v-else-if="!sync.catalogUpdatedAt">分类尚未同步时标记未知，不影响行情任务运行。</small>
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
