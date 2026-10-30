<!-- 本文件提供项目使用的轻量 SVG 图标，避免额外图标依赖。 -->
<script setup lang="ts">
withDefaults(defineProps<{
    name: string;
    size?: number;
}>(), { size: 20 });
const paths: Record<string, string> = {
    chart: 'M4 18V11m5 7V5m5 13v-8m5 8V3',
    grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    database: 'M20 6c0 2-4 3-8 3S4 8 4 6s4-3 8-3 8 1 8 3ZM4 6v6c0 2 4 3 8 3s8-1 8-3V6M4 12v6c0 2 4 3 8 3s8-1 8-3v-6',
    users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.9M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm8-7.8a4 4 0 0 1 0 7.6',
    lock: 'M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4',
    logout: 'M9 21H4V3h5m6 4 5 5-5 5m-5-5h10',
    search: 'M21 21l-5-5M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z',
    arrow: 'M5 12h14m-6-6 6 6-6 6',
    back: 'M19 12H5m6-6-6 6 6 6',
    info: 'M12 11v6m0-10v.01M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0Z',
    refresh: 'M20 7v5h-5M4 17v-5h5m-4.5-4a8 8 0 0 1 13-4L20 7M4 17l2.5 3a8 8 0 0 0 13-4',
    spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
    check: 'm5 12 4 4L19 6',
    close: 'm6 6 12 12M6 18 18 6',
    sliders: 'M4 6h16M4 18h16M8 3v6m8 6v6',
    menu: 'M3 6h18M3 12h18M3 18h18',
    clock: 'M12 7v5l3 2M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0Z',
    layers: 'm3 7 9-4 9 4-9 4-9-4Zm0 5 9 4 9-4M3 17l9 4 9-4',
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] ?? paths.info" />
  </svg>
</template>
