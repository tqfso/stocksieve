<!-- 本文件为无效前端路由提供恢复入口。 -->
<script setup lang="ts">
import { useRouter } from 'vue-router';
import StatePanel from '../components/StatePanel.vue';
const router = useRouter();
</script>
<template>
  <section class="panel">
    <StatePanel title="这个页面暂不存在" description="请从机会列表或左侧导航继续浏览。" action="返回机会列表" @action="router.push('/opportunities')" />
  </section>
</template>
