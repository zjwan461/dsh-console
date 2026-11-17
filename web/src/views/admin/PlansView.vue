<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { Plan } from '@/types'

const loading = ref(false)
const rows = ref<Plan[]>([])
const visible = ref(false)
const saving = ref(false)
const editing = ref<number | null>(null)
const form = reactive({
  code: '',
  name: '',
  description: '',
  cpu_cores: 1,
  memory_mb: 2048,
  disk_gb: 20,
  monthly_minutes: 3600,
  max_snapshots: 2,
  max_user_images: 2,
  gpu_count: 0,
  is_default: false,
  enabled: true,
  sort_order: 0,
})

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: Plan[] }>('/admin/plans')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    code: '',
    name: '',
    description: '',
    cpu_cores: 1,
    memory_mb: 2048,
    disk_gb: 20,
    monthly_minutes: 3600,
    max_snapshots: 2,
    max_user_images: 2,
    gpu_count: 0,
    is_default: false,
    enabled: true,
    sort_order: 0,
  })
}

function openCreate() {
  editing.value = null
  resetForm()
  visible.value = true
}

function openEdit(row: Plan) {
  editing.value = row.id
  Object.assign(form, {
    code: row.code,
    name: row.name,
    description: row.description,
    cpu_cores: row.cpu_cores,
    memory_mb: row.memory_mb,
    disk_gb: row.disk_gb,
    monthly_minutes: row.monthly_minutes,
    max_snapshots: row.max_snapshots,
    max_user_images: row.max_user_images,
    gpu_count: row.gpu_count,
    is_default: row.is_default,
    enabled: row.enabled,
    sort_order: row.sort_order,
  })
  visible.value = true
}

async function submit() {
  saving.value = true
  try {
    if (editing.value) {
      const { code, ...rest } = form
      await api.patch(`/admin/plans/${editing.value}`, rest)
      ElMessage.success('套餐已更新')
    } else {
      await api.post('/admin/plans', form)
      ElMessage.success('套餐已创建')
    }
    visible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function remove(row: Plan) {
  try {
    await ElMessageBox.confirm(`删除套餐「${row.name}」？`, '删除套餐', { type: 'warning' })
    await api.del(`/admin/plans/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

function minutesLabel(minutes: number) {
  return `${Math.round(minutes / 60)} h / 月`
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>套餐管理</h1>
        <p>资源规格与每月可用活跃时长；单用户可覆盖</p>
      </div>
      <el-button type="primary" @click="openCreate">新建套餐</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column prop="name" label="名称" width="120" />
        <el-table-column prop="code" label="编码" width="120" />
        <el-table-column label="规格" min-width="180">
          <template #default="{ row }">
            {{ row.cpu_cores }}C / {{ Math.round(row.memory_mb / 1024) }}G / {{ row.disk_gb }}GB
          </template>
        </el-table-column>
        <el-table-column label="月时长" width="120">
          <template #default="{ row }">{{ minutesLabel(row.monthly_minutes) }}</template>
        </el-table-column>
        <el-table-column label="快照/镜像" width="110">
          <template #default="{ row }">{{ row.max_snapshots }} / {{ row.max_user_images }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
            <el-tag v-else-if="!row.enabled" type="info" size="small">停用</el-tag>
            <span v-else class="muted">启用</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="visible" :title="editing ? '编辑套餐' : '新建套餐'" width="560px">
      <el-form label-width="110px">
        <el-form-item label="编码">
          <el-input v-model="form.code" :disabled="!!editing" placeholder="basic" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="基础" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" />
        </el-form-item>
        <el-form-item label="CPU 核数">
          <el-input-number v-model="form.cpu_cores" :min="0.5" :max="64" :step="0.5" />
        </el-form-item>
        <el-form-item label="内存 MB">
          <el-input-number v-model="form.memory_mb" :min="512" :max="1048576" :step="512" />
        </el-form-item>
        <el-form-item label="磁盘 GB">
          <el-input-number v-model="form.disk_gb" :min="1" :max="100000" />
        </el-form-item>
        <el-form-item label="月时长(分)">
          <el-input-number v-model="form.monthly_minutes" :min="0" :max="5256000" :step="60" />
        </el-form-item>
        <el-form-item label="快照上限">
          <el-input-number v-model="form.max_snapshots" :min="0" :max="100" />
        </el-form-item>
        <el-form-item label="镜像上限">
          <el-input-number v-model="form.max_user_images" :min="0" :max="100" />
        </el-form-item>
        <el-form-item label="默认套餐">
          <el-switch v-model="form.is_default" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
