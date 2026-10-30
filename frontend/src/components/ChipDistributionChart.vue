<!-- 本文件在 AI 筹码分析结果内绘制价格区间占比，并标出估算支撑、压力与现价。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { ChipBin, ChipDistribution } from '../types.ts';
const props = defineProps<{
    distribution: ChipDistribution;
}>();
const selectedPrice = ref<number | null>(null);
const bins = computed(() => props.distribution.bins.slice().reverse());
const maxRatio = computed(() => Math.max(...bins.value.map((bin) => bin.ratio), 1));
const currentBin = computed(() => props.distribution.bins.find((bin) => bin.lower <= props.distribution.currentPrice && bin.upper > props.distribution.currentPrice));
const selected = computed(() => bins.value.find((bin) => bin.price === selectedPrice.value) ?? currentBin.value ?? bins.value[0]);
/** 参考位来自同一份分布数据，图形标记与报告正文保持一致。 */
function marker(bin: ChipBin): string {
    if (bin.price === props.distribution.support.price)
        return '支撑参考';
    if (bin.price === props.distribution.resistance.price)
        return '压力参考';
    if (bin.price === currentBin.value?.price)
        return '现价所在档';
    return '';
}
function description(bin: ChipBin): string {
    return `${bin.lower.toFixed(2)}～${bin.upper.toFixed(2)}元，估算筹码占比${bin.ratio.toFixed(2)}%，${marker(bin)}`;
}
watch(() => props.distribution, () => { selectedPrice.value = null; });
</script>

<template>
  <section class="chip-distribution" aria-label="模拟筹码价格分布">
    <div class="chip-title">
      <h3>筹码价格分布</h3>
      <span class="tag tag-amber">模拟估算 · 合计 100%</span>
    </div>
    <div class="chip-levels">
      <div>
        <span>支撑参考</span>
        <strong>
          {{ distribution.support.price.toFixed(2) }}
          <small>元</small>
        </strong>
        <p>{{ distribution.support.lower.toFixed(2) }}～{{ distribution.support.upper.toFixed(2) }} 元</p>
      </div>
      <div>
        <span>当前收盘价</span>
        <strong>
          {{ distribution.currentPrice.toFixed(2) }}
          <small>元</small>
        </strong>
        <p>同一模拟价格口径</p>
      </div>
      <div>
        <span>压力参考</span>
        <strong>
          {{ distribution.resistance.price.toFixed(2) }}
          <small>元</small>
        </strong>
        <p>{{ distribution.resistance.lower.toFixed(2) }}～{{ distribution.resistance.upper.toFixed(2) }} 元</p>
      </div>
    </div>
    <p class="chip-explanation">每根横条对应一个价格区间的估算筹码占比。悬停、点击或使用键盘选择，可查看该区间的具体比例。</p>
    <div class="chip-axis">
      <span>价位（元）</span>
      <span>筹码占比：0～{{ maxRatio.toFixed(2) }}%</span>
      <span>占比</span>
    </div>
    <div class="chip-bars">
      <button
        v-for="bin in bins"
        :key="bin.price"
        class="chip-row"
        :class="{ 'is-support': bin.price === distribution.support.price, 'is-resistance': bin.price === distribution.resistance.price, 'is-current': bin.price === currentBin?.price, 'is-selected': selected?.price === bin.price }"
        :title="description(bin)"
        :aria-label="description(bin)"
        :aria-pressed="selected?.price === bin.price"
        @pointerenter="selectedPrice = bin.price"
        @focus="selectedPrice = bin.price"
        @click="selectedPrice = bin.price"
      >
        <span class="chip-price">
          <b>{{ bin.price.toFixed(2) }}</b>
          <small v-if="marker(bin)">{{ marker(bin) }}</small>
        </span>
        <span class="chip-track">
          <i :style="{ width: `${bin.ratio / maxRatio * 100}%`, background: bin.lower > distribution.currentPrice ? 'var(--ma5)' : 'var(--accent)' }" />
        </span>
        <span class="chip-ratio">{{ bin.ratio.toFixed(2) }}%</span>
      </button>
    </div>
    <div v-if="selected" class="chip-selection" aria-live="polite">
      <strong>{{ selected.lower.toFixed(2) }}～{{ selected.upper.toFixed(2) }} 元</strong>
      <span>
        估算筹码占比
        <b>{{ selected.ratio.toFixed(2) }}%</b>
      </span>
      <span v-if="marker(selected)">{{ marker(selected) }}</span>
    </div>
    <p class="chip-explanation">蓝色为现价及以下区域，橙色为现价上方区域；跨现价档位单独标记。支撑和压力取自现价两侧的主要筹码档位，仅是观察参考，不保证价格反弹或受阻。</p>
  </section>
</template>

<style scoped>
.chip-distribution { border: 1px solid var(--line); border-radius: 5px; margin: 24px 0; padding: 22px; }
.chip-title { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }
.chip-title h3 { margin: 0; font-size: 17px; font-weight: 600; }
.chip-levels { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 22px 0; }
.chip-levels > div { min-width: 0; padding: 14px; border-radius: 4px; background: var(--surface-muted); border-top: 2px solid var(--line); }
.chip-levels > div:first-child { border-top-color: var(--accent); }
.chip-levels > div:last-child { border-top-color: var(--ma5); }
.chip-levels span, .chip-levels small, .chip-levels p { color: var(--muted); font-size: var(--font-size-small); }
.chip-levels strong { display: block; font-size: 23px; margin-top: 9px; font-variant-numeric: tabular-nums; }
.chip-levels small { margin-left: 5px; font-weight: 400; }
.chip-levels p { margin: 8px 0 0; overflow-wrap: anywhere; }
.chip-explanation { color: var(--muted); font-size: var(--font-size-small); line-height: 1.8; }
.chip-axis, .chip-row { display: grid; grid-template-columns: 100px minmax(0, 1fr) 64px; gap: 13px; align-items: center; }
.chip-axis { margin: 18px 0 8px; color: var(--muted); font-size: var(--font-size-small); }
.chip-axis > span:last-child { text-align: right; }
.chip-bars { border-top: 1px solid var(--line); }
.chip-row { width: 100%; padding: 5px 8px; min-height: 30px; text-align: left; border: 0; border-bottom: 1px solid var(--surface-muted); border-left: 3px solid transparent; background: var(--surface); color: var(--ink); }
.chip-row.is-selected { background: var(--accent-faint); }
.chip-row.is-support { border-left-color: var(--accent); }
.chip-row.is-resistance { border-left-color: var(--ma5); }
.chip-row.is-current { border-bottom: 1px dashed var(--muted); }
.chip-price { font-size: var(--font-size-small); font-variant-numeric: tabular-nums; }
.chip-price b { font-weight: 500; }
.chip-price small { display: block; font-size: var(--font-size-small); color: var(--muted); }
.is-support .chip-price small { color: var(--accent); }
.is-resistance .chip-price small { color: var(--ma5); }
.chip-track { height: 15px; background: var(--surface-muted); border-radius: 2px; }
.chip-track i { display: block; height: 100%; min-width: 1px; border-radius: 2px; }
.chip-ratio { text-align: right; font-size: var(--font-size-small); font-variant-numeric: tabular-nums; }
.chip-selection { display: flex; flex-wrap: wrap; gap: 9px 20px; padding: 14px; margin-top: 15px; background: var(--accent-soft); font-size: var(--font-size-table); }
.chip-selection strong { font-weight: 600; }
@media (max-width: 640px) {
  .chip-distribution { padding: 14px 10px; }
  .chip-levels { gap: 7px; }
  .chip-levels > div { padding: 10px 7px; }
  .chip-levels strong { font-size: 18px; }
  .chip-axis, .chip-row { grid-template-columns: 82px minmax(0, 1fr) 56px; gap: 7px; }
  .chip-row { padding-left: 4px; padding-right: 4px; }
}
</style>
