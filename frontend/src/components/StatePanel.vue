<!-- 本文件统一展示页面加载、空结果和失败状态。 -->
<script setup lang="ts">
import AppIcon from './AppIcon.vue';
withDefaults(defineProps<{
    kind?: 'loading' | 'empty' | 'error';
    title: string;
    description?: string;
    action?: string;
}>(), { kind: 'empty' });
defineEmits<{
    action: [
    ];
}>();
</script>
<template>
  <div class="state-panel" role="status" :aria-busy="kind === 'loading'">
    <div class="state-symbol" :class="{ spinning: kind === 'loading' }">
      <AppIcon :name="kind === 'loading' ? 'refresh' : kind === 'error' ? 'info' : 'search'" :size="28" />
    </div>
    <h3>{{ title }}</h3>
    <p v-if="description">{{ description }}</p>
    <el-button v-if="action" @click="$emit('action')">{{ action }}</el-button>
  </div>
</template>
