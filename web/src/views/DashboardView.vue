<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useInstanceStore } from '@/stores/instance'
import type { InstanceStatus, Overview } from '@/types'

const auth = useAuthStore()
const store = useInstanceStore()
const loading = ref(true)
const overview = ref<Overview | null>(null)
const createVisible = ref(false)
const selectedImage = ref<number | null>(null)
const idleMinutes = ref(30)

const statusMeta: Record<InstanceStatus, { text: string; type: string }> = {
  provisioning: { text: '准备中', type: 'info' },
  starting: { text: '启动中', type: 'info' },
  running: { text: '运行中', type: 'success' },
  sleeping: { text: '已休眠', type: 'info' },
  waking: { text: '唤醒中', type: 'info' },
  stopping: { text: '停止中', type: 'warning' },
  stopped: { text: '已停止', type: 'info' },
  error: { text: '异常', type: 'danger' },
  deleting: { text: '删除中', type: 'danger' },
}

const instance = computed(() => store.instance)
const meta = computed(() =>
  instance.value ? statusMeta[instance.value.status] : { text: '未创建', type: 'info' },
)

function hours(minutes: number) {
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return h > 0 ? `${h} 小时 ${m} 分` : `${m} 分钟`
}

function gb(mb: number) {
  return `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB`
}

function shortDate(value: string | null) {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

async function load() {
  loading.value = true
  try {
    overview.value = await api.get<Overview>('/me/overview')
    await Promise.all([store.load(), store.loadOptions()])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  selectedImage.value = store.options?.default_image_id ?? store.options?.images[0]?.id ?? null
  idleMinutes.value = Math.round((instance.value?.idle_timeout_seconds ?? 1800) / 60)
  createVisible.value = true
}

async function submitCreate() {
  try {
    await store.create(selectedImage.value, idleMinutes.value * 60)
    createVisible.value = false
    ElMessage.success('实例创建中，通常 30–60 秒完成')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function openInstance() {
  try {
    await store.launch()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function act(action: 'start' | 'stop' | 'restart') {
  try {
    await store.act(action)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function removeInstance() {
  try {
    await ElMessageBox.prompt(
      `删除后容器会立即移除，数据保留 7 天。请输入用户名 ${auth.user?.username} 确认。`,
      '删除实例',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        inputPlaceholder: auth.user?.username,
        type: 'warning',
      },
    ).then(async ({ value }) => {
      if (value !== auth.user?.username) {
        ElMessage.warning('用户名不匹配')
        return
      }
      await store.remove(auth.user!.username)
      ElMessage.success('实例已删除')
      await load()
    })
  } catch {
    // cancelled
  }
}

onMounted(load)
onUnmounted(() => store.stopPolling())
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1>工作台</h1>
        <p>欢迎回来，{{ overview?.user.display_name || overview?.user.username }}</p>
      </div>
      <el-button v-if="!instance" type="primary" @click="openCreate">创建我的云电脑</el-button>
      <el-button v-else-if="store.isRunning" type="primary" :loading="store.busy" @click="openInstance">
        打开云舱
      </el-button>
      <el-button v-else :loading="store.busy" :disabled="store.isTransitional" @click="openInstance">
        {{ instance.status === 'sleeping' ? '唤醒并打开' : '启动并打开' }}
      </el-button>
    </div>

    <template v-if="overview">
      <div class="grid grid-4">
        <div class="card stat-card">
          <div class="label">剩余时长</div>
          <div class="value">{{ hours(overview.quota.remaining_minutes) }}</div>
          <div class="hint">
            已用 {{ hours(overview.quota.used_minutes) }} / 共
            {{ hours(overview.quota.total_minutes) }}
          </div>
        </div>
        <div class="card stat-card">
          <div class="label">当前规格</div>
          <div class="value">{{ overview.quota.cpu_cores }}C / {{ gb(overview.quota.memory_mb) }}</div>
          <div class="hint">套餐：{{ overview.quota.plan ?? '未分配' }}</div>
        </div>
        <div class="card stat-card">
          <div class="label">磁盘</div>
          <div class="value">{{ overview.quota.disk_gb }} GB</div>
          <div class="hint">数据目录配额</div>
        </div>
        <div class="card stat-card">
          <div class="label">计费周期</div>
          <div class="value" style="font-size: 18px">{{ overview.quota.cycle_end }}</div>
          <div class="hint">按活跃时长扣减</div>
        </div>
      </div>

      <div class="card instance-card" style="margin-top: 16px">
        <div class="instance-head">
          <div>
            <div class="instance-title">
              <h3>我的云舱</h3>
              <el-tag :type="meta.type as 'success' | 'info' | 'warning' | 'danger'" effect="plain">
                {{ meta.text }}
              </el-tag>
            </div>
            <div v-if="instance" class="instance-url mono">{{ instance.url }}</div>
            <div v-else class="muted">还没有实例。创建后你会得到独立子域名、数据目录与时长配额。</div>
          </div>
          <div v-if="instance" class="instance-actions">
            <el-button
              v-if="instance.status !== 'stopped' && instance.status !== 'sleeping' && instance.status !== 'error'"
              :disabled="store.isTransitional"
              @click="act('stop')"
            >
              停止
            </el-button>
            <el-button
              v-else
              type="primary"
              :disabled="store.isTransitional"
              @click="act('start')"
            >
              启动
            </el-button>
            <el-button :disabled="store.isTransitional" @click="act('restart')">重启</el-button>
            <el-button type="danger" plain @click="removeInstance">删除</el-button>
          </div>
        </div>

        <el-alert
          v-if="instance?.status === 'error'"
          type="error"
          :closable="false"
          show-icon
          style="margin-top: 12px"
          :title="instance.error || '实例异常，请尝试重启'"
        />

        <template v-if="instance">
          <div class="spec-grid">
            <div><span class="muted">版本</span><strong>{{ instance.dsh_version }}</strong></div>
            <div>
              <span class="muted">规格</span>
              <strong>
                {{ instance.spec.cpu_cores }}C / {{ gb(instance.spec.memory_mb) }} /
                {{ instance.spec.disk_gb }}GB
              </strong>
            </div>
            <div><span class="muted">最近启动</span><strong>{{ shortDate(instance.last_started_at) }}</strong></div>
            <div><span class="muted">自动休眠</span><strong>{{ instance.idle_timeout_seconds / 60 }} 分钟</strong></div>
          </div>

          <el-divider style="margin: 18px 0 12px" />
          <div class="events-head">
            <span class="muted">最近事件</span>
            <el-button link type="primary" @click="store.loadEvents()">刷新</el-button>
          </div>
          <el-timeline v-if="store.events.length" style="padding-left: 4px; margin-top: 8px">
            <el-timeline-item
              v-for="event in store.events"
              :key="event.id"
              :timestamp="shortDate(event.created_at)"
              size="small"
            >
              <span class="mono">{{ event.type }}</span>
              <span v-if="event.to" class="muted"> → {{ event.to }}</span>
              <span v-if="event.message" class="muted"> · {{ event.message }}</span>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无事件" :image-size="48" />
        </template>
        <el-empty v-else description="点击右上角创建你的第一间云舱" :image-size="90" />
      </div>

      <div class="card" style="margin-top: 16px">
        <div class="section-head"><h3>公告</h3></div>
        <el-empty v-if="overview.announcements.length === 0" description="暂无公告" :image-size="60" />
      </div>
    </template>

    <el-dialog v-model="createVisible" title="创建我的云电脑" width="520px">
      <el-form label-width="100px">
        <el-form-item label="当前套餐">
          <el-tag>{{ overview?.quota.plan ?? '未分配' }}</el-tag>
          <span class="muted" style="margin-left: 10px">
            {{ overview?.quota.cpu_cores }}C / {{ gb(overview?.quota.memory_mb ?? 0) }} /
            {{ overview?.quota.disk_gb }}GB
          </span>
        </el-form-item>
        <el-form-item label="DSH 版本">
          <el-select v-model="selectedImage" placeholder="选择版本" style="width: 100%">
            <el-option
              v-for="image in store.options?.images ?? []"
              :key="image.id"
              :label="`${image.version}${image.is_default ? '（默认）' : ''}`"
              :value="image.id"
            />
          </el-select>
          <div v-if="(store.options?.images.length ?? 0) === 0" class="muted" style="font-size: 12px">
            管理员还没有导入可用版本。
          </div>
        </el-form-item>
        <el-form-item label="自动休眠">
          <el-input-number v-model="idleMinutes" :min="0" :max="240" :step="5" />
          <span class="muted" style="margin-left: 8px">分钟（0 = 不息眠）</span>
        </el-form-item>
        <el-form-item v-if="store.options" label="宿主余量">
          <span class="muted" style="font-size: 12px">
            可分配内存 {{ Math.round(store.options.capacity.mem_limit_mb / 1024) }} GB，
            已分配 {{ Math.round(store.options.capacity.mem_committed_mb / 1024) }} GB
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="store.busy"
          :disabled="!selectedImage"
          @click="submitCreate"
        >
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.instance-card {
  min-height: 220px;
}

.instance-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  flex-wrap: wrap;
}

.instance-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.instance-title h3 {
  margin: 0;
  font-size: 16px;
}

.instance-url {
  margin-top: 6px;
  color: var(--dsh-muted);
  font-size: 12px;
  word-break: break-all;
}

.instance-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.spec-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.spec-grid div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px 12px;
}

.events-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.section-head h3 {
  margin: 0;
  font-size: 15px;
}

@media (max-width: 900px) {
  .spec-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
