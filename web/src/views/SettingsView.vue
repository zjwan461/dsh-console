<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tab = ref('profile')
const savingProfile = ref(false)
const savingPassword = ref(false)
const savingKey = ref(false)
const keyHint = ref('')
const keyConfigured = ref(false)
const platformConfigured = ref(false)
const keyInput = ref('')

const profile = reactive({ display_name: '', email: '' })
const password = reactive({ old_password: '', new_password: '', confirm: '' })

async function loadCredentials() {
  try {
    const data = await api.get<{
      platform_key_configured: boolean
      user_key: { configured: boolean; hint: string }
    }>('/me/credentials')
    platformConfigured.value = data.platform_key_configured
    keyConfigured.value = data.user_key.configured
    keyHint.value = data.user_key.hint
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function saveProfile() {
  savingProfile.value = true
  try {
    await api.patch('/me/profile', {
      display_name: profile.display_name,
      email: profile.email || null,
    })
    await auth.refresh()
    ElMessage.success('资料已更新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    savingProfile.value = false
  }
}

async function savePassword() {
  if (password.new_password.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  if (password.new_password !== password.confirm) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  savingPassword.value = true
  try {
    await api.post('/auth/password', {
      old_password: password.old_password,
      new_password: password.new_password,
    })
    password.old_password = ''
    password.new_password = ''
    password.confirm = ''
    ElMessage.success('密码已修改，其他设备已下线')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    savingPassword.value = false
  }
}

async function saveKey() {
  savingKey.value = true
  try {
    const result = await api.put<{ configured: boolean; hint: string }>('/me/credentials', {
      api_key: keyInput.value,
    })
    keyConfigured.value = result.configured
    keyHint.value = result.hint
    keyInput.value = ''
    ElMessage.success(result.configured ? '已保存，重启实例后生效' : '已清除，将使用平台 Key')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    savingKey.value = false
  }
}

onMounted(() => {
  profile.display_name = auth.user?.display_name ?? ''
  profile.email = auth.user?.email ?? ''
  loadCredentials()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>设置</h1>
        <p>账号资料、登录密码与模型 API Key</p>
      </div>
    </div>

    <div class="card">
      <el-tabs v-model="tab">
        <el-tab-pane label="资料" name="profile">
          <el-form label-width="110px" style="max-width: 520px">
            <el-form-item label="用户名">
              <el-input :model-value="auth.user?.username" disabled />
            </el-form-item>
            <el-form-item label="子域名">
              <el-input :model-value="auth.user?.subdomain" disabled />
            </el-form-item>
            <el-form-item label="显示名称">
              <el-input v-model="profile.display_name" maxlength="64" />
            </el-form-item>
            <el-form-item label="邮箱">
              <el-input v-model="profile.email" placeholder="可选" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingProfile" @click="saveProfile">
                保存
              </el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="安全" name="security">
          <el-form label-width="110px" style="max-width: 520px">
            <el-form-item label="当前密码">
              <el-input v-model="password.old_password" type="password" show-password />
            </el-form-item>
            <el-form-item label="新密码">
              <el-input v-model="password.new_password" type="password" show-password />
            </el-form-item>
            <el-form-item label="确认新密码">
              <el-input v-model="password.confirm" type="password" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingPassword" @click="savePassword">
                修改密码
              </el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="模型 Key" name="key">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="默认使用平台 Key；填写自己的 DeepSeek Key 后优先使用你的。"
            description="保存后需要重启实例才能生效。Key 加密存储，接口只返回掩码。"
            style="margin-bottom: 18px"
          />
          <el-form label-width="110px" style="max-width: 560px">
            <el-form-item label="平台 Key">
              <el-tag :type="platformConfigured ? 'success' : 'info'">
                {{ platformConfigured ? '已配置' : '未配置' }}
              </el-tag>
            </el-form-item>
            <el-form-item label="我的 Key">
              <el-tag :type="keyConfigured ? 'success' : 'info'">
                {{ keyConfigured ? `已配置 ${keyHint}` : '未配置（使用平台 Key）' }}
              </el-tag>
            </el-form-item>
            <el-form-item label="设置 Key">
              <el-input
                v-model="keyInput"
                placeholder="sk-...（留空表示清除）"
                show-password
                type="password"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingKey" @click="saveKey">保存</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>
