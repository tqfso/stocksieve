<!-- 本文件提供模拟账号登录、表单校验及角色快速填入。 -->
<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppIcon from '../components/AppIcon.vue';
import { useSession } from '../stores.ts';
const session = useSession();
const router = useRouter();
const route = useRoute();
const form = reactive({ username: '', password: '' });
const loading = ref(false);
const error = ref('');
function fill(username: string): void { form.username = username; form.password = 'demo1234'; error.value = ''; }
async function submit(): Promise<void> {
    error.value = '';
    if (!form.username.trim() || !form.password) {
        error.value = '请输入账号和密码。';
        return;
    }
    loading.value = true;
    try {
        await session.login(form.username, form.password);
        const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') && !route.query.redirect.startsWith('//') ? route.query.redirect : '/opportunities';
        await router.replace(redirect);
    }
    catch (cause) {
        error.value = (cause as Error).message;
    }
    finally {
        loading.value = false;
    }
}
</script>
<template>
  <div class="login-layout">
    <section class="login-story">
      <div class="brand">
        <span class="brand-symbol">
          <AppIcon name="chart" :size="28" />
        </span>
        <span>
          StockSieve
          <small>收盘后的思考</small>
        </span>
      </div>
      <div class="login-story-content">
        <span class="eyebrow">CLARITY AFTER THE CLOSE</span>
        <h1>
          市场收盘，
          <br />
          思考开始。
        </h1>
        <p>
          从全市场的纷繁信号中，
          <br />
          筛出值得进一步研究的机会。
        </p>
        <div class="story-chips">
          <span>规则筛选</span>
          <span>量价观察</span>
          <span>按需分析</span>
        </div>
      </div>
      <div class="login-chart" aria-hidden="true">
        <i v-for="n in 24" :key="n" :style="{ height: `${30 + (n * 17 % 47) + n * 4}px`, opacity: 0.25 + n / 40 }" />
      </div>
      <div class="login-story-footer">
        全 A 股覆盖
        <span>上海 · 深圳 · 北京</span>
      </div>
    </section>
    <section class="login-form-side">
      <span class="mock-label">
        <i />
        前端模拟评审版
      </span>
      <div class="login-card">
        <span class="eyebrow">WELCOME BACK</span>
        <h2>登录工作台</h2>
        <p class="muted">从今天的机会列表，开始你的研究。</p>
        <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="form-alert" />
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="账号">
            <el-input v-model="form.username" placeholder="请输入账号" size="large" autocomplete="username" :disabled="loading" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="请输入密码"
              size="large"
              autocomplete="current-password"
              :disabled="loading"
            />
          </el-form-item>
          <el-button type="primary" size="large" native-type="submit" :loading="loading" class="login-submit">
            登录工作台
            <AppIcon name="arrow" :size="17" />
          </el-button>
        </el-form>
        <div class="demo-accounts">
          <span>快速填入演示账号</span>
          <div>
            <el-button @click="fill('admin')">管理员 admin</el-button>
            <el-button @click="fill('viewer')">普通用户 viewer</el-button>
          </div>
          <small>初始密码均为 demo1234；新增用户也可在此登录。</small>
        </div>
        <p class="login-disclaimer">仅用于产品评审。账号和密码保存在当前浏览器，请使用演示信息。</p>
      </div>
      <span class="login-copyright">STOCKSIEVE / PRODUCT PREVIEW</span>
    </section>
  </div>
</template>
