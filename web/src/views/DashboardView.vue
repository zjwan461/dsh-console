<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Calendar, Cpu, Coin, Timer } from '@element-plus/icons-vue'
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
      <div class="actions">
        <el-button v-if="!instance" type="primary" @click="openCreate">
          创建我的 DSH 云电脑
        </el-button>
        <el-button
          v-else-if="store.isRunning"
          type="primary"
          :loading="store.busy"
          @click="openInstance"
        >
          打开云舱
        </el-button>
        <el-button
          v-else
          :loading="store.busy"
          :disabled="store.isTransitional"
          @click="openInstance"
        >
          {{ instance.status === 'sleeping' ? '唤醒并打开' : '启动并打开' }}
        </el-button>
      </div>
    </div>

    <template v-if="overview">
      <div class="grid grid-4">
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">剩余时长</span>
            <span class="chip"
              ><el-icon><Timer /></el-icon
            ></span>
          </div>
          <div class="value">{{ hours(overview.quota.remaining_minutes) }}</div>
          <div class="hint">
            已用 {{ hours(overview.quota.used_minutes) }} / 共
            {{ hours(overview.quota.total_minutes) }}
          </div>
        </div>
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">当前规格</span>
            <span class="chip slate"
              ><el-icon><Cpu /></el-icon
            ></span>
          </div>
          <div class="value">
            {{ overview.quota.cpu_cores }}<small>C</small> /
            {{ gb(overview.quota.memory_mb) }}
          </div>
          <div class="hint">套餐：{{ overview.quota.plan ?? '未分配' }}</div>
        </div>
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">磁盘配额</span>
            <span class="chip bronze"
              ><el-icon><Coin /></el-icon
            ></span>
          </div>
          <div class="value">{{ overview.quota.disk_gb }}<small>GB</small></div>
          <div class="hint">实例数据目录上限</div>
        </div>
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">计费周期</span>
            <span class="chip"
              ><el-icon><Calendar /></el-icon
            ></span>
          </div>
          <div class="value date">{{ overview.quota.cycle_end }}</div>
          <div class="hint">按活跃时长扣减，闲置不计费</div>
        </div>
      </div>

      <div class="card instance-card">
        <div class="instance-head">
          <div>
            <div class="instance-title">
              <h3>我的云舱</h3>
              <span class="status-pill" :class="instance?.status ?? 'none'">
                <span class="dot" />
                {{ meta.text }}
              </span>
            </div>
            <div v-if="instance" class="instance-url mono">
              {{ instance.url }}
            </div>
            <div v-else class="muted instance-sub">
              还没有实例。创建后你会得到独立子域名、数据目录与时长配额。
            </div>
          </div>
          <div v-if="instance" class="instance-actions">
            <el-button
              v-if="
                instance.status !== 'stopped' &&
                instance.status !== 'sleeping' &&
                instance.status !== 'error'
              "
              :disabled="store.isTransitional"
              @click="act('stop')"
            >
              停止
            </el-button>
            <el-button v-else type="primary" :disabled="store.isTransitional" @click="act('start')">
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
          style="margin-top: 14px"
          :title="instance.error || '实例异常，请尝试重启'"
        />

        <template v-if="instance">
          <div class="spec-grid">
            <div>
              <span class="muted">DSH 版本</span>
              <strong>{{ instance.dsh_version }}</strong>
            </div>
            <div>
              <span class="muted">规格</span>
              <strong>
                {{ instance.spec.cpu_cores }}C / {{ gb(instance.spec.memory_mb) }} /
                {{ instance.spec.disk_gb }}GB
              </strong>
            </div>
            <div>
              <span class="muted">最近启动</span>
              <strong>{{ shortDate(instance.last_started_at) }}</strong>
            </div>
            <div>
              <span class="muted">自动休眠</span>
              <strong>{{ instance.idle_timeout_seconds / 60 }} 分钟</strong>
            </div>
          </div>

          <div class="divider" />
          <div class="events-head">
            <span class="muted">最近事件</span>
            <el-button link type="primary" @click="store.loadEvents()">刷新</el-button>
          </div>
          <el-timeline v-if="store.events.length" class="events">
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
          <div v-else class="blank small">
            <strong>暂无事件</strong>
            实例的状态变化会记录在这里
          </div>
        </template>

        <div v-else class="blank">
          <div class="blank-icon">
            <el-icon><Cpu /></el-icon>
          </div>
          <strong>还没有云舱</strong>
          创建后你会得到独立子域名、数据目录与时长配额
          <div class="blank-action">
            <el-button type="primary" @click="openCreate">创建我的 DSH 云电脑</el-button>
          </div>
        </div>
      </div>

      <div class="card announcement-card">
        <div class="section-head">
          <h3>公告</h3>
          <span class="hint">{{ overview.announcements.length }} 条</span>
        </div>
        <ul v-if="overview.announcements.length" class="announcements">
          <li v-for="item in overview.announcements" :key="item.id">
            <span class="announce-dot" :class="item.level" />
            <div>
              <strong>{{ item.title }}</strong>
              <p>{{ item.body_md }}</p>
            </div>
          </li>
        </ul>
        <div v-else class="blank small">
          <strong>暂无公告</strong>
          平台更新与维护通知会显示在这里
        </div>
      </div>
    </template>

    <el-dialog v-model="createVisible" title="创建我的 DSH 云电脑" width="520px">
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
          <div
            v-if="(store.options?.images.length ?? 0) === 0"
            class="muted"
            style="font-size: 12px"
          >
            管理员还没有导入可用版本。
          </div>
        </el-form-item>
        <el-form-item label="自动休眠">
          <el-input-number v-model="idleMinutes" :min="0" :max="240" :step="5" />
          <span class="muted" style="margin-left: 8px">分钟（0 = 不息眠）</span>
        </el-form-item>
        <el-form-item v-if="store.options" label="宿主余量">
          <span class="muted" style="font-size: 12px">
            可分配内存
            {{ Math.round(store.options.capacity.mem_limit_mb / 1024) }} GB， 已分配
            {{ Math.round(store.options.capacity.mem_committed_mb / 1024) }} GB
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
  min-height: 200px;
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
  font-weight: 600;
}

.instance-url {
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
  word-break: break-all;
}

.instance-sub {
  margin-top: 6px;
  font-size: 13px;
}

.instance-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* status pill with a dot, instead of a generic tag */
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 999px;
  background: #f0efeb;
  color: #6b7280;
}

.status-pill .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status-pill.running {
  background: #edf5ef;
  color: #3f6b4d;
}

.status-pill.running .dot {
  box-shadow: 0 0 0 3px rgba(63, 107, 77, 0.14);
}

.status-pill.error,
.status-pill.deleting {
  background: var(--danger-soft);
  color: var(--danger);
}

.status-pill.stopping,
.status-pill.waking,
.status-pill.starting,
.status-pill.provisioning {
  background: var(--bronze-soft);
  color: var(--bronze);
}

.spec-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin-top: 20px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
}

.spec-grid div {
  display: flex;
  flex-direction: column;
  gap: 5px;
  background: var(--paper-soft);
  padding: 13px 15px;
}

.spec-grid .muted {
  font-size: 11.5px;
  letter-spacing: 0.02em;
}

.spec-grid strong {
  font-size: 13.5px;
  font-weight: 550;
  font-variant-numeric: tabular-nums;
}

.divider {
  height: 1px;
  background: var(--line);
  margin: 20px 0 14px;
}

.events-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12.5px;
}

.events {
  padding-left: 2px;
  margin-top: 14px;
}

.events :deep(.el-timeline-item__timestamp) {
  color: var(--muted);
  font-size: 11.5px;
}

.events :deep(.el-timeline-item__node) {
  background: var(--primary);
}

.events :deep(.el-timeline-item__tail) {
  border-left-color: var(--line);
}

.blank.small {
  margin-top: 14px;
  padding: 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  justify-content: center;
}

.blank-action {
  margin-top: 16px;
}

.announcement-card {
  margin-top: 18px;
}

.announcements {
  list-style: none;
  margin: 16px 0 0;
  padding: 0;
  display: grid;
  gap: 0;
}

.announcements li {
  display: flex;
  gap: 12px;
  padding: 14px 0;
  border-top: 1px solid #f0ebe2;
}

.announcements li:first-child {
  border-top: none;
  padding-top: 4px;
}

.announce-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 50%;
  margin-top: 6px;
  background: var(--primary);
}

.announce-dot.warning {
  background: var(--bronze);
}

.announce-dot.maintenance {
  background: var(--danger);
}

.announcements strong {
  font-size: 13.5px;
  font-weight: 550;
}

.announcements p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.7;
}

.stat-card .value.date {
  font-size: 21px;
  margin-top: 15px;
}

@media (max-width: 900px) {
  .spec-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
