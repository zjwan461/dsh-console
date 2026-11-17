<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'

interface AdminTicket {
  id: number
  subject: string
  status: string
  priority: string
  category: string
  created_at: string
  user: { id: number; username: string } | null
  message_count: number
}

interface Message {
  id: number
  author: string
  body: string
  created_at: string
}

const loading = ref(false)
const status = ref('')
const rows = ref<AdminTicket[]>([])
const detailVisible = ref(false)
const detail = ref<(AdminTicket & { messages: Message[] }) | null>(null)
const reply = ref('')

async function load() {
  loading.value = true
  try {
    const query = status.value ? `?status=${status.value}` : ''
    const data = await api.get<{ items: AdminTicket[] }>(`/admin/tickets${query}`)
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function open(row: AdminTicket) {
  try {
    detail.value = await api.get(`/admin/tickets/${row.id}`)
    reply.value = ''
    detailVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function send() {
  if (!detail.value || !reply.value.trim()) return
  try {
    await api.post(`/admin/tickets/${detail.value.id}/messages`, { body: reply.value })
    detail.value = await api.get(`/admin/tickets/${detail.value.id}`)
    reply.value = ''
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function setStatus(value: string) {
  if (!detail.value) return
  try {
    await api.patch(`/admin/tickets/${detail.value.id}`, { status: value })
    detail.value.status = value
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

const STATUS_TEXT: Record<string, string> = {
  open: '待处理',
  processing: '处理中',
  resolved: '已解决',
  closed: '已关闭',
}

const PRIORITY_TEXT: Record<string, string> = {
  low: '低',
  normal: '普通',
  high: '高',
}

function statusText(status: string) {
  return STATUS_TEXT[status] ?? status
}

function priorityText(priority: string) {
  return PRIORITY_TEXT[priority] ?? priority
}

function statusTag(status: string): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'open') return 'warning'
  if (status === 'processing') return 'info'
  if (status === 'resolved') return 'success'
  return 'info'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>工单队列</h1>
        <p>回复、指派与关闭</p>
      </div>
      <el-select
        v-model="status"
        placeholder="全部状态"
        clearable
        style="width: 150px"
        @change="load"
      >
        <el-option label="待处理" value="open" />
        <el-option label="处理中" value="processing" />
        <el-option label="已解决" value="resolved" />
        <el-option label="已关闭" value="closed" />
      </el-select>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe @row-click="open">
        <el-table-column label="用户" width="130">
          <template #default="{ row }">{{ row.user?.username ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="subject" label="主题" min-width="240" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="100">
          <template #default="{ row }">
            <span :class="['priority', row.priority]">{{ priorityText(row.priority) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{
            row.created_at.replace('T', ' ').slice(0, 16)
          }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="detailVisible" :title="detail?.subject" width="680px">
      <template v-if="detail">
        <div class="bar">
          <el-tag>{{ detail.status }}</el-tag>
          <div>
            <el-button size="small" @click="setStatus('processing')">处理中</el-button>
            <el-button size="small" @click="setStatus('resolved')">已解决</el-button>
            <el-button size="small" @click="setStatus('closed')">关闭</el-button>
          </div>
        </div>
        <div class="messages">
          <div v-for="message in detail.messages" :key="message.id" class="message">
            <div class="head">
              <strong>{{ message.author }}</strong>
              <span class="muted">{{ message.created_at.replace('T', ' ').slice(0, 16) }}</span>
            </div>
            <div class="body">{{ message.body }}</div>
          </div>
        </div>
        <el-input v-model="reply" type="textarea" :rows="3" placeholder="回复用户…" />
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!reply.trim()" @click="send">发送</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
}

.priority {
  font-size: 12.5px;
  color: var(--muted);
}

.priority.high {
  color: var(--danger);
  font-weight: 500;
}

.priority.low {
  color: #9aa2aa;
}

.messages {
  max-height: 300px;
  overflow: auto;
  margin-bottom: 12px;
}

.message {
  padding: 8px 0;
  border-bottom: 1px solid var(--dsh-border);
}

.head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}

.body {
  white-space: pre-wrap;
  font-size: 13px;
  margin-top: 4px;
}
</style>
