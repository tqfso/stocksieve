<!-- 本文件构建应用导航、全局身份信息及评审场景控制。 -->
<script setup lang="ts">
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import AppIcon from './components/AppIcon.vue';
import { DATA_DATE } from './mock/fixtures.ts';
import { resetDemo, storageAvailable } from './mock/service.ts';
import { useReview, useSession } from './stores.ts';
const route = useRoute();
const router = useRouter();
const session = useSession();
const review = useReview();
const reviewOpen = ref(false);
const menuOpen = ref(false);
function signOut(): void { session.logout(); void router.push('/login'); }
async function reset(): Promise<void> {
    try {
        await ElMessageBox.confirm('将清除本浏览器中的演示用户、密码修改、报告和任务记录，并恢复默认账号。', '重置演示数据', { confirmButtonText: '确认重置', cancelButtonText: '取消', type: 'warning' });
        resetDemo();
        window.location.hash = '#/login';
        window.location.reload();
    }
    catch { /* 取消重置时保留当前评审进度。 */ }
}
function command(value: string): void {
    if (value === 'logout')
        signOut();
    if (value === 'password')
        void router.push('/admin/password');
}
router.afterEach(() => { menuOpen.value = false; });
if (!storageAvailable)
    ElMessage.warning('浏览器存储不可用，本次演示的修改可能无法在刷新后保留。');
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
        <span>全 A 股 · 五类策略</span>
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
            模拟评审版
          </span>
          <el-button text @click="reviewOpen = true">
            <AppIcon name="sliders" :size="16" />
            <span>演示场景</span>
          </el-button>
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
        <span>当前全部使用模拟数据，不连接行情、AI 或后端服务。</span>
        <span class="demo-date">示例数据日期 {{ DATA_DATE }}</span>
      </div>
      <main class="workspace">
        <router-view :key="route.path" />
      </main>
      <footer class="app-footer">
        <span>StockSieve · 收盘后选股工作台</span>
        <span>行情、信号与报告均为虚构示例</span>
      </footer>
    </div>
    <el-dialog v-model="reviewOpen" title="演示场景" width="min(520px, 92vw)">
      <p class="muted">切换状态以评审异常流程。数据修改仅保存在当前浏览器，场景开关刷新后恢复默认。</p>
      <el-form label-position="top" class="review-form">
        <el-form-item label="机会列表">
          <el-radio-group v-model="review.listScenario">
            <el-radio-button value="normal">正常</el-radio-button>
            <el-radio-button value="empty">空结果</el-radio-button>
            <el-radio-button value="error">请求失败</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="AI 分析">
          <el-radio-group v-model="review.aiScenario">
            <el-radio-button value="normal">资料完整</el-radio-button>
            <el-radio-button value="partial">资料不完整</el-radio-button>
            <el-radio-button value="failed">分析失败</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="session.isAdmin" label="数据同步">
          <el-switch v-model="review.syncFail" active-text="模拟任务失败" inactive-text="正常完成" />
        </el-form-item>
      </el-form>
      <div class="review-reset">
        <div>
          <strong>恢复初始演示</strong>
          <p>清空本地修改，并退出当前账号。</p>
        </div>
        <el-button type="danger" plain @click="reset">重置演示</el-button>
      </div>
      <template #footer>
        <el-button type="primary" @click="reviewOpen = false">完成</el-button>
      </template>
    </el-dialog>
  </div>
</template>
