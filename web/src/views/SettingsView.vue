<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
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
const platformBaseUrl = ref<string | null>(null)
const userBaseUrl = ref<string | null>(null)
const effectiveBaseUrl = ref<string | null>(null)
const effectiveModel = ref<string | null>(null)
const keyInput = ref('')
const baseUrlInput = ref('')
const modelInput = ref('')

const profile = reactive({ display_name: '', email: '' })
const password = reactive({ old_password: '', new_password: '', confirm: '' })

async function loadCredentials() {
  try {
    const data = await api.get<{
      platform_key_configured: boolean
      platform_base_url: string | null
      user_key: {
        configured: boolean
        hint: string
        base_url: string | null
        model: string | null
      }
      effective_base_url: string | null
      effective_model: string | null
    }>('/me/credentials')
    platformConfigured.value = data.platform_key_configured
    platformBaseUrl.value = data.platform_base_url
    keyConfigured.value = data.user_key.configured
    keyHint.value = data.user_key.hint
    userBaseUrl.value = data.user_key.base_url
    effectiveBaseUrl.value = data.effective_base_url
    effectiveModel.value = data.effective_model
    baseUrlInput.value = data.user_key.base_url ?? ''
    modelInput.value = data.user_key.model ?? ''
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
    const result = await api.put<{
      configured: boolean
      hint: string
      base_url: string | null
      model: string | null
    }>('/me/credentials', {
      api_key: keyInput.value,
      // Always send the endpoint and model: empty means "official default", not
      // "leave it alone", which is what the restore button relies on.
      base_url: baseUrlInput.value,
      model: modelInput.value,
    })
    keyConfigured.value = result.configured
    keyHint.value = result.hint
    userBaseUrl.value = result.base_url
    keyInput.value = ''
    ElMessage.success(result.configured ? '已保存，重启实例后生效' : '已清除，将使用平台 Key')
    await loadCredentials()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    savingKey.value = false
  }
}

async function clearKey() {
  try {
    await ElMessageBox.confirm('清除后会用平台的 Key 和地址，实例重启后生效。', '清除我的 Key', {
      type: 'warning',
    })
  } catch {
    return
  }
  savingKey.value = true
  try {
    await api.put('/me/credentials', { clear_key: true })
    keyInput.value = ''
    baseUrlInput.value = ''
    modelInput.value = ''
    ElMessage.success('已清除，将使用平台 Key')
    await loadCredentials()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    savingKey.value = false
  }
}

function restoreOfficial() {
  baseUrlInput.value = ''
  modelInput.value = ''
  saveKey()
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
            title="默认使用平台 Key 与地址；填写自己的 Key 后优先使用你的。"
            description="不一定要用 DeepSeek 官方接口：任何兼容 OpenAI /chat/completions 的网关都可以，把地址填到 /v1。保存后重启实例生效；Key 加密存储，接口只返回掩码。"
            style="margin-bottom: 18px"
          />
          <el-form label-width="110px" style="max-width: 640px">
            <el-form-item label="平台 Key">
              <el-tag :type="platformConfigured ? 'success' : 'info'">
                {{ platformConfigured ? '已配置' : '未配置' }}
              </el-tag>
              <span class="muted" style="margin-left: 10px; font-size: 12px">
                {{ platformBaseUrl || '官方地址' }}
              </span>
            </el-form-item>
            <el-form-item label="我的 Key">
              <el-tag :type="keyConfigured ? 'success' : 'info'">
                {{ keyConfigured ? `已配置 ${keyHint}` : '未配置（使用平台 Key）' }}
              </el-tag>
            </el-form-item>
            <el-form-item label="当前生效地址">
              <span class="mono" style="font-size: 12.5px">
                {{ effectiveBaseUrl || 'https://api.deepseek.com（官方，默认）' }}
              </span>
              <span v-if="effectiveModel" class="muted" style="margin-left: 10px; font-size: 12px">
                模型 {{ effectiveModel }}
              </span>
            </el-form-item>
            <el-form-item label="设置 Key">
              <el-input
                v-model="keyInput"
                placeholder="sk-...（留空则沿用已保存的 Key）"
                show-password
                type="password"
              />
            </el-form-item>
            <el-form-item label="Base URL">
              <el-input
                v-model="baseUrlInput"
                placeholder="https://api.deepseek.com/v1（留空 = 官方地址）"
              />
              <div class="muted" style="font-size: 12px; line-height: 1.6; margin-top: 4px">
                兼容 OpenAI 协议的网关（LiteLLM / vLLM / 中转）填到
                <span class="mono">/v1</span> 即可；只填地址时写入
                <span class="mono">llm-deepseek.baseURL</span>，沿用 DeepSeek 的模型目录。
              </div>
            </el-form-item>
            <el-form-item label="模型名">
              <el-input
                v-model="modelInput"
                placeholder="例如 qwen2.5-32b-instruct（留空 = 用 DeepSeek 默认模型）"
              />
              <div class="muted" style="font-size: 12px; line-height: 1.6; margin-top: 4px">
                网关上的模型 id 不是 DeepSeek 系列时必填。填了会生成一个
                <span class="mono">openai-completions</span> 提供方并设为新会话默认模型；
                <span class="mono">contextWindow</span> 默认 128k、<span class="mono"
                  >maxTokens</span
                >
                默认 8k，可在 DSH 设置页微调。
              </div>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingKey" @click="saveKey">保存</el-button>
              <el-button :disabled="savingKey" @click="restoreOfficial">恢复官方地址</el-button>
              <el-button
                v-if="keyConfigured"
                type="danger"
                plain
                :disabled="savingKey"
                @click="clearKey"
              >
                清除我的 Key
              </el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>
