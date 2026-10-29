<!-- 本文件提供管理员用户列表、搜索、角色筛选及新增普通用户的模拟流程。 -->
<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import AppIcon from '../components/AppIcon.vue';
import StatePanel from '../components/StatePanel.vue';
import { addUser, listUsers } from '../mock/service.ts';
const users = ref(listUsers());
const search = ref('');
const role = ref('');
const dialogOpen = ref(false);
const loading = ref(false);
const error = ref('');
const form = reactive({ username: '', name: '', password: '', confirm: '' });
const visible = computed(() => users.value.filter((user) => `${user.username}${user.name}`.toLowerCase().includes(search.value.trim().toLowerCase()) && (!role.value || user.role === role.value)));
function openDialog(): void { Object.assign(form, { username: '', name: '', password: '', confirm: '' }); error.value = ''; dialogOpen.value = true; }
async function submit(): Promise<void> {
    if (loading.value)
        return;
    error.value = '';
    if (form.password !== form.confirm) {
        error.value = '两次输入的密码不一致。';
        return;
    }
    loading.value = true;
    try {
        const user = await addUser(form.username.trim(), form.name, form.password);
        users.value = listUsers();
        dialogOpen.value = false;
        ElMessage.success(`用户 ${user.username} 已创建，可退出当前账号后验证登录。`);
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
  <div class="page-heading">
    <div>
      <span class="eyebrow">ACCESS MANAGEMENT</span>
      <h1>为每位研究者开放工作台</h1>
      <p>管理员创建普通用户，普通用户可浏览机会并按需生成模拟报告。</p>
    </div>
    <el-button type="primary" @click="openDialog">
      <AppIcon name="users" :size="17" />
      新增普通用户
    </el-button>
  </div>
  <div class="summary-grid">
    <div class="summary-card">
      <span>全部账号</span>
      <strong>
        {{ users.length }}
        <small>个</small>
      </strong>
      <p>账号记录仅保存在当前浏览器</p>
    </div>
    <div class="summary-card">
      <span>普通用户</span>
      <strong>
        {{ users.filter(user => user.role === 'user').length }}
        <small>位</small>
      </strong>
      <p>机会筛选、个股详情、模拟 AI 分析</p>
    </div>
    <div class="summary-card">
      <span>管理员</span>
      <strong>
        {{ users.filter(user => user.role === 'admin').length }}
        <small>位</small>
      </strong>
      <p>额外拥有数据和用户管理权限</p>
    </div>
  </div>
  <section class="panel">
    <div class="section-caption">
      <strong>用户列表</strong>
      <div class="inline-filters">
        <el-input v-model="search" placeholder="搜索账号或名称" clearable aria-label="搜索用户">
          <template #prefix>
            <AppIcon name="search" :size="16" />
          </template>
        </el-input>
        <el-select v-model="role" placeholder="全部角色" clearable aria-label="用户角色">
          <el-option label="管理员" value="admin" />
          <el-option label="普通用户" value="user" />
        </el-select>
      </div>
    </div>
    <StatePanel v-if="!visible.length" title="没有找到匹配的用户" action="清除筛选" @action="search = ''; role = ''" />
    <div v-else class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>用户</th>
            <th>账号</th>
            <th>角色</th>
            <th>创建时间（北京时间）</th>
            <th>可用功能</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in visible" :key="user.id">
            <td>
              <div class="user-cell">
                <span class="avatar" :class="{ 'user-avatar': user.role === 'user' }">{{ user.name.slice(0, 1) }}</span>
                <strong>{{ user.name }}</strong>
              </div>
            </td>
            <td class="numeric">{{ user.username }}</td>
            <td>
              <span class="tag" :class="user.role === 'admin' ? 'tag-green' : ''">{{ user.role === 'admin' ? '管理员' : '普通用户' }}</span>
            </td>
            <td>{{ user.createdAt }}</td>
            <td class="muted">{{ user.role === 'admin' ? '全部前端功能' : '机会筛选与个股分析' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="table-footer">
      <span>共 {{ visible.length }} 个账号</span>
      <span>密码不会在用户列表中展示</span>
    </div>
  </section>
  <el-dialog
    v-model="dialogOpen"
    title="新增普通用户"
    width="min(500px, 92vw)"
    :close-on-click-modal="!loading"
    :close-on-press-escape="!loading"
    :show-close="!loading"
  >
    <p class="muted">创建后即可用新账号登录此模拟版。请勿填写真实账号或常用密码。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="form-alert" />
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="登录账号">
        <el-input v-model="form.username" maxlength="20" placeholder="3～20 位，以字母开头" autocomplete="off" :disabled="loading" />
      </el-form-item>
      <el-form-item label="用户名称">
        <el-input v-model="form.name" maxlength="20" placeholder="请输入用户名称" :disabled="loading" />
      </el-form-item>
      <el-form-item label="初始密码">
        <el-input
          v-model="form.password"
          type="password"
          show-password
          maxlength="32"
          placeholder="6～32 位，仅使用演示密码"
          autocomplete="new-password"
          :disabled="loading"
        />
      </el-form-item>
      <el-form-item label="确认密码">
        <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password" :disabled="loading" />
      </el-form-item>
      <div class="form-actions">
        <el-button :disabled="loading" @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" native-type="submit" :loading="loading">创建用户</el-button>
      </div>
    </el-form>
  </el-dialog>
</template>
