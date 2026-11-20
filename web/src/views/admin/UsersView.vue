<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import { formatDateTime } from '@/utils/datetime'
import type { AdminQuota, AdminUser, Paginated, Plan } from '@/types'

const loading = ref(false)
const rows = ref<AdminUser[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const plans = ref<Plan[]>([])
const filters = reactive({ q: '', status: '' })

const createVisible = ref(false)
const createForm = reactive({
  username: '',
  password: '',
  display_name: '',
  email: '',
  role: 'user',
  plan_id: undefined as number | undefined,
  must_change_password: true,
})

const editVisible = ref(false)
const editForm = reactive({
  id: 0,
  username: '',
  display_name: '',
  email: '',
  role: 'user' as 'user' | 'admin',
  status: 'active' as 'active' | 'disabled' | 'pending',
  plan_id: undefined as number | undefined,
  reason: '',
})

const resetVisible = ref(false)
const resetForm = reactive({ id: 0, username: '', password: '' })

const quotaVisible = ref(false)
const quota = ref<AdminQuota | null>(null)
const grantForm = reactive({ id: 0, username: '', minutes: 600, reason: '' })

const saving = ref(false)

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: String(page.value),
      page_size: String(pageSize.value),
    })
    if (filters.q) params.set('q', filters.q)
    if (filters.status) params.set('status', filters.status)
    const data = await api.get<Paginated<AdminUser>>(`/admin/users?${params}`)
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadPlans() {
  try {
    const data = await api.get<{ items: Plan[] }>('/admin/plans')
    plans.value = data.items.filter((p) => p.enabled)
  } catch {
    plans.value = []
  }
}

function openCreate() {
  Object.assign(createForm, {
    username: '',
    password: '',
    display_name: '',
    email: '',
    role: 'user',
    plan_id: plans.value.find((p) => p.is_default)?.id,
    must_change_password: true,
  })
  createVisible.value = true
}

async function submitCreate() {
  saving.value = true
  try {
    await api.post('/admin/users', {
      ...createForm,
      email: createForm.email || null,
      plan_id: createForm.plan_id ?? null,
    })
    ElMessage.success('用户已创建')
    createVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

function openEdit(row: AdminUser) {
  Object.assign(editForm, {
    id: row.id,
    username: row.username,
    display_name: row.display_name,
    email: row.email ?? '',
    role: row.role,
    status: row.status,
    plan_id: row.plan?.id,
    reason: '',
  })
  editVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    await api.patch(`/admin/users/${editForm.id}`, {
      display_name: editForm.display_name,
      email: editForm.email || null,
      role: editForm.role,
      status: editForm.status,
      plan_id: editForm.plan_id ?? undefined,
      reason: editForm.reason || undefined,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

function openReset(row: AdminUser) {
  Object.assign(resetForm, {
    id: row.id,
    username: row.username,
    password: '',
  })
  resetVisible.value = true
}

async function submitReset() {
  if (resetForm.password.length < 8) {
    ElMessage.warning('临时密码至少 8 位')
    return
  }
  saving.value = true
  try {
    await api.post(`/admin/users/${resetForm.id}/reset-password`, {
      new_password: resetForm.password,
    })
    ElMessage.success('密码已重置，用户下次登录需修改')
    resetVisible.value = false
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function removeUser(row: AdminUser) {
  try {
    await ElMessageBox.confirm(
      `删除用户 ${row.username}？其会话会立即失效，数据保留 7 天。`,
      '删除用户',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await api.del(`/admin/users/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function openQuota(row: AdminUser) {
  try {
    const data = await api.get<AdminQuota>(`/admin/quotas/${row.id}`)
    quota.value = data
    Object.assign(grantForm, {
      id: row.id,
      username: row.username,
      minutes: 600,
      reason: '',
    })
    quotaVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function submitGrant() {
  if (!grantForm.reason.trim()) {
    ElMessage.warning('请填写调整原因')
    return
  }
  saving.value = true
  try {
    quota.value = await api.post<AdminQuota>(`/admin/quotas/${grantForm.id}/grant`, {
      minutes: grantForm.minutes,
      reason: grantForm.reason,
    })
    ElMessage.success('时长已调整')
    grantForm.reason = ''
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

function applyFilter() {
  page.value = 1
  load()
}

function statusTag(status: string): 'success' | 'danger' | 'info' {
  if (status === 'active') return 'success'
  if (status === 'disabled') return 'danger'
  return 'info'
}

const STATUS_TEXT: Record<string, string> = {
  active: '正常',
  disabled: '已禁用',
  pending: '待激活',
  deleted: '已删除',
}

function statusText(status: string): string {
  return STATUS_TEXT[status] ?? status
}

onMounted(async () => {
  await Promise.all([load(), loadPlans()])
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>用户管理</h1>
        <p>创建账号、分配套餐、禁用与重置密码</p>
      </div>
      <el-button type="primary" @click="openCreate">新建用户</el-button>
    </div>

    <div class="toolbar">
      <el-input
        v-model="filters.q"
        placeholder="搜索用户名 / 显示名 / 邮箱"
        style="width: 260px"
        clearable
        @keyup.enter="applyFilter"
      />
      <el-select v-model="filters.status" placeholder="状态" clearable style="width: 140px">
        <el-option label="正常" value="active" />
        <el-option label="禁用" value="disabled" />
        <el-option label="待激活" value="pending" />
      </el-select>
      <el-button @click="applyFilter">查询</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="用户" min-width="180">
          <template #default="{ row }">
            <div class="user-cell">
              <strong>{{ row.username }}</strong>
              <span class="muted">{{ row.display_name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.role === 'admin'" type="warning" size="small" effect="plain">
              管理员
            </el-tag>
            <span v-else class="muted">用户</span>
          </template>
        </el-table-column>
        <el-table-column label="套餐" width="120">
          <template #default="{ row }">{{ row.plan?.name ?? '未分配' }}</template>
        </el-table-column>
        <el-table-column label="剩余时长" width="130">
          <template #default="{ row }">
            {{ Math.round(row.quota.remaining_minutes / 60) }} h
            <span class="muted">/ {{ Math.round(row.quota.total_minutes / 60) }} h</span>
          </template>
        </el-table-column>
        <el-table-column label="最近登录" width="170">
          <template #default="{ row }">
            <span class="muted">{{
              row.last_login_at ? formatDateTime(row.last_login_at) : '从未'
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="268" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="openQuota(row)">配额</el-button>
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="primary" @click="openReset(row)">重置密码</el-button>
              <el-button link type="danger" @click="removeUser(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        style="margin-top: 16px; justify-content: flex-end"
        @current-change="load"
      />
    </div>

    <el-dialog v-model="createVisible" title="新建用户" width="480px">
      <el-form label-width="96px">
        <el-form-item label="用户名">
          <el-input v-model="createForm.username" placeholder="小写字母/数字/连字符" />
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input v-model="createForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="createForm.display_name" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="createForm.email" />
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="createForm.role">
            <el-radio value="user">用户</el-radio>
            <el-radio value="admin">管理员</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="套餐">
          <el-select v-model="createForm.plan_id" placeholder="选择套餐" style="width: 100%">
            <el-option v-for="p in plans" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑用户" width="480px">
      <el-form label-width="96px">
        <el-form-item label="用户名">
          <el-input :model-value="editForm.username" disabled />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="editForm.display_name" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="editForm.email" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role">
            <el-option label="用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status">
            <el-option label="正常" value="active" />
            <el-option label="禁用" value="disabled" />
            <el-option label="待激活" value="pending" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editForm.status === 'disabled'" label="禁用原因">
          <el-input v-model="editForm.reason" placeholder="可选，展示在审计中" />
        </el-form-item>
        <el-form-item label="套餐">
          <el-select v-model="editForm.plan_id" placeholder="选择套餐" style="width: 100%">
            <el-option v-for="p in plans" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetVisible" title="重置密码" width="420px">
      <p class="muted">为 {{ resetForm.username }} 设置临时密码，所有会话将下线。</p>
      <el-input
        v-model="resetForm.password"
        type="password"
        show-password
        placeholder="至少 8 位"
      />
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitReset">确认重置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="quotaVisible" title="配额与时长" width="520px">
      <template v-if="quota">
        <div class="quota-grid">
          <div>
            <span class="muted">套餐</span><strong>{{ quota.plan }}</strong>
          </div>
          <div>
            <span class="muted">已用</span><strong>{{ quota.used_minutes }} 分钟</strong>
          </div>
          <div>
            <span class="muted">剩余</span><strong>{{ quota.remaining_minutes }} 分钟</strong>
          </div>
          <div>
            <span class="muted">总额</span><strong>{{ quota.total_minutes }} 分钟</strong>
          </div>
        </div>
        <el-divider />
        <el-form label-width="90px">
          <el-form-item label="调整分钟">
            <el-input-number v-model="grantForm.minutes" :step="60" :min="-100000" :max="100000" />
            <span class="muted" style="margin-left: 8px">正数赠送，负数扣减</span>
          </el-form-item>
          <el-form-item label="原因">
            <el-input v-model="grantForm.reason" placeholder="必填，写入流水" />
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="quotaVisible = false">关闭</el-button>
        <el-button type="primary" :loading="saving" @click="submitGrant">提交调整</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.user-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.user-cell .muted {
  font-size: 12px;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  white-space: nowrap;
}

.quota-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.quota-grid div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px 12px;
}
</style>
