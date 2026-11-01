<!-- 本文件构建应用导航、服务器会话与真实行情状态。 -->
<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import AppIcon from './components/AppIcon.vue';
import { useMarket, useSession } from './stores.ts';
const route = useRoute();
const router = useRouter();
const session = useSession();
const market = useMarket();
const menuOpen = ref(false);
async function signOut(): Promise<void> {
    try { await session.logout(); } catch (cause) { ElMessage.error((cause as Error).message); }
    finally { market.status = null; await router.push('/login'); }
}
function command(value: string): void { if (value === 'logout') void signOut(); if (value === 'password') void router.push('/admin/password'); }
function expired(): void { void router.replace({ path: '/login', query: { expired: '1' } }); }
window.addEventListener('stocksieve:session-expired', expired);
onUnmounted(() => window.removeEventListener('stocksieve:session-expired', expired));
router.afterEach(() => { menuOpen.value = false; });
watch(() => session.user?.id, (id) => { market.status = null; if (id) void market.refresh().catch(() => {}); }, { immediate: true });
</script>

<template>
  <router-view v-if="route.meta.public" />
  <div v-else class="app-shell">
    <div v-if="menuOpen" class="sidebar-backdrop" @click="menuOpen = false" />
    <aside class="sidebar" :class="{ 'is-open': menuOpen }">
      <router-link to="/opportunities" class="brand">
        <span class="brand-symbol">
          <AppIcon name="chart" :size="26" />
        </span>
        <span>
          StockSieve
          <small>收盘后的思考</small>
        </span>
      </router-link>
      <div class="nav-label">投资工作台</div>
      <nav aria-label="主导航">
        <router-link to="/opportunities" :class="{ 'router-link-active': route.path.startsWith('/stocks/') }">
          <AppIcon name="grid" />
          机会筛选
          <span class="nav-dot" />
        </router-link>
        <template v-if="session.isAdmin">
          <div class="nav-label">管理中心</div>
          <router-link to="/admin/data">
            <AppIcon name="database" />
            数据管理
          </router-link>
          <router-link to="/admin/users">
            <AppIcon name="users" />
            用户管理
          </router-link>
          <router-link to="/admin/password">
            <AppIcon name="lock" />
            修改密码
          </router-link>
        </template>
      </nav>
      <div class="sidebar-note">
        <span class="eyebrow">AFTER THE CLOSE</span>
        <p>
          让每一个入选理由，
          <br />
          都有迹可循。
        </p>
        <span>{{ market.status?.marketBoards?.includes('BSE') ? '沪深北 · 五类策略' : '沪深四板块 · 五类策略' }}</span>
      </div>
      <button class="sidebar-user" @click="signOut">
        <span class="avatar">{{ session.isAdmin ? '管' : '用' }}</span>
        <span>
          {{ session.user?.name }}
          <small>{{ session.isAdmin ? '管理员' : '普通用户' }}</small>
        </span>
        <AppIcon name="logout" :size="17" />
      </button>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <button class="icon-button mobile-menu" aria-label="展开导航" @click="menuOpen = !menuOpen">
          <AppIcon name="menu" />
        </button>
        <div class="breadcrumb">
          工作台
          <span>/</span>
          <strong>{{ route.meta.title }}</strong>
        </div>
        <div class="topbar-actions">
          <span class="mock-label">
            <i />
            收盘工作台
          </span>
          <el-dropdown @command="command">
            <button class="avatar avatar-button" aria-label="账号菜单">{{ session.isAdmin ? '管' : '用' }}</button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="session.isAdmin" command="password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>
      <div class="demo-strip">
        <AppIcon name="info" :size="15" />
        <span>{{ market.status?.marketBoards?.includes('BSE') ? '以已发布收盘数据为准。' : '当前覆盖沪深四板块，北交所暂不可用。' }}</span>
        <span class="demo-date">收盘数据 {{ market.status?.publishedSnapshot?.dataDate ?? '尚未发布' }}</span>
      </div>
      <main class="workspace">
        <router-view :key="route.path" />
      </main>
      <footer class="app-footer">
        <span>StockSieve · 收盘后选股工作台</span>
        <span>按收盘数据研究 · AI结果按报告标识展示</span>
      </footer>
    </div>
  </div>
</template>
