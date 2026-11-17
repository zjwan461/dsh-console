<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'

interface SnapshotRow {
  id: number
  status: 'creating' | 'ready' | 'restoring' | 'failed'
  size_bytes: number
  dsh_version: string
  note: string
  error: string | null
  created_at: string
}

const loading = ref(false)
const rows = ref<SnapshotRow[]>([])
const visible = ref(false)
const saving = ref(false)
const form = reactive({ note: '' })

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: SnapshotRow[] }>('/instances/me/snapshots')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.note = ''
  visible.value = true
}

async function submit() {
  saving.value = true
  try {
    await api.post('/instances/me/snapshots', { note: form.note })
    ElMessage.success('快照任务已提交')
    visible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function restore(row: SnapshotRow) {
  try {
    await ElMessageBox.confirm(
      '恢复会覆盖当前 data 与 workspace，实例会短暂停止。继续？',
      '恢复快照',
      { type: 'warning' },
    )
    await api.post(`/snapshots/${row.id}/restore`)
    ElMessage.success('已恢复')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function remove(row: SnapshotRow) {
  try {
    await ElMessageBox.confirm('删除该快照？', '删除', { type: 'warning' })
    await api.del(`/snapshots/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

function size(bytes: number) {
  if (!bytes) return '—'
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>快照</h1>
        <p>保存 `/data/dsh` 与 workspace；恢复会覆盖当前数据</p>
      </div>
      <el-button type="primary" @click="openCreate">创建快照</el-button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="快照包含数据与工作区，不包含系统层改动；系统层请用「我的镜像」。"
      style="margin-bottom: 16px"
    />

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="备注" min-width="200">
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ready' ? 'success' : row.status === 'failed' ? 'danger' : 'info'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="110">
          <template #default="{ row }">{{ size(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column prop="dsh_version" label="版本" width="140" />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ row.created_at.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.status !== 'ready'" @click="restore(row)">
              恢复
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="visible" title="创建快照" width="460px">
      <el-form label-width="80px">
        <el-form-item label="备注">
          <el-input v-model="form.note" maxlength="200" placeholder="例如：装好依赖后" />
        </el-form-item>
      </el-form>
      <p class="muted" style="font-size: 12px">
        创建时实例会短暂停止，完成后自动恢复运行。
      </p>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>
