<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { InstanceOptions } from '@/types'

interface UserImageRow {
  id: number
  name: string
  image_ref: string
  size_bytes: number
  base_version: string
  status: 'committing' | 'ready' | 'failed'
  note: string
  error: string | null
  created_at: string
}

const loading = ref(false)
const rows = ref<UserImageRow[]>([])
const options = ref<InstanceOptions | null>(null)
const visible = ref(false)
const saving = ref(false)
const version = ref<number | null>(null)
const form = reactive({ name: '', note: '' })

async function load() {
  loading.value = true
  try {
    const [images, opt] = await Promise.all([
      api.get<{ items: UserImageRow[] }>('/me/images'),
      api.get<InstanceOptions>('/instances/me/options'),
    ])
    rows.value = images.items
    options.value = opt
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCommit() {
  form.name = ''
  form.note = ''
  visible.value = true
}

// `docker commit` takes minutes on a multi-GB container; the API only reserves the
// row, so poll it instead of leaving the dialog spinning with no feedback.
let pollTimer: ReturnType<typeof setTimeout> | null = null

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

async function pollCommit() {
  stopPolling()
  if (!rows.value.some((row) => row.status === 'committing')) return
  pollTimer = setTimeout(async () => {
    await load()
    const failed = rows.value.find((row) => row.status === 'failed')
    if (failed) {
      ElMessage.error(`保存失败：${failed.error ?? failed.name}`)
      stopPolling()
      return
    }
    if (!rows.value.some((row) => row.status === 'committing')) {
      ElMessage.success('镜像已保存')
      stopPolling()
      return
    }
    await pollCommit()
  }, 4000)
}

async function submit() {
  saving.value = true
  try {
    await api.post('/instances/me/images', form)
    ElMessage.success('已开始保存镜像，可关闭本页，进度见列表')
    visible.value = false
    await load()
    await pollCommit()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function rebuild(row: UserImageRow) {
  try {
    await ElMessageBox.confirm('用该镜像重建实例（保留数据），实例会重启。继续？', '从镜像重建', {
      type: 'warning',
    })
    await api.post(`/me/images/${row.id}/rebuild`)
    ElMessage.success('正在重建')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function remove(row: UserImageRow) {
  try {
    await ElMessageBox.confirm('删除该镜像？', '删除', { type: 'warning' })
    await api.del(`/me/images/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function switchVersion() {
  if (!version.value) return
  try {
    await ElMessageBox.confirm('切换基础版本会重建容器（保留数据）。继续？', '切换版本', {
      type: 'warning',
    })
    await api.post('/instances/me/version', { image_id: version.value })
    ElMessage.success('正在切换')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

function size(bytes: number) {
  return bytes ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : '—'
}

onMounted(async () => {
  await load()
  // A commit started before a refresh keeps running server-side.
  if (rows.value.some((row) => row.status === 'committing')) await pollCommit()
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>我的镜像</h1>
        <p>对实例系统层做 commit；数据请用快照</p>
      </div>
      <el-button type="primary" @click="openCommit">保存为镜像</el-button>
    </div>

    <div class="card" style="margin-bottom: 16px">
      <div class="switch-row">
        <div>
          <strong>基础版本</strong>
          <div class="muted" style="font-size: 12px">
            跨版本切换前会自动保留数据（建议先建快照）
          </div>
        </div>
        <div style="display: flex; gap: 8px">
          <el-select v-model="version" placeholder="选择版本" style="width: 220px">
            <el-option
              v-for="item in options?.images ?? []"
              :key="item.id"
              :label="item.version"
              :value="item.id"
            />
          </el-select>
          <el-button :disabled="!version" @click="switchVersion">切换</el-button>
        </div>
      </div>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column prop="name" label="名称" width="160" />
        <el-table-column label="引用" min-width="240">
          <template #default="{ row }"
            ><span class="mono" style="font-size: 12px">{{ row.image_ref }}</span></template
          >
        </el-table-column>
        <el-table-column prop="base_version" label="基础版本" width="140" />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ size(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.status === 'failed' && row.error"
              :content="row.error"
              placement="top"
            >
              <el-tag type="danger">失败</el-tag>
            </el-tooltip>
            <el-tag
              v-else-if="row.status === 'committing'"
              type="warning"
              effect="light"
              class="pulse"
            >
              保存中
            </el-tag>
            <el-tag v-else type="success">可用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.status !== 'ready'" @click="rebuild(row)">
              重建实例
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="visible" title="保存为镜像" width="460px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="镜像只包含系统层（apt/pip/npm 安装等），不含 /data/dsh 与 workspace。"
        style="margin-bottom: 14px"
      />
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="pandas-env" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" maxlength="200" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 「保存中」要看起来还在动，否则用户以为卡住了 */
.pulse {
  animation: pulse 1.6s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pulse {
    animation: none;
  }
}

.switch-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
</style>
