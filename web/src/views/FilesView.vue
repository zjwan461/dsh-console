<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage, getCsrfToken } from '@/api/client'

interface FileEntry {
  name: string
  path: string
  type: 'file' | 'dir'
  size: number
  modified_at: string
}

const loading = ref(false)
const path = ref('/')
const entries = ref<FileEntry[]>([])
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const segments = computed(() => {
  const parts = path.value.split('/').filter(Boolean)
  const result = [{ name: '根目录', path: '/' }]
  let acc = ''
  for (const part of parts) {
    acc += `/${part}`
    result.push({ name: part, path: acc })
  }
  return result
})

async function load(target = path.value) {
  loading.value = true
  try {
    const data = await api.get<{ path: string; entries: FileEntry[] }>(
      `/instances/me/files?path=${encodeURIComponent(target)}`,
    )
    path.value = data.path
    entries.value = data.entries
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function open(entry: FileEntry) {
  if (entry.type === 'dir') load(entry.path)
}

function size(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function download(entry: FileEntry) {
  window.location.href = `/api/v1/instances/me/files/download?path=${encodeURIComponent(entry.path)}`
}

async function mkdir() {
  try {
    const { value } = await ElMessageBox.prompt('目录名称', '新建目录')
    await api.post('/instances/me/files/mkdir', { path: path.value, name: value })
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function rename(entry: FileEntry) {
  try {
    const { value } = await ElMessageBox.prompt('新名称', '重命名', { inputValue: entry.name })
    await api.post('/instances/me/files/rename', { path: entry.path, new_name: value })
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function remove(entry: FileEntry) {
  try {
    await ElMessageBox.confirm(`删除 ${entry.name}？`, '删除', { type: 'warning' })
    await api.del(
      `/instances/me/files?path=${encodeURIComponent(entry.path)}&recursive=${entry.type === 'dir'}`,
    )
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
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
  body.append('path', path.value)
  body.append('file', file)
  try {
    const response = await fetch('/api/v1/instances/me/files/upload', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': getCsrfToken() },
      body,
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.error?.message ?? '上传失败')
    }
    ElMessage.success('上传完成')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    uploading.value = false
    input.value = ''
  }
}

onMounted(() => load('/'))
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>文件</h1>
        <p>管理实例 workspace；下载走后端流式读取</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button @click="mkdir">新建目录</el-button>
        <el-button type="primary" :loading="uploading" @click="pickFile">上传文件</el-button>
        <input ref="fileInput" type="file" style="display: none" @change="onFile" />
      </div>
    </div>

    <div class="card">
      <div class="crumbs">
        <template v-for="(seg, index) in segments" :key="seg.path">
          <a @click="load(seg.path)">{{ seg.name }}</a>
          <span v-if="index < segments.length - 1">/</span>
        </template>
      </div>

      <el-table v-loading="loading" :data="entries" stripe @row-dblclick="open">
        <el-table-column label="名称" min-width="260">
          <template #default="{ row }">
            <a class="name" @click="open(row)">
              {{ row.type === 'dir' ? '📁' : '📄' }} {{ row.name }}
            </a>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="120">
          <template #default="{ row }">{{ row.type === 'dir' ? '—' : size(row.size) }}</template>
        </el-table-column>
        <el-table-column label="修改时间" width="180">
          <template #default="{ row }">{{ row.modified_at.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.type === 'file'" link type="primary" @click="download(row)">
              下载
            </el-button>
            <el-button link type="primary" @click="rename(row)">重命名</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !entries.length" description="空目录" :image-size="70" />
    </div>
  </div>
</template>

<style scoped>
.crumbs {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 12px;
  font-size: 13px;
}

.crumbs a {
  cursor: pointer;
}

.name {
  cursor: pointer;
}
</style>
