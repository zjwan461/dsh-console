<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { formatDateTime } from '@/utils/datetime'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'

interface TicketRow {
  id: number
  subject: string
  category: string
  priority: string
  status: string
  created_at: string
  last_message: string
  message_count: number
}

interface Message {
  id: number
  author: string
  body: string
  created_at: string
}

const loading = ref(false)
const rows = ref<TicketRow[]>([])
const createVisible = ref(false)
const detailVisible = ref(false)
const saving = ref(false)
const detail = ref<(TicketRow & { messages: Message[] }) | null>(null)
const reply = ref('')
const form = reactive({ subject: '', category: 'other', priority: 'normal', body: '' })

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: TicketRow[] }>('/me/tickets')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function submit() {
  saving.value = true
  try {
    await api.post('/me/tickets', form)
    ElMessage.success('工单已提交')
    createVisible.value = false
    Object.assign(form, { subject: '', category: 'other', priority: 'normal', body: '' })
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function open(row: TicketRow) {
  try {
    detail.value = await api.get(`/me/tickets/${row.id}`)
    reply.value = ''
    detailVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function send() {
  if (!detail.value || !reply.value.trim()) return
  try {
    await api.post(`/me/tickets/${detail.value.id}/messages`, { body: reply.value })
    detail.value = await api.get(`/me/tickets/${detail.value.id}`)
    reply.value = ''
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function tagType(status: string) {
  if (status === 'open') return 'warning'
  if (status === 'processing') return 'primary'
  if (status === 'resolved') return 'success'
  return 'info'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>工单</h1>
        <p>提交问题、查看管理员回复</p>
      </div>
      <el-button type="primary" @click="createVisible = true">新建工单</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe @row-click="open">
        <el-table-column prop="subject" label="主题" min-width="240" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="tagType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="100" />
        <el-table-column prop="message_count" label="消息" width="90" />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="createVisible" title="新建工单" width="560px">
      <el-form label-width="80px">
        <el-form-item label="主题">
          <el-input v-model="form.subject" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="form.category">
            <el-option label="用量" value="usage" />
            <el-option label="配额" value="quota" />
            <el-option label="故障" value="bug" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-radio-group v-model="form.priority">
            <el-radio value="low">低</el-radio>
            <el-radio value="normal">普通</el-radio>
            <el-radio value="high">高</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.body" type="textarea" :rows="4" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailVisible" :title="detail?.subject" width="640px">
      <template v-if="detail">
        <div class="meta">
          <el-tag :type="tagType(detail.status)">{{ detail.status }}</el-tag>
          <span class="muted">{{ formatDateTime(detail.created_at) }}</span>
        </div>
        <div class="messages">
          <div v-for="message in detail.messages" :key="message.id" class="message">
            <div class="message-head">
              <strong>{{ message.author }}</strong>
              <span class="muted">{{ formatDateTime(message.created_at) }}</span>
            </div>
            <div class="message-body">{{ message.body }}</div>
          </div>
        </div>
        <el-input
          v-model="reply"
          type="textarea"
          :rows="3"
          placeholder="补充说明…"
          :disabled="['resolved', 'closed'].includes(detail.status)"
        />
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!reply.trim()" @click="send">发送</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.meta {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}

.messages {
  max-height: 320px;
  overflow: auto;
  margin-bottom: 12px;
}

.message {
  padding: 10px 0;
  border-bottom: 1px solid var(--dsh-border);
}

.message-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 4px;
}

.message-body {
  white-space: pre-wrap;
  font-size: 13px;
}
</style>
