<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import TerminalPanel from '@/components/TerminalPanel.vue'

interface AdminInstance {
  id: number
  status: string
  health: string
  dsh_version: string
  url: string
  spec: { cpu_cores: number; memory_mb: number; disk_gb: number }
  user: { id: number; username: string; display_name: string }
  container_id: string | null
  last_started_at: string | null
}

const loading = ref(false)
const rows = ref<AdminInstance[]>([])
const logsVisible = ref(false)
const logs = ref<string[]>([])
const terminalVisible = ref(false)
const terminalInstance = ref<AdminInstance | null>(null)
const terminalTitle = ref('')

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: AdminInstance[] }>('/admin/instances')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function action(row: AdminInstance, name: 'start' | 'stop' | 'restart') {
  try {
    await api.post(`/admin/instances/${row.id}/${name}`)
    ElMessage.success('操作已提交')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function remove(row: AdminInstance) {
  try {
    await ElMessageBox.confirm(`删除 ${row.user.username} 的实例？`, '删除实例', {
      type: 'warning',
    })
    await api.del(`/admin/instances/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function showLogs(row: AdminInstance) {
  try {
    const data = await api.get<{ lines: string[] }>(`/admin/instances/${row.id}/logs?tail=300`)
    logs.value = data.lines
    logsVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function openTerminal(row: AdminInstance) {
  terminalInstance.value = row
  terminalTitle.value = `${row.user.username} / ${row.dsh_version}`
  terminalVisible.value = true
}

function closeTerminal() {
  terminalVisible.value = false
  terminalInstance.value = null
}

function tag(status: string) {
  if (status === 'running') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'sleeping' || status === 'stopped') return 'info'
  return 'warning'
}

const STATUS_TEXT: Record<string, string> = {
  provisioning: '准备中',
  starting: '启动中',
  running: '运行中',
  sleeping: '已休眠',
  waking: '唤醒中',
  stopping: '停止中',
  stopped: '已停止',
  error: '异常',
  deleting: '删除中',
}

function statusText(status: string) {
  return STATUS_TEXT[status] ?? status
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>全站实例</h1>
        <p>强制启停、日志与管理员终端</p>
      </div>
      <el-button @click="load">刷新</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="用户" width="120">
          <template #default="{ row }">{{ row.user.username }}</template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="tag(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="dsh_version" label="版本" width="110" />
        <el-table-column label="规格" width="132">
          <template #default="{ row }">
            {{ row.spec.cpu_cores }}C / {{ Math.round(row.spec.memory_mb / 1024) }}G /
            {{ row.spec.disk_gb }}GB
          </template>
        </el-table-column>
        <el-table-column label="地址" min-width="200">
          <template #default="{ row }">
            <span class="mono url" :title="row.url">{{ row.url.replace('https://', '') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最近启动" width="140">
          <template #default="{ row }">
            <span class="muted">{{
              row.last_started_at?.replace('T', ' ').slice(0, 16) ?? '—'
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="action(row, 'start')">启动</el-button>
              <el-button link type="primary" @click="action(row, 'stop')">停止</el-button>
              <el-button link type="primary" @click="action(row, 'restart')">重启</el-button>
              <el-button link type="primary" @click="showLogs(row)">日志</el-button>
              <el-button link type="primary" @click="openTerminal(row)">终端</el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="logsVisible" title="容器日志（token 已脱敏）" width="760px">
      <pre class="logs">{{ logs.join('\n') }}</pre>
    </el-dialog>

    <el-dialog
      v-model="terminalVisible"
      :title="`终端 · ${terminalTitle}`"
      width="920px"
      top="6vh"
      destroy-on-close
      class="terminal-dialog"
      @closed="closeTerminal"
    >
      <TerminalPanel
        v-if="terminalInstance"
        :instance-id="terminalInstance.id"
        :title="terminalTitle"
        @close="closeTerminal"
      />
    </el-dialog>
  </div>
</template>

<style scoped>
.url {
  font-size: 12px;
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  white-space: nowrap;
}

.logs {
  background: #101820;
  color: #d7e2e8;
  border-radius: 8px;
  padding: 12px;
  max-height: 420px;
  overflow: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
}

:deep(.terminal-dialog .el-dialog__body) {
  padding-top: 4px;
}
</style>
