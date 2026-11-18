<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { Settings } from '@/types'

const loading = ref(false)
const saving = ref(false)
const keySaving = ref(false)
const platformConfigured = ref(false)
const platformHint = ref('')
const platformKey = ref('')
const platformBaseUrl = ref<string | null>(null)
const platformBaseUrlInput = ref('')
const platformModel = ref<string | null>(null)
const platformModelInput = ref('')
const form = reactive<Settings>({
  base_domain: 'dsh.example.com',
  console_name: '优墨云舱',
  default_plan_id: null,
  default_idle_minutes: 30,
  allow_user_version_switch: true,
  allow_user_self_key: true,
  snapshot_retention_days: 30,
  deleted_user_retention_days: 7,
  upload_max_mb: 1024,
  admin_terminal_enabled: true,
  maintenance_mode: false,
  maintenance_message: '',
})

async function load() {
  loading.value = true
  try {
    const data = await api.get<Settings>('/admin/settings')
    Object.assign(form, data)
    const cred = await api.get<{
      configured: boolean
      hint: string
      base_url: string | null
      model: string | null
    }>('/admin/credentials/platform')
    platformConfigured.value = cred.configured
    platformHint.value = cred.hint
    platformBaseUrl.value = cred.base_url
    platformBaseUrlInput.value = cred.base_url ?? ''
    platformModel.value = cred.model
    platformModelInput.value = cred.model ?? ''
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const updated = await api.put<Settings>('/admin/settings', {
      base_domain: form.base_domain,
      console_name: form.console_name,
      default_idle_minutes: form.default_idle_minutes,
      allow_user_version_switch: form.allow_user_version_switch,
      allow_user_self_key: form.allow_user_self_key,
      snapshot_retention_days: form.snapshot_retention_days,
      deleted_user_retention_days: form.deleted_user_retention_days,
      upload_max_mb: form.upload_max_mb,
      admin_terminal_enabled: form.admin_terminal_enabled,
      maintenance_mode: form.maintenance_mode,
      maintenance_message: form.maintenance_message,
    })
    Object.assign(form, updated)
    ElMessage.success('设置已保存')
    if (updated.base_domain !== form.base_domain) {
      ElMessage.warning('域名已变更，需要重载 Caddy 并重新签发证书')
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function savePlatformKey() {
  keySaving.value = true
  try {
    const result = await api.put<{
      configured: boolean
      hint: string
      base_url: string | null
      model: string | null
    }>('/admin/credentials/platform', {
      api_key: platformKey.value,
      base_url: platformBaseUrlInput.value,
      model: platformModelInput.value,
    })
    platformConfigured.value = result.configured
    platformHint.value = result.hint
    platformBaseUrl.value = result.base_url
    platformBaseUrlInput.value = result.base_url ?? ''
    platformModel.value = result.model
    platformModelInput.value = result.model ?? ''
    platformKey.value = ''
    ElMessage.success(result.configured ? '平台 Key 已保存' : '平台 Key 已清除')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    keySaving.value = false
  }
}

async function clearPlatformKey() {
  try {
    await ElMessageBox.confirm(
      '清除后，未自带 Key 的用户将无法调用模型，直到重新配置。',
      '清除平台 Key',
      { type: 'warning' },
    )
  } catch {
    return
  }
  keySaving.value = true
  try {
    await api.put('/admin/credentials/platform', { clear_key: true })
    platformConfigured.value = false
    platformHint.value = ''
    platformBaseUrl.value = null
    platformBaseUrlInput.value = ''
    platformModel.value = null
    platformModelInput.value = ''
    platformKey.value = ''
    ElMessage.success('平台 Key 已清除')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    keySaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1>系统设置</h1>
        <p>基础域名、默认值与平台模型 Key</p>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top: 0">基础</h3>
      <el-form label-width="170px" style="max-width: 720px">
        <el-form-item label="基础域名">
          <el-input v-model="form.base_domain" placeholder="dsh.example.com" />
          <div class="hint">
            控制台为 console.&lt;域名&gt;，用户实例为 &lt;用户名&gt;.&lt;域名&gt;
          </div>
        </el-form-item>
        <el-form-item label="控制台名称">
          <el-input v-model="form.console_name" />
        </el-form-item>
        <el-form-item label="默认休眠（分钟）">
          <el-input-number v-model="form.default_idle_minutes" :min="0" :max="1440" :step="5" />
          <span class="hint" style="margin-left: 10px">0 表示不自动休眠</span>
        </el-form-item>
        <el-form-item label="允许自选版本">
          <el-switch v-model="form.allow_user_version_switch" />
        </el-form-item>
        <el-form-item label="允许自带 Key">
          <el-switch v-model="form.allow_user_self_key" />
        </el-form-item>
        <el-form-item label="快照保留（天）">
          <el-input-number v-model="form.snapshot_retention_days" :min="1" :max="3650" />
        </el-form-item>
        <el-form-item label="删除用户保留（天）">
          <el-input-number v-model="form.deleted_user_retention_days" :min="1" :max="3650" />
        </el-form-item>
        <el-form-item label="上传上限（MB）">
          <el-input-number v-model="form.upload_max_mb" :min="1" :max="10240" />
        </el-form-item>
        <el-form-item label="管理员终端">
          <el-switch v-model="form.admin_terminal_enabled" />
        </el-form-item>
        <el-form-item label="维护模式">
          <el-switch v-model="form.maintenance_mode" />
        </el-form-item>
        <el-form-item v-if="form.maintenance_mode" label="维护提示">
          <el-input v-model="form.maintenance_message" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="card" style="margin-top: 16px">
      <h3 style="margin-top: 0">平台模型 Key</h3>
      <p class="muted" style="font-size: 13px">
        用于未自带 Key 的实例。风险提示：Key 会以环境变量注入容器，实例内可被读取；
        生产环境建议只对可信用户开放，并尽快启用 1.5 期的 Key 网关。
      </p>
      <el-form label-width="170px" style="max-width: 760px">
        <el-form-item label="当前状态">
          <el-tag :type="platformConfigured ? 'success' : 'info'">
            {{ platformConfigured ? `已配置 ${platformHint}` : '未配置' }}
          </el-tag>
          <span class="muted" style="margin-left: 10px; font-size: 12px">
            {{ platformBaseUrl || '官方地址' }}
            <template v-if="platformModel"> · 模型 {{ platformModel }}</template>
          </span>
        </el-form-item>
        <el-form-item label="设置 Key">
          <el-input
            v-model="platformKey"
            type="password"
            show-password
            placeholder="sk-...（留空则沿用已保存的 Key）"
          />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input
            v-model="platformBaseUrlInput"
            placeholder="https://api.deepseek.com/v1（留空 = 官方地址）"
          />
          <div class="muted" style="font-size: 12px; line-height: 1.6; margin-top: 4px">
            用户没有自填地址时使用这个端点。兼容 OpenAI 协议的网关（LiteLLM / vLLM / 中转）填到
            <span class="mono">/v1</span>；会写入各实例的 <span class="mono">settings.yaml</span>。
          </div>
        </el-form-item>
        <el-form-item label="模型名">
          <el-input
            v-model="platformModelInput"
            placeholder="例如 qwen2.5-32b-instruct（留空 = DeepSeek 默认模型）"
          />
          <div class="muted" style="font-size: 12px; line-height: 1.6; margin-top: 4px">
            非 DeepSeek 模型 id 时必填。填了会为实例生成
            <span class="mono">openai-completions</span> 提供方并设为默认模型。
          </div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="keySaving" @click="savePlatformKey"> 保存 </el-button>
          <el-button
            v-if="platformConfigured"
            type="danger"
            plain
            :disabled="keySaving"
            @click="clearPlatformKey"
          >
            清除平台 Key
          </el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.hint {
  color: var(--dsh-muted);
  font-size: 12px;
  margin-top: 4px;
}
</style>
