<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'

interface AnnouncementRow {
  id: number
  title: string
  body_md: string
  level: string
  status: string
  pinned: boolean
  publish_at: string | null
  created_at: string
}

const loading = ref(false)
const rows = ref<AnnouncementRow[]>([])
const visible = ref(false)
const saving = ref(false)
const form = reactive({
  title: '',
  body_md: '',
  level: 'info',
  status: 'draft',
  pinned: false,
})

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: AnnouncementRow[] }>('/admin/announcements')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, { title: '', body_md: '', level: 'info', status: 'draft', pinned: false })
  visible.value = true
}

async function submit() {
  saving.value = true
  try {
    await api.post('/admin/announcements', form)
    ElMessage.success('公告已创建')
    visible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function publish(row: AnnouncementRow) {
  try {
    await api.post(`/admin/announcements/${row.id}/publish`)
    ElMessage.success('已发布')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function remove(row: AnnouncementRow) {
  try {
    await ElMessageBox.confirm(`删除公告「${row.title}」？`, '删除', { type: 'warning' })
    await api.del(`/admin/announcements/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>公告</h1>
        <p>Markdown 内容；发布后用户工作台可见</p>
      </div>
      <el-button type="primary" @click="openCreate">新建公告</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column prop="title" label="标题" min-width="240" />
        <el-table-column prop="level" label="级别" width="120" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'published' ? 'success' : 'info'">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="置顶" width="80">
          <template #default="{ row }">{{ row.pinned ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.status === 'published'" @click="publish(row)">
              发布
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="visible" title="新建公告" width="560px">
      <el-form label-width="80px">
        <el-form-item label="标题">
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="级别">
          <el-select v-model="form.level">
            <el-option label="普通" value="info" />
            <el-option label="警告" value="warning" />
            <el-option label="维护" value="maintenance" />
          </el-select>
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="form.body_md" type="textarea" :rows="6" />
        </el-form-item>
        <el-form-item label="置顶">
          <el-switch v-model="form.pinned" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>
