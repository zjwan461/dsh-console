<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'

interface ImageRow {
  id: number
  version: string
  image_ref: string
  digest: string | null
  size_bytes: number
  arch: string
  status: 'importing' | 'ready' | 'disabled' | 'failed'
  is_default: boolean
  changelog: string | null
  error: string | null
  created_at: string
}

const loading = ref(false)
const rows = ref<ImageRow[]>([])
const visible = ref(false)
const saving = ref(false)
const form = reactive({
  image_ref: '',
  version: '',
  changelog: '',
  is_default: false,
})

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: ImageRow[] }>('/admin/images')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openRegister() {
  Object.assign(form, { image_ref: '', version: '', changelog: '', is_default: rows.value.length === 0 })
  visible.value = true
}

async function submit() {
  saving.value = true
  try {
    await api.post('/admin/images', form)
    ElMessage.success('镜像已登记')
    visible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function setDefault(row: ImageRow) {
  try {
    await api.patch(`/admin/images/${row.id}`, { is_default: true })
    ElMessage.success('已设为默认版本')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function toggle(row: ImageRow) {
  try {
    await api.patch(`/admin/images/${row.id}`, {
      status: row.status === 'ready' ? 'disabled' : 'ready',
    })
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function remove(row: ImageRow) {
  try {
    await ElMessageBox.confirm(`删除版本 ${row.version}？`, '删除镜像', { type: 'warning' })
    await api.del(`/admin/images/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

function size(bytes: number) {
  if (!bytes) return '—'
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function tag(status: string) {
  if (status === 'ready') return 'success'
  if (status === 'disabled') return 'info'
  if (status === 'failed') return 'danger'
  return 'warning'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>DSH 版本</h1>
        <p>只登记本机已有的镜像；构建与 login-gate 移除由 dsh-docker 的 CI 负责</p>
      </div>
      <el-button type="primary" @click="openRegister">登记镜像</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="版本" width="150">
          <template #default="{ row }">
            <strong>{{ row.version }}</strong>
            <el-tag v-if="row.is_default" type="success" size="small" style="margin-left: 6px">
              默认
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="镜像" min-width="260">
          <template #default="{ row }">
            <span class="mono" style="font-size: 12px">{{ row.image_ref }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="tag(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ size(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column label="架构" width="90" prop="arch" />
        <el-table-column label="登记时间" width="160">
          <template #default="{ row }">
            <span class="muted">{{ row.created_at.replace('T', ' ').slice(0, 16) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.is_default" @click="setDefault(row)">
              设为默认
            </el-button>
            <el-button link type="primary" @click="toggle(row)">
              {{ row.status === 'ready' ? '禁用' : '启用' }}
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="visible" title="登记已有镜像" width="560px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="镜像必须已经 docker pull 到本机"
        description="控制台不构建、不拉取，只登记引用并校验存在性。"
        style="margin-bottom: 16px"
      />
      <el-form label-width="96px">
        <el-form-item label="镜像引用">
          <el-input v-model="form.image_ref" placeholder="ghcr.io/you/dsh:0.2.1-rc.5" />
        </el-form-item>
        <el-form-item label="版本号">
          <el-input v-model="form.version" placeholder="0.2.1-rc.5" />
        </el-form-item>
        <el-form-item label="变更说明">
          <el-input v-model="form.changelog" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="form.is_default" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">登记</el-button>
      </template>
    </el-dialog>
  </div>
</template>
