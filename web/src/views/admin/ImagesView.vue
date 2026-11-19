<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Box } from '@element-plus/icons-vue'
import { api, errorMessage, getCsrfToken } from '@/api/client'

interface ImageRow {
  id: number
  version: string
  image_ref: string
  digest: string | null
  size_bytes: number
  arch: string
  status: 'importing' | 'ready' | 'disabled' | 'failed'
  source: string
  is_default: boolean
  changelog: string | null
  error: string | null
  created_at: string
}

interface RemoteItem {
  tag: string
  version: string
  variant: string
  digest: string | null
  size_bytes: number
  created_at: string | null
  imported: boolean
  matched_by: string
  image_id: number | null
  is_default: boolean
  status: string | null
}

interface Catalog {
  repository: string
  checked_at: string | null
  items: RemoteItem[]
  latest_version: string | null
  latest_tag: string | null
  current_version: string | null
  update_available: boolean
  stale: boolean
  error?: string
}

const tab = ref('local')
const loading = ref(false)
const rows = ref<ImageRow[]>([])
const catalog = ref<Catalog | null>(null)
const catalogLoading = ref(false)
const importing = ref<string | null>(null)

const registerVisible = ref(false)
const saving = ref(false)
const form = reactive({ image_ref: '', version: '', changelog: '', is_default: false })

const tarVisible = ref(false)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const tarForm = reactive({ version: '', changelog: '', is_default: false })

const sizeText = (bytes: number) => (bytes ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : '—')
const shortDigest = (digest: string | null) =>
  digest ? digest.replace('sha256:', '').slice(0, 12) : '—'

const newTags = computed(() => (catalog.value?.items ?? []).filter((item) => !item.imported).length)
const latestItem = computed(
  () => (catalog.value?.items ?? []).find((item) => item.tag === catalog.value?.latest_tag) ?? null,
)

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

async function loadCatalog(refresh = false) {
  catalogLoading.value = true
  try {
    catalog.value = await api.get<Catalog>(`/admin/images/remote${refresh ? '?refresh=true' : ''}`)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    catalogLoading.value = false
  }
}

async function refreshCatalog() {
  try {
    catalog.value = await api.post<Catalog>('/admin/images/remote/check')
    if (catalog.value?.error) {
      ElMessage.warning(`检查失败：${catalog.value.error}`)
    } else {
      ElMessage.success(`已检查：${catalog.value?.items.length ?? 0} 个版本`)
    }
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

// A 1.3 GB pull takes minutes, so the API only reserves the row; poll until the
// instance stops being `importing` instead of leaving a spinner with no feedback.
let pollTimer: ReturnType<typeof setTimeout> | null = null

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

async function pollImport() {
  stopPolling()
  const rowsNow = rows.value.filter((row) => row.status === 'importing')
  if (!rowsNow.length) return
  pollTimer = setTimeout(async () => {
    await load()
    const failed = rows.value.find((row) => row.status === 'failed')
    if (failed) {
      ElMessage.error(`导入失败：${failed.error ?? failed.version}`)
      stopPolling()
      return
    }
    if (!rows.value.some((row) => row.status === 'importing')) {
      const ready = rows.value.find((row) => row.status === 'ready')
      ElMessage.success(ready ? `已导入 ${ready.version}` : '导入完成')
      await loadCatalog()
      stopPolling()
      return
    }
    await pollImport()
  }, 4000)
}

async function importTag(item: RemoteItem, asDefault = false) {
  importing.value = item.tag
  try {
    await api.post('/admin/images/remote/import', { tag: item.tag, is_default: asDefault })
    ElMessage.success(`已开始导入 ${item.tag}，可离开本页，进度见「DSH 版本」列表`)
    await Promise.all([load(), loadCatalog()])
    await pollImport()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    importing.value = null
  }
}

function openRegister() {
  Object.assign(form, {
    image_ref: '',
    version: '',
    changelog: '',
    is_default: rows.value.length === 0,
  })
  registerVisible.value = true
}

async function submitRegister() {
  saving.value = true
  try {
    await api.post('/admin/images', form)
    ElMessage.success('镜像已登记')
    registerVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

function openTar() {
  Object.assign(tarForm, { version: '', changelog: '', is_default: rows.value.length === 0 })
  tarVisible.value = true
}

function pickTar() {
  fileInput.value?.click()
}

async function onTar(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  const body = new FormData()
  body.append('file', file)
  if (tarForm.version) body.append('version', tarForm.version)
  if (tarForm.changelog) body.append('changelog', tarForm.changelog)
  body.append('is_default', String(tarForm.is_default))
  try {
    const response = await fetch('/api/v1/admin/images/upload', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': getCsrfToken() },
      body,
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.error?.message ?? `上传失败（${response.status}）`)
    }
    ElMessage.success(`已导入 ${payload.data.image_ref}`)
    tarVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function setDefault(row: ImageRow) {
  try {
    await api.patch(`/admin/images/${row.id}`, { is_default: true })
    ElMessage.success('已设为默认版本')
    await Promise.all([load(), loadCatalog()])
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
    await Promise.all([load(), loadCatalog()])
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

function tag(status: string) {
  if (status === 'ready') return 'success'
  if (status === 'disabled') return 'info'
  if (status === 'failed') return 'danger'
  return 'warning'
}

const STATUS_TEXT: Record<string, string> = {
  ready: '可用',
  disabled: '已禁用',
  failed: '失败',
  importing: '导入中',
}

const SOURCE_TEXT: Record<string, string> = {
  local: '本机登记',
  pull: '仓库拉取',
  upload: '离线导入',
}

function statusText(status: string) {
  return STATUS_TEXT[status] ?? status
}

function sourceText(source: string) {
  return SOURCE_TEXT[source] ?? source
}

onMounted(async () => {
  await load()
  loadCatalog()
  // A pull started before a refresh keeps running server-side; resume reporting.
  if (rows.value.some((row) => row.status === 'importing')) await pollImport()
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1>DSH 版本</h1>
        <p>自动发现 dsh-docker 在 GHCR 上的新构建，也支持离线导入 tar 包</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button @click="openRegister">登记本机镜像</el-button>
        <el-button type="primary" @click="openTar">导入 tar 包</el-button>
      </div>
    </div>

    <el-alert
      v-if="catalog?.update_available && latestItem && !latestItem.imported"
      type="success"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #title>
        发现新版本 <strong>{{ catalog?.latest_version }}</strong> （当前默认
        {{ catalog?.current_version ?? '未设置' }}）
      </template>
      <el-button
        size="small"
        type="primary"
        style="margin-top: 8px"
        :loading="importing === latestItem.tag"
        @click="importTag(latestItem, true)"
      >
        导入并设为默认
      </el-button>
    </el-alert>

    <el-tabs v-model="tab">
      <el-tab-pane label="已导入" name="local">
        <div class="toolbar">
          <span class="muted" style="font-size: 13px">
            共 {{ rows.length }} 个版本；同一时间只有一个默认版本
          </span>
        </div>
        <div class="table-wrap">
          <el-table v-loading="loading" :data="rows" stripe>
            <el-table-column label="版本" width="170">
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
            <el-table-column label="来源" width="100">
              <template #default="{ row }">
                <span class="muted">{{ sourceText(row.source) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tooltip
                  v-if="row.status === 'failed' && row.error"
                  :content="row.error"
                  placement="top"
                >
                  <el-tag :type="tag(row.status)" size="small">
                    {{ statusText(row.status) }}
                  </el-tag>
                </el-tooltip>
                <el-tag v-else :type="tag(row.status)" size="small">
                  {{ statusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="100">
              <template #default="{ row }">{{ sizeText(row.size_bytes) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="220" fixed="right">
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
      </el-tab-pane>

      <el-tab-pane name="remote">
        <template #label>
          仓库版本
          <el-badge v-if="newTags" :value="newTags" type="danger" style="margin-left: 6px" />
        </template>

        <div class="toolbar">
          <span class="mono muted" style="font-size: 12px">{{ catalog?.repository }}</span>
          <span class="muted" style="font-size: 12px">
            上次检查 {{ catalog?.checked_at?.replace('T', ' ').slice(0, 16) ?? '从未' }}
          </span>
          <el-button size="small" :loading="catalogLoading" @click="refreshCatalog">
            立即检查
          </el-button>
        </div>

        <el-alert
          v-if="catalog?.error"
          type="warning"
          :closable="false"
          show-icon
          :title="`无法访问仓库：${catalog.error}`"
          style="margin-bottom: 12px"
        />

        <div class="table-wrap">
          <el-table
            v-if="(catalog?.items ?? []).length"
            v-loading="catalogLoading"
            :data="catalog?.items ?? []"
            stripe
          >
            <el-table-column label="标签" width="170">
              <template #default="{ row }">
                <strong>{{ row.tag }}</strong>
                <el-tag v-if="row.variant === 'slim'" size="small" style="margin-left: 6px">
                  slim
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="110">
              <template #default="{ row }">{{ sizeText(row.size_bytes) }}</template>
            </el-table-column>
            <el-table-column label="构建时间" width="170">
              <template #default="{ row }">
                <span class="muted">
                  {{ row.created_at?.replace('T', ' ').slice(0, 16) ?? '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="Digest" width="150">
              <template #default="{ row }">
                <span class="mono" style="font-size: 12px">{{ shortDigest(row.digest) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="140">
              <template #default="{ row }">
                <el-tag v-if="row.imported" type="success" size="small">已导入</el-tag>
                <el-tag v-else-if="row.tag === catalog?.latest_tag" type="warning" size="small">
                  可导入（最新）
                </el-tag>
                <span v-else class="muted">未导入</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <template v-if="!row.imported">
                  <el-button
                    link
                    type="primary"
                    :loading="importing === row.tag"
                    :disabled="importing !== null"
                    @click="importTag(row)"
                  >
                    导入
                  </el-button>
                  <el-button
                    link
                    type="primary"
                    :loading="importing === row.tag"
                    :disabled="importing !== null"
                    @click="importTag(row, true)"
                  >
                    导入并设默认
                  </el-button>
                </template>
                <span v-else class="muted">已导入</span>
              </template>
            </el-table-column>
          </el-table>
          <div v-else-if="!catalogLoading" class="blank">
            <div class="blank-icon">
              <el-icon><Box /></el-icon>
            </div>
            <strong>还没有可识别的版本</strong>
            仓库里只有 latest / sha-* 标签时不会列出；打一个版本号标签或改 DSH_VERSION 后重新检查
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <input ref="fileInput" type="file" accept=".tar" style="display: none" @change="onTar" />

    <el-dialog v-model="registerVisible" title="登记本机已有镜像" width="560px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="镜像必须已经 docker pull / docker load 到本机"
        description="控制台不构建镜像，只登记引用并校验存在性。拉仓库里的版本请用「仓库版本」页。"
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
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitRegister">登记</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="tarVisible" title="导入 tar 包" width="540px" :close-on-click-modal="false">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="tar 包由 docker save 生成"
        description="docker save -o dsh.tar ghcr.io/zjwan461/dsh-docker:0.2.1-rc.5"
        style="margin-bottom: 14px"
      />
      <el-form label-width="90px">
        <el-form-item label="版本号">
          <el-input v-model="tarForm.version" placeholder="留空则取自镜像 tag" />
        </el-form-item>
        <el-form-item label="变更说明">
          <el-input v-model="tarForm.changelog" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="tarForm.is_default" />
        </el-form-item>
      </el-form>
      <el-progress
        v-if="uploading"
        :percentage="100"
        :indeterminate="true"
        style="margin-bottom: 10px"
      />
      <p v-if="uploading" class="muted" style="font-size: 12px">
        正在上传并执行 docker load，大镜像需要几十秒。
      </p>
      <template #footer>
        <el-button :disabled="uploading" @click="tarVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="pickTar">选择文件并导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
</style>
