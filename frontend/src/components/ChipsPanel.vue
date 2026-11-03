<!-- 本文件独立读取筹码分布，查看图形不创建AI任务或调用模型。 -->
<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue';
import { getChips } from '../api.ts';
import type { ChipsResult, LiveStock } from '../api.ts';
import StatePanel from './StatePanel.vue';
import ChipDistributionChart from './ChipDistributionChart.vue';
const props = defineProps<{ stock: LiveStock }>();
const result = ref<ChipsResult>();
const loading = ref(false);
const error = ref('');
let version = 0;
async function load(): Promise<void> {
    const current = ++version; loading.value = true; result.value = undefined; error.value = '';
    try { const value = await getChips(props.stock); if (current === version) result.value = value; }
    catch (cause) { if (current === version) error.value = (cause as Error).message; }
    finally { if (current === version) loading.value = false; }
}
watch([() => props.stock.stockId, () => props.stock.snapshotId], () => { void load(); }, { immediate: true });
onUnmounted(() => { ++version; });
</script>
<template>
  <section class="panel chips-panel">
    <div class="section-caption"><div><strong>筹码分布</strong><span class="muted">估算成本 · 行情截至 {{ stock.dataDate }}</span></div><span class="tag">无需 AI 分析</span></div>
    <StatePanel v-if="loading" kind="loading" title="正在计算筹码分布" />
    <StatePanel v-else-if="error" kind="error" title="暂时无法读取筹码" :description="error" action="重试" @action="load" />
    <template v-else-if="result"><div class="ownership-note"><div><strong>股东户数变化</strong><span class="tag tag-amber">待接入</span></div><p>{{ result.shareholders.explanation }}</p><small>户数趋势用于观察平均持股变化，成本图用于观察估算价位；户数减少不等于已确认主力吸筹。</small></div><ChipDistributionChart v-if="result.distribution" :distribution="result.distribution" /><StatePanel v-else title="筹码资料不足" :description="result.missingInformation.join('；')" /><details class="chip-method"><summary>估算方法与适用范围</summary><p>{{ result.method }}</p><p>历史成交成本估算不等同真实持股清单，上下方参考区不保证价格反弹或受阻。</p></details></template>
  </section>
</template>
<style scoped>
.ownership-note { margin: 18px 24px; padding: 18px; border: 1px solid var(--line); border-radius: 12px; }
.ownership-note > div { display: flex; justify-content: space-between; gap: 12px; }
.ownership-note p { line-height: 1.8; font-size: 14px; margin: 12px 0 8px; }
.ownership-note small { color: var(--text-secondary); line-height: 1.7; }
.chips-panel { padding-bottom: 16px; }
.chip-method { margin: 16px 24px; color: var(--text-secondary); line-height: 1.8; font-size: 13px; }
.chip-method summary { cursor: pointer; }
</style>
