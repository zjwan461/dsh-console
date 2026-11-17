<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { Invite } from '@/types'

const loading = ref(false)
const rows = ref<Invite[]>([])
const visible = ref(false)
const saving = ref(false)
const form = reactive({ role: 'user', max_uses: 1, expires_in_days: 7, note: '' })

async function load() {
  loading.value = true
  try {
    const data = await api.get<{ items: Invite[] }>('/admin/invites')
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, { role: 'user', max_uses: 1, expires_in_days: 7, note: '' })
  visible.value = true
}

async function submit() {
  saving.value = true
  try {
    await api.post('/admin/invites', form)
    ElMessage.success('邀请码已生成')
    visible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function copy(invite: Invite) {
  const link = `${window.location.origin}/register?code=${invite.code}`
  try {
    await navigator.clipboard.writeText(link)
    ElMessage.success('注册链接已复制')
  } catch {
    ElMessage.info(link)
  }
}

async function revoke(row: Invite) {
  try {
    await ElMessageBox.confirm(`吊销邀请码 ${row.code}？`, '吊销', { type: 'warning' })
    await api.del(`/admin/invites/${row.id}`)
    ElMessage.success('已吊销')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

function tag(status: string) {
  if (status === 'active') return 'success'
  if (status === 'expired') return 'warning'
  return 'info'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>邀请码</h1>
        <p>一次性或限次，过期自动失效；注册即绑定默认套餐</p>
      </div>
      <el-button type="primary" @click="openCreate">生成邀请码</el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column prop="code" label="邀请码" min-width="220">
          <template #default="{ row }">
            <span class="mono">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="90">
          <template #default="{ row }">{{ row.role === 'admin' ? '管理员' : '用户' }}</template>
        </el-table-column>
        <el-table-column label="使用" width="100">
          <template #default="{ row }">{{ row.used_count }} / {{ row.max_uses }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="tag(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="过期时间" width="180">
          <template #default="{ row }">
            <span class="muted">{{ row.expires_at?.replace('T', ' ').slice(0, 16) ?? '永久' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="140" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.status !== 'active'" @click="copy(row)">
              复制链接
            </el-button>
            <el-button link type="danger" :disabled="row.status === 'revoked'" @click="revoke(row)">
              吊销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="visible" title="生成邀请码" width="440px">
      <el-form label-width="100px">
        <el-form-item label="角色">
          <el-radio-group v-model="form.role">
            <el-radio value="user">用户</el-radio>
            <el-radio value="admin">管理员</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="可用次数">
          <el-input-number v-model="form.max_uses" :min="1" :max="1000" />
        </el-form-item>
        <el-form-item label="有效期(天)">
          <el-input-number v-model="form.expires_in_days" :min="1" :max="365" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" placeholder="例如：11 月批次" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>
