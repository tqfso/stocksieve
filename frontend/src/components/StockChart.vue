<!-- 本文件绘制模拟 OHLC、成交量与均线，提供周期、缩放、拖动和十字光标交互。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { aggregateCandles, generateCandles } from '../mock/market.ts';
import { calculateMovingAverages, MA_PERIODS } from '../moving-averages.ts';
import type { MovingAveragePeriod } from '../moving-averages.ts';
import type { Period, Stock } from '../types.ts';
const props = defineProps<{
    stock: Stock;
}>();
const period = ref<Period>('day');
const windowSize = ref(60);
const offset = ref(0);
const hovered = ref<number | null>(null);
const averages = ref<MovingAveragePeriod[]>([...MA_PERIODS]);
const chart = ref<SVGSVGElement>();
const dragging = ref(false);
let dragStart = 0;
let dragOffset = 0;
const daily = computed(() => generateCandles(props.stock));
const all = computed(() => aggregateCandles(daily.value, period.value));
const end = computed(() => all.value.length - offset.value);
const start = computed(() => Math.max(0, end.value - windowSize.value));
const visible = computed(() => all.value.slice(start.value, end.value));
const step = computed(() => 910 / Math.max(1, visible.value.length));
const active = computed(() => visible.value[hovered.value ?? visible.value.length - 1]);
const averageSeries = computed(() => calculateMovingAverages(all.value));
// 纵轴同时容纳已选均线，避免较长周期的均线被图表裁切。
const visibleAverages = computed(() => averages.value.flatMap((size) => averageSeries.value[size].slice(start.value, end.value).filter((value): value is number => value !== null)));
const upper = computed(() => Math.max(...visible.value.map((candle) => candle.high), ...visibleAverages.value) * 1.025);
const lower = computed(() => Math.min(...visible.value.map((candle) => candle.low), ...visibleAverages.value) * 0.975);
const maxVolume = computed(() => Math.max(...visible.value.map((candle) => candle.volume), 1));
const priceTicks = computed(() => Array.from({ length: 5 }, (_, index) => upper.value - (upper.value - lower.value) * index / 4));
const tickIndexes = computed(() => [...new Set([0, Math.floor(visible.value.length / 3), Math.floor(visible.value.length * 2 / 3), visible.value.length - 1])]);
const averageColors = Object.fromEntries(MA_PERIODS.map((size) => [size, `var(--ma${size})`])) as Record<MovingAveragePeriod, string>;
function x(index: number): number { return 12 + step.value * (index + 0.5); }
function y(price: number): number { return 15 + (upper.value - price) / Math.max(0.01, upper.value - lower.value) * 225; }
function color(open: number, close: number): string { return close >= open ? 'var(--rise)' : 'var(--fall)'; }
function line(size: MovingAveragePeriod): string {
    let drawing = false;
    return averageSeries.value[size]!.slice(start.value, end.value).map((value, index) => {
        if (value === null) {
            drawing = false;
            return '';
        }
        const command = drawing ? 'L' : 'M';
        drawing = true;
        return `${command}${x(index).toFixed(2)},${y(value).toFixed(2)}`;
    }).join(' ');
}
function move(event: PointerEvent): void {
    const rect = chart.value?.getBoundingClientRect();
    if (!rect)
        return;
    if (dragging.value)
        offset.value = Math.max(0, Math.min(all.value.length - Math.min(windowSize.value, all.value.length), dragOffset + Math.round((event.clientX - dragStart) / rect.width * visible.value.length)));
    hovered.value = Math.max(0, Math.min(visible.value.length - 1, Math.floor(((event.clientX - rect.left) / rect.width * 1000 - 12) / step.value)));
}
function beginDrag(event: PointerEvent): void { if (event.button !== 0)
    return; dragging.value = true; dragStart = event.clientX; dragOffset = offset.value; chart.value?.setPointerCapture(event.pointerId); }
function stopDrag(): void { dragging.value = false; }
function zoom(delta: number): void { windowSize.value = Math.max(10, Math.min(180, windowSize.value + delta)); offset.value = Math.min(offset.value, Math.max(0, all.value.length - windowSize.value)); hovered.value = null; }
function averageLabel(size: MovingAveragePeriod): string { const value = averageSeries.value[size]?.[start.value + (hovered.value ?? visible.value.length - 1)]; return value === null || value === undefined ? '—' : value.toFixed(2); }
watch(period, () => { offset.value = 0; hovered.value = null; windowSize.value = period.value === 'year' ? 20 : 60; });
watch(() => props.stock.code, () => { offset.value = 0; hovered.value = null; });
</script>
<template>
  <section class="panel chart-panel">
    <div class="chart-toolbar">
      <div class="segment-control">
        <button
          v-for="item in ([['day', '日 K'], ['week', '周 K'], ['year', '年 K']] as const)"
          :key="item[0]"
          :class="{ active: period === item[0] }"
          @click="period = item[0]"
        >{{ item[1] }}</button>
      </div>
      <div class="chart-tools">
        <el-button size="small" aria-label="放大 K 线" @click="zoom(-20)">＋</el-button>
        <el-button size="small" aria-label="缩小 K 线" @click="zoom(20)">－</el-button>
        <el-button size="small" @click="offset = 0; windowSize = period === 'year' ? 20 : 60; hovered = null">最近数据</el-button>
      </div>
    </div>
    <div v-if="active" class="ohlc-strip">
      <strong>{{ active.time }}</strong>
      <span>
        开
        <b>{{ active.open.toFixed(2) }}</b>
      </span>
      <span>
        高
        <b>{{ active.high.toFixed(2) }}</b>
      </span>
      <span>
        低
        <b>{{ active.low.toFixed(2) }}</b>
      </span>
      <span>
        收
        <b :class="active.close >= active.open ? 'rise' : 'fall'">{{ active.close.toFixed(2) }}</b>
      </span>
      <span>
        量
        <b>{{ (active.volume / 10000).toFixed(1) }} 万股</b>
      </span>
    </div>
    <div class="ma-legend">
      <label v-for="size in MA_PERIODS" :key="size" :style="{ color: averageColors[size] }">
        <input v-model="averages" type="checkbox" :value="size" />
        MA
        {{ size }}
        <b>{{ averageLabel(size) }}</b>
      </label>
      <span>均线按当前周期计算 · — 表示历史不足</span>
    </div>
    <svg
      ref="chart"
      class="candlestick-chart"
      :class="{ dragging }"
      viewBox="0 0 1000 350"
      preserveAspectRatio="none"
      role="img"
      :aria-label="`${stock.name}模拟${period === 'day' ? '日' : period === 'week' ? '周' : '年'}K线，拖动查看历史，使用按钮缩放`"
      @pointermove="move"
      @pointerdown="beginDrag"
      @pointerup="stopDrag"
      @pointercancel="stopDrag"
      @pointerleave="hovered = null"
      @wheel.prevent="zoom($event.deltaY > 0 ? 10 : -10)"
    >
      <defs>
        <clipPath id="price-clip">
          <rect x="10" y="8" width="918" height="241" />
        </clipPath>
      </defs>
      <g v-for="price in priceTicks" :key="price">
        <line x1="10" x2="928" :y1="y(price)" :y2="y(price)" stroke="var(--line)" stroke-dasharray="3 4" />
        <text x="945" :y="y(price) + 4" class="chart-axis">{{ price.toFixed(2) }}</text>
      </g>
      <g clip-path="url(#price-clip)">
        <g
          v-for="(candle, index) in visible"
          :key="candle.time"
          :stroke="color(candle.open, candle.close)"
          :fill="color(candle.open, candle.close)"
        >
          <line :x1="x(index)" :x2="x(index)" :y1="y(candle.high)" :y2="y(candle.low)" />
          <rect
            :x="x(index) - Math.min(15, step * 0.28)"
            :y="Math.min(y(candle.open), y(candle.close))"
            :width="Math.min(30, step * 0.56)"
            :height="Math.max(1.2, Math.abs(y(candle.open) - y(candle.close)))"
          />
        </g>
        <path
          v-for="size in averages"
          :key="size"
          :d="line(size)"
          fill="none"
          :stroke="averageColors[size]"
          stroke-width="1.4"
        />
      </g>
      <line x1="10" x2="928" y1="260" y2="260" stroke="var(--line)" />
      <text x="12" y="277" class="chart-axis">成交量</text>
      <rect
        v-for="(candle, index) in visible"
        :key="`v${candle.time}`"
        :x="x(index) - Math.min(15, step * 0.28)"
        :y="320 - candle.volume / maxVolume * 43"
        :width="Math.min(30, step * 0.56)"
        :height="candle.volume / maxVolume * 43"
        :fill="color(candle.open, candle.close)"
        opacity="0.58"
      />
      <text
        v-for="index in tickIndexes"
        :key="index"
        :x="x(index)"
        y="343"
        :text-anchor="index === 0 ? 'start' : 'middle'"
        class="chart-axis"
      >{{ period === 'year' ? visible[index]?.time.slice(0, 4) : visible[index]?.time.slice(0, 10) }}</text>
      <g v-if="hovered !== null && active">
        <line :x1="x(hovered)" :x2="x(hovered)" y1="8" y2="322" stroke="var(--muted)" stroke-dasharray="4 3" />
        <line x1="10" x2="928" :y1="y(active.close)" :y2="y(active.close)" stroke="var(--muted)" stroke-dasharray="4 3" />
        <rect x="932" :y="y(active.close) - 10" width="68" height="20" rx="3" fill="var(--accent)" />
        <text x="966" :y="y(active.close) + 4" text-anchor="middle" fill="white" class="chart-price-label">{{ active.close.toFixed(2) }}</text>
      </g>
    </svg>
    <div class="chart-footnote">
      <span>前复权展示口径 · 工作日生成的虚构走势</span>
      <span>拖动查看历史 · 滚轮或 ＋ / － 缩放</span>
    </div>
  </section>
</template>
