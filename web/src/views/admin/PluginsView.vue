<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection } from '@element-plus/icons-vue'
import { api, errorMessage, getCsrfToken } from '@/api/client'

interface Grant {
  scope: 'all' | 'plan' | 'user'
  target_id: number
}

interface PluginRow {
  id: number
  package_name: string
  display_name: string
  version: string
  description: string
  dsh_versions: string
  status: 'enabled' | 'disabled'
  size_bytes: number
  grants: Grant[]
  updated_at: string
}

interface PlanRow {
  id: number
  name: string
}

interface UserRow {
  id: number
  username: string
  display_name: string
}

const loading = ref(false)
const rows = ref<PluginRow[]>([])
const plans = ref<PlanRow[]>([])
const users = ref<UserRow[]>([])
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const grantsVisible = ref(false)
const grantPlugin = ref<PluginRow | null>(null)
const grantMode = ref<'all' | 'plan' | 'user'>('all')
const grantPlans = ref<number[]>([])
const grantUsers = ref<number[]>([])

const grantText = (plugin: PluginRow) => {
  const grants = plugin.grants ?? []
  if (grants.some((item) => item.scope === 'all')) return '全员'
  const planCount = grants.filter((item) => item.scope === 'plan').length
  const userCount = grants.filter((item) => item.scope === 'user').length
  const parts = []
  if (planCount) parts.push(`${planCount} 个套餐`)
  if (userCount) parts.push(`${userCount} 位用户`)
  return parts.length ? parts.join(' / ') : '未分配'
}

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: PluginRow[] }>('/admin/plugins')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadDirectory() {
  try {
    const [planData, userData] = await Promise.all([
      api.get<{ items: PlanRow[] }>('/admin/plans'),
      api.get<{ items: UserRow[] }>('/admin/users?page=1&page_size=100'),
    ])
    plans.value = planData.items
    users.value = userData.items
  } catch {
    // Only used by the assignment dialog.
  }
}

function pickFile() {
  fileInput.value?.click()
}

async function onFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  const body = new FormData()
  body.append('file', file)
  try {
    const response = await fetch('/api/v1/admin/plugins/import', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': getCsrfToken() },
      body,
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.error?.message ?? `导入失败（${response.status}）`)
    }
    ElMessage.success(`已导入 ${payload.data.package_name}@${payload.data.version}`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function toggle(plugin: PluginRow) {
  const action = plugin.status === 'enabled' ? 'disable' : 'enable'
  const label = action === 'enable' ? '启用' : '停用'
  try {
    if (action === 'disable') {
      await ElMessageBox.confirm(
        `停用 ${plugin.package_name}？会从实例移除并清理 patch 块，实例需要重启。`,
        '停用插件',
        { type: 'warning' },
      )
    }
    const result = await api.post<{ sync: { changed: number } }>(
      `/admin/plugins/${plugin.id}/${action}`,
    )
    ElMessage.success(`${label}成功，${result.sync.changed} 个实例已标记需重启`)
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

function openGrants(plugin: PluginRow) {
  grantPlugin.value = plugin
  const grants = plugin.grants ?? []
  if (grants.some((item) => item.scope === 'all')) grantMode.value = 'all'
  else if (grants.some((item) => item.scope === 'plan')) grantMode.value = 'plan'
  else if (grants.some((item) => item.scope === 'user')) grantMode.value = 'user'
  else grantMode.value = 'all'
  grantPlans.value = grants.filter((item) => item.scope === 'plan').map((item) => item.target_id)
  grantUsers.value = grants.filter((item) => item.scope === 'user').map((item) => item.target_id)
  grantsVisible.value = true
}

async function saveGrants() {
  if (!grantPlugin.value) return
  let grants: Grant[] = []
  if (grantMode.value === 'all') grants = [{ scope: 'all', target_id: 0 }]
  if (grantMode.value === 'plan') {
    grants = grantPlans.value.map((id) => ({ scope: 'plan' as const, target_id: id }))
  }
  if (grantMode.value === 'user') {
    grants = grantUsers.value.map((id) => ({ scope: 'user' as const, target_id: id }))
  }
  if (grantMode.value !== 'all' && grants.length === 0) {
    ElMessage.warning('请选择套餐或用户')
    return
  }
  try {
    const result = await api.put<{ sync: { changed: number } }>(
      `/admin/plugins/${grantPlugin.value.id}/grants`,
      { grants },
    )
    ElMessage.success(`分配已更新，${result.sync.changed} 个实例需重启`)
    grantsVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

const sizeText = (bytes: number) => (bytes ? `${(bytes / 1024).toFixed(1)} KB` : '—')

onMounted(() => {
  load()
  loadDirectory()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>插件目录</h1>
        <p>
          插件以只读挂载追加到 <span class="mono">/opt/dsh-plugins</span>，镜像内置插件保持不变；
          用户不能自选，变更后实例需重启。
        </p>
      </div>
      <div class="actions">
        <el-button :loading="uploading" @click="pickFile">导入插件包</el-button>
        <input
          ref="fileInput"
          type="file"
          accept=".zip,.tar,.tar.gz,.tgz"
          style="display: none"
          @change="onFile"
        />
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="插件是跑在 DSH 服务进程里的 Node 代码，不是 agent 沙箱内容"
      description="因此只允许管理员导入并按套餐/用户分配；用户端只读展示。启用或停用都需要重启实例才能生效。"
      style="margin-bottom: 16px"
    />

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="插件" min-width="240">
          <template #default="{ row }">
            <div class="plugin-cell">
              <strong>{{ row.display_name || row.package_name }}</strong>
              <span class="mono muted">{{ row.package_name }}@{{ row.version }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
        <el-table-column label="DSH 兼容" width="130">
          <template #default="{ row }">
            <span class="muted mono" style="font-size: 11.5px">
              {{ row.dsh_versions || '未声明' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="row.status === 'enabled' ? 'success' : 'info'" size="small">
              {{ row.status === 'enabled' ? '已启用' : '未启用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分配" width="110">
          <template #default="{ row }">{{ grantText(row) }}</template>
        </el-table-column>
        <el-table-column label="大小" width="80">
          <template #default="{ row }">{{ sizeText(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="openGrants(row)">分配</el-button>
              <el-button
                link
                :type="row.status === 'enabled' ? 'danger' : 'primary'"
                @click="toggle(row)"
              >
                {{ row.status === 'enabled' ? '停用' : '启用' }}
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!loading && rows.length === 0" class="blank">
        <div class="blank-icon">
          <el-icon><Connection /></el-icon>
        </div>
        <strong>还没有插件</strong>
        导入一个 DSH bundle（package.json + 入口 + 可选 cordis.patch.yml）后按套餐分配
      </div>
    </div>

    <el-dialog v-model="grantsVisible" title="插件分配" width="560px">
      <el-form label-width="92px">
        <el-form-item label="范围">
          <el-radio-group v-model="grantMode">
            <el-radio value="all">全员</el-radio>
            <el-radio value="plan">按套餐</el-radio>
            <el-radio value="user">指定用户</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="grantMode === 'plan'" label="套餐">
          <el-select v-model="grantPlans" multiple placeholder="选择套餐" style="width: 100%">
            <el-option v-for="plan in plans" :key="plan.id" :label="plan.name" :value="plan.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="grantMode === 'user'" label="用户">
          <el-select
            v-model="grantUsers"
            multiple
            filterable
            placeholder="选择用户"
            style="width: 100%"
          >
            <el-option
              v-for="user in users"
              :key="user.id"
              :label="`${user.display_name || user.username} (@${user.username})`"
              :value="user.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="grantsVisible = false">取消</el-button>
        <el-button type="primary" @click="saveGrants">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.plugin-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.plugin-cell .mono {
  font-size: 12px;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  white-space: nowrap;
}
</style>
