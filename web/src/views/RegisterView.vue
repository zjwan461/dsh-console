<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { api, errorMessage } from '@/api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const baseDomain = ref('dsh.example.com')
const form = reactive({
  code: typeof route.query.code === 'string' ? route.query.code : '',
  username: '',
  password: '',
  confirm: '',
  email: '',
  display_name: '',
})

async function submit() {
  if (!form.code || !form.username || !form.password) {
    ElMessage.warning('请填写邀请码、用户名和密码')
    return
  }
  if (form.password.length < 8) {
    ElMessage.warning('密码至少 8 位')
    return
  }
  if (form.password !== form.confirm) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  loading.value = true
  try {
    await auth.register({
      code: form.code.trim(),
      username: form.username.trim(),
      password: form.password,
      email: form.email.trim() || undefined,
      display_name: form.display_name.trim() || undefined,
    })
    ElMessage.success('注册成功')
    router.replace('/')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const info = await api.get<{ base_domain: string }>('/system/info')
    if (info.base_domain) baseDomain.value = info.base_domain
  } catch {
    // keep the placeholder domain
  }
})
</script>

<template>
  <div class="auth-shell">
    <div class="auth-card">
      <h1>创建账号</h1>
      <p class="sub">使用管理员发放的邀请码注册</p>

      <el-form label-position="top">
        <el-form-item label="邀请码">
          <el-input v-model="form.code" size="large" placeholder="INV-..." />
        </el-form-item>
        <el-form-item label="用户名（即你的子域名）">
          <el-input v-model="form.username" size="large" placeholder="alice">
            <template #append>.{{ baseDomain }}</template>
          </el-input>
        </el-form-item>
        <el-form-item label="显示名称（可选）">
          <el-input v-model="form.display_name" size="large" placeholder="Alice" />
        </el-form-item>
        <el-form-item label="邮箱（可选）">
          <el-input v-model="form.email" size="large" placeholder="alice@example.com" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" size="large" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input
            v-model="form.confirm"
            size="large"
            type="password"
            show-password
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" size="large" class="submit" :loading="loading" @click="submit">
          注册并登录
        </el-button>
      </el-form>

      <div class="footer">
        已有账号？<router-link to="/login">去登录</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
