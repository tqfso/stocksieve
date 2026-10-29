<!-- 本文件提供管理员修改自身模拟密码的校验及重新登录流程。 -->
<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import AppIcon from '../components/AppIcon.vue';
import { changePassword } from '../mock/service.ts';
import { useSession } from '../stores.ts';
const session = useSession();
const router = useRouter();
const form = reactive({ old: '', password: '', confirm: '' });
const error = ref('');
const loading = ref(false);
async function submit(): Promise<void> {
    if (loading.value)
        return;
    error.value = '';
    if (!form.old || !form.password) {
        error.value = '请输入当前密码和新密码。';
        return;
    }
    if (form.password !== form.confirm) {
        error.value = '两次输入的新密码不一致。';
        return;
    }
    loading.value = true;
    try {
        await changePassword(form.old, form.password);
        session.logout();
        ElMessage.success('模拟密码已修改，请使用新密码重新登录。');
        await router.replace('/login');
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
      <span class="eyebrow">ACCOUNT SETTINGS</span>
      <h1>修改登录密码</h1>
      <p>修改当前管理员账号的演示密码，保存后重新登录。</p>
    </div>
  </div>
  <section class="panel password-panel">
    <div class="section-caption">
      <strong>
        <AppIcon name="lock" :size="19" />
        账号设置
      </strong>
      <span class="tag">{{ session.user?.username }}</span>
    </div>
    <div class="password-body">
      <el-alert title="密码仅保存在本浏览器中，请使用演示密码。忘记后可在“演示场景”中重置全部演示数据。" type="info" :closable="false" show-icon />
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="form-alert" />
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="当前密码">
          <el-input v-model="form.old" type="password" show-password autocomplete="current-password" :disabled="loading" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="请输入 6～32 位新密码"
            :disabled="loading"
          />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password" :disabled="loading" />
        </el-form-item>
        <div class="form-actions">
          <el-button :disabled="loading" @click="Object.assign(form, { old: '', password: '', confirm: '' }); error = ''">清空</el-button>
          <el-button type="primary" native-type="submit" :loading="loading">保存并重新登录</el-button>
        </div>
      </el-form>
    </div>
  </section>
</template>
