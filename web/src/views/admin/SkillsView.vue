<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage, getCsrfToken } from '@/api/client'

interface Grant {
  scope: 'all' | 'plan' | 'user'
  target_id: number
}

interface SkillRow {
  id: number
  name: string
  display_name: string
  description: string
  when_to_use: string
  model_invocable: boolean
  user_invocable: boolean
  status: 'draft' | 'published' | 'disabled'
  source: string
  revision: number | null
  size_bytes: number
  grants: Grant[]
  body?: string
  files?: Array<{ path: string; size: number }>
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
const rows = ref<SkillRow[]>([])
const plans = ref<PlanRow[]>([])
const users = ref<UserRow[]>([])
const q = ref('')

const editorVisible = ref(false)
const saving = ref(false)
const editing = ref<SkillRow | null>(null)
const form = reactive({
  name: '',
  display_name: '',
  description: '',
  when_to_use: '',
  model_invocable: true,
  user_invocable: true,
  body: '',
})

const grantsVisible = ref(false)
const grantSkill = ref<SkillRow | null>(null)
const grantMode = ref<'all' | 'plan' | 'user'>('all')
const grantPlans = ref<number[]>([])
const grantUsers = ref<number[]>([])

const revisionsVisible = ref(false)
const revisions = ref<Array<{ id: number; rev: number; changelog: string; created_at: string }>>([])

const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const statusMeta: Record<string, { text: string; type: 'success' | 'info' | 'warning' }> = {
  draft: { text: '草稿', type: 'warning' },
  published: { text: '已发布', type: 'success' },
  disabled: { text: '已停用', type: 'info' },
}

const publishedCount = computed(
  () => rows.value.filter((item) => item.status === 'published').length,
)

const grantText = (skill: SkillRow) => {
  const grants = skill.grants ?? []
  if (grants.some((item) => item.scope === 'all')) return '全员'
  const planCount = grants.filter((item) => item.scope === 'plan').length
  const userCount = grants.filter((item) => item.scope === 'user').length
  const parts = []
  if (planCount) parts.push(`${planCount} 个套餐`)
  if (userCount) parts.push(`${userCount} 位用户`)
  return parts.length ? parts.join(' / ') : '未开放'
}

async function load() {
  loading.value = true
  try {
    const query = q.value ? `?q=${encodeURIComponent(q.value)}` : ''
    const data = await api.get<{ items: SkillRow[] }>(`/admin/skills${query}`)
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
    // Directory data is only needed by the grant dialog.
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    name: '',
    display_name: '',
    description: '',
    when_to_use: '',
    model_invocable: true,
    user_invocable: true,
    body: '',
  })
  editorVisible.value = true
}

async function openEdit(skill: SkillRow) {
  try {
    const detail = await api.get<SkillRow>(`/admin/skills/${skill.id}`)
    editing.value = detail
    Object.assign(form, {
      name: detail.name,
      display_name: detail.display_name,
      description: detail.description,
      when_to_use: detail.when_to_use,
      model_invocable: detail.model_invocable,
      user_invocable: detail.user_invocable,
      body: detail.body ?? '',
    })
    editorVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function save() {
  if (!form.name.trim() || !form.description.trim()) {
    ElMessage.warning('名称和描述必填')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await api.put(`/admin/skills/${editing.value.id}`, { ...form, changelog: '编辑' })
      ElMessage.success(
        editing.value.status === 'published'
          ? '已保存并热更新到实例'
          : '已保存草稿，发布后生效',
      )
    } else {
      await api.post('/admin/skills', form)
      ElMessage.success('已创建草稿')
    }
    editorVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
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
    const response = await fetch('/api/v1/admin/skills/import', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': getCsrfToken() },
      body,
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.error?.message ?? `导入失败（${response.status}）`)
    }
    ElMessage.success(`已导入 ${payload.data.name}`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function publish(skill: SkillRow) {
  try {
    const result = await api.post<{ synced: number }>(`/admin/skills/${skill.id}/publish`)
    ElMessage.success(`已发布，同步 ${result.synced} 个实例（热重载）`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function disable(skill: SkillRow) {
  try {
    await ElMessageBox.confirm(
      `停用 ${skill.name}？会从所有实例移除，但保留历史版本。`,
      '停用技能',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await api.del(`/admin/skills/${skill.id}`)
    ElMessage.success('已停用')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function openGrants(skill: SkillRow) {
  grantSkill.value = skill
  const grants = skill.grants ?? []
  if (grants.some((item) => item.scope === 'all')) grantMode.value = 'all'
  else if (grants.some((item) => item.scope === 'plan')) grantMode.value = 'plan'
  else if (grants.some((item) => item.scope === 'user')) grantMode.value = 'user'
  else grantMode.value = 'all'
  grantPlans.value = grants.filter((item) => item.scope === 'plan').map((item) => item.target_id)
  grantUsers.value = grants.filter((item) => item.scope === 'user').map((item) => item.target_id)
  grantsVisible.value = true
}

async function saveGrants() {
  if (!grantSkill.value) return
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
    const result = await api.put<{ synced: number }>(
      `/admin/skills/${grantSkill.value.id}/grants`,
      {
        grants,
      },
    )
    ElMessage.success(`开放范围已更新，同步 ${result.synced} 个实例`)
    grantsVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function openRevisions(skill: SkillRow) {
  try {
    const data = await api.get<{ items: typeof revisions.value }>(
      `/admin/skills/${skill.id}/revisions`,
    )
    revisions.value = data.items
    grantSkill.value = skill
    revisionsVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function rollback(revision: { id: number; rev: number }) {
  if (!grantSkill.value) return
  try {
    await ElMessageBox.confirm(`回滚到 r${revision.rev} 并发布？`, '回滚版本', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.post(`/admin/skills/${grantSkill.value.id}/rollback`, { revision_id: revision.id })
    ElMessage.success('已回滚并发布')
    revisionsVisible.value = false
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
        <h1>技能仓库</h1>
        <p>平台技能会以追加方式投放给用户（镜像内置技能不受影响）；发布后热重载，无需重启实例。</p>
      </div>
      <div class="actions">
        <el-button :loading="uploading" @click="pickFile">导入 zip / tar / md</el-button>
        <el-button type="primary" @click="openCreate">新建技能</el-button>
        <input
          ref="fileInput"
          type="file"
          accept=".zip,.tar,.tar.gz,.tgz,.md"
          style="display: none"
          @change="onFile"
        />
      </div>
    </div>

    <div class="toolbar">
      <el-input
        v-model="q"
        placeholder="搜索名称或描述"
        clearable
        style="max-width: 280px"
        @keyup.enter="load"
        @clear="load"
      />
      <el-button @click="load">查询</el-button>
      <span class="muted" style="font-size: 12px">
        共 {{ rows.length }} 个，已发布 {{ publishedCount }} 个
      </span>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="技能" min-width="220">
          <template #default="{ row }">
            <div class="skill-cell">
              <strong>{{ row.display_name || row.name }}</strong>
              <span class="mono muted">{{ row.name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="190" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusMeta[row.status].type" size="small">
              {{ statusMeta[row.status].text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="开放范围" width="120">
          <template #default="{ row }">{{ grantText(row) }}</template>
        </el-table-column>
        <el-table-column label="版本" width="70">
          <template #default="{ row }">r{{ row.revision ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="145">
          <template #default="{ row }">
            <span class="muted">{{ row.updated_at.replace('T', ' ').slice(0, 16) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="primary" @click="openGrants(row)">开放范围</el-button>
              <el-button
                v-if="row.status !== 'published'"
                link
                type="primary"
                @click="publish(row)"
              >
                发布
              </el-button>
              <el-button v-if="row.status !== 'disabled'" link type="danger" @click="disable(row)">
                停用
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog
      v-model="editorVisible"
      :title="editing ? `编辑技能 · ${editing.name}` : '新建技能'"
      width="820px"
      top="5vh"
    >
      <el-form label-width="96px">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="技能名">
              <el-input v-model="form.name" placeholder="小写字母/数字/连字符" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="显示名">
              <el-input v-model="form.display_name" placeholder="可选" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="一行说明，会出现在技能目录里" />
        </el-form-item>
        <el-form-item label="适用场景">
          <el-input v-model="form.when_to_use" placeholder="给模型的使用提示（可选）" />
        </el-form-item>
        <el-form-item label="调用方式">
          <el-checkbox v-model="form.model_invocable">模型可调用</el-checkbox>
          <el-checkbox v-model="form.user_invocable">用户可调用</el-checkbox>
        </el-form-item>
        <el-form-item label="正文">
          <el-input
            v-model="form.body"
            type="textarea"
            :rows="16"
            class="md-editor"
            placeholder="# 标题&#10;&#10;Markdown 指令、步骤、示例……"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button v-if="editing" @click="openRevisions(editing)">版本历史</el-button>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">
          {{ editing ? '保存为新版本' : '创建草稿' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="grantsVisible" title="开放范围" width="560px">
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
      <p class="muted" style="font-size: 12px">
        用户对开放给自己的技能默认启用；被收回时不删除其偏好。
      </p>
      <template #footer>
        <el-button @click="grantsVisible = false">取消</el-button>
        <el-button type="primary" @click="saveGrants">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="revisionsVisible" title="版本历史" width="620px">
      <el-table :data="revisions">
        <el-table-column label="版本" width="80">
          <template #default="{ row }">r{{ row.rev }}</template>
        </el-table-column>
        <el-table-column prop="changelog" label="说明" />
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            {{ row.created_at.replace('T', ' ').slice(0, 16) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="primary" @click="rollback(row)">回滚</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.skill-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.skill-cell .mono {
  font-size: 12px;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  white-space: nowrap;
}

.md-editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
}
</style>
