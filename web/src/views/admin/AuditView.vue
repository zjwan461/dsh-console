<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { AuditEntry, Paginated } from '@/types'

const loading = ref(false)
const rows = ref<AuditEntry[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(30)
const filters = reactive({ action: '', actor_id: '' })

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: String(page.value),
      page_size: String(pageSize.value),
    })
    if (filters.action) params.set('action', filters.action)
    if (filters.actor_id) params.set('actor_id', filters.actor_id)
    const data = await api.get<Paginated<AuditEntry>>(`/admin/audit?${params}`)
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  load()
}

function detailText(detail: Record<string, unknown> | null) {
  if (!detail) return ''
  return JSON.stringify(detail)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>审计日志</h1>
        <p>所有写操作留痕，只增不改</p>
      </div>
    </div>

    <div class="toolbar">
      <el-input
        v-model="filters.action"
        placeholder="动作前缀，如 admin.user"
        style="width: 240px"
        clearable
        @keyup.enter="applyFilter"
      />
      <el-input
        v-model="filters.actor_id"
        placeholder="操作者 ID"
        style="width: 160px"
        clearable
        @keyup.enter="applyFilter"
      />
      <el-button @click="applyFilter">查询</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">
            {{ row.created_at.replace('T', ' ').slice(0, 19) }}
          </template>
        </el-table-column>
        <el-table-column prop="actor_name" label="操作者" width="120" />
        <el-table-column prop="action" label="动作" width="220">
          <template #default="{ row }"><span class="mono">{{ row.action }}</span></template>
        </el-table-column>
        <el-table-column label="目标" width="160">
          <template #default="{ row }">
            <span class="muted">{{ row.target_type }} #{{ row.target_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" width="140" />
        <el-table-column label="详情" min-width="260">
          <template #default="{ row }">
            <span class="mono muted" style="font-size: 12px">{{ detailText(row.detail) }}</span>
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
  </div>
</template>
