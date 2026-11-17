<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { errorMessage } from '@/api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const form = reactive({ username: '', password: '', remember: true })

async function submit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password, form.remember)
    const next = typeof route.query.next === 'string' ? route.query.next : '/'
    router.replace(next)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-shell">
    <div class="auth-card">
      <h1>优墨云舱</h1>
      <p class="sub">登录后管理你的 DeepSeek Harness 实例</p>

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input
            v-model="form.username"
            size="large"
            placeholder="alice"
            autocomplete="username"
            autofocus
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            size="large"
            type="password"
            show-password
            placeholder="••••••••"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <div class="row">
          <el-checkbox v-model="form.remember">保持登录 7 天</el-checkbox>
        </div>
        <el-button type="primary" size="large" class="submit" :loading="loading" @click="submit">
          登录
        </el-button>
      </el-form>

      <div class="footer">
        有邀请码？<router-link to="/register">注册账号</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 18px;
}

.submit {
  width: 100%;
}

.footer {
  margin-top: 20px;
  text-align: center;
  color: var(--dsh-muted);
  font-size: 13px;
}
</style>
