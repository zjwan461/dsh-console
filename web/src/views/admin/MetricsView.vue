<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Cpu, Monitor, Timer, UserFilled } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import LineChart from '@/components/LineChart.vue'
import { formatShortDateTime } from '@/utils/datetime'

interface Overview {
  users: { total: number; active_7d: number }
  instances: { running: number; sleeping: number; stopped: number; error: number }
  today: { active_hours: number; cpu_hours: number; mem_gb_hours: number }
  capacity: {
    cpu_cores: number
    cpu_committed: number
    mem_total_mb: number
    mem_committed_mb: number
    mem_limit_mb: number
    disk_total_gb: number
    disk_committed_gb: number
  }
  host: null | {
    cpu_percent: number
    mem_used_percent: number
    load1: number
    containers_running: number
  }
}

const loading = ref(true)
const overview = ref<Overview | null>(null)
const node = ref<
  Array<{ ts: string; cpu_percent: number; mem_used_bytes: number; mem_total_bytes: number }>
>([])
const top = ref<Array<{ key: string; value: number }>>([])

const nodeLabels = computed(() => node.value.map((p) => formatShortDateTime(p.ts)))
const nodeCpu = computed(() => node.value.map((p) => p.cpu_percent))
const nodeMem = computed(() =>
  node.value.map((p) =>
    p.mem_total_bytes ? +((p.mem_used_bytes / p.mem_total_bytes) * 100).toFixed(1) : 0,
  ),
)

async function load() {
  loading.value = true
  try {
    overview.value = await api.get<Overview>('/admin/metrics/overview')
    const since = new Date(Date.now() - 3600_000).toISOString()
    const nodeData = await api.get<{ series: typeof node.value }>(
      `/admin/metrics/node?from=${encodeURIComponent(since)}`,
    )
    node.value = nodeData.series
    const usage = await api.get<{ rows: typeof top.value }>(
      '/admin/metrics/usage?group_by=user&metric=active_seconds',
    )
    top.value = usage.rows
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function gb(mb: number) {
  return (mb / 1024).toFixed(0)
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1>监控总览</h1>
        <p>实例、宿主与用量；数据来自 worker 采样</p>
      </div>
      <el-button @click="load">刷新</el-button>
    </div>

    <template v-if="overview">
      <div class="grid grid-4">
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">运行实例</span>
            <span class="chip"
              ><el-icon><Monitor /></el-icon
            ></span>
          </div>
          <div class="value">{{ overview.instances.running }}</div>
          <div class="hint">
            休眠 {{ overview.instances.sleeping }} · 停止 {{ overview.instances.stopped }}
          </div>
        </div>
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">今日活跃</span>
            <span class="chip"
              ><el-icon><Timer /></el-icon
            ></span>
          </div>
          <div class="value">{{ overview.today.active_hours }}<small>h</small></div>
          <div class="hint">CPU {{ overview.today.cpu_hours }} 核时</div>
        </div>
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">内存分配</span>
            <span class="chip slate"
              ><el-icon><Cpu /></el-icon
            ></span>
          </div>
          <div class="value">
            {{ gb(overview.capacity.mem_committed_mb)
            }}<small>/ {{ gb(overview.capacity.mem_total_mb) }} GB</small>
          </div>
          <div class="hint">可分配上限 {{ gb(overview.capacity.mem_limit_mb) }} GB</div>
        </div>
        <div class="card stat-card">
          <div class="stat-top">
            <span class="label">用户</span>
            <span class="chip bronze"
              ><el-icon><UserFilled /></el-icon
            ></span>
          </div>
          <div class="value">{{ overview.users.total }}</div>
          <div class="hint">7 日活跃 {{ overview.users.active_7d }}</div>
        </div>
      </div>

      <div class="grid grid-2" style="margin-top: 18px">
        <div class="card">
          <div class="section-head">
            <h3>宿主 CPU</h3>
            <span class="hint">近 1 小时</span>
          </div>
          <LineChart
            v-if="node.length"
            :x="nodeLabels"
            :series="[{ name: 'CPU %', data: nodeCpu, color: '#2f6f63', area: true }]"
            height="240px"
          />
          <div v-else class="blank small">
            <strong>暂无节点指标</strong>
            worker 采样后这里会出现趋势
          </div>
        </div>
        <div class="card">
          <div class="section-head">
            <h3>宿主内存</h3>
            <span class="hint">近 1 小时</span>
          </div>
          <LineChart
            v-if="node.length"
            :x="nodeLabels"
            :series="[{ name: '内存 %', data: nodeMem, color: '#a9713f' }]"
            height="240px"
          />
          <div v-else class="blank small">
            <strong>暂无节点指标</strong>
            worker 采样后这里会出现趋势
          </div>
        </div>
      </div>

      <div class="card" style="margin-top: 18px">
        <div class="section-head">
          <h3>用户活跃时长 Top</h3>
          <span class="hint">按本周期活跃时长排序</span>
        </div>
        <el-table :data="top" stripe style="margin-top: 8px">
          <el-table-column prop="key" label="用户" />
          <el-table-column label="活跃时长" width="180">
            <template #default="{ row }">{{ (row.value / 3600).toFixed(1) }} h</template>
          </el-table-column>
          <el-table-column label="占比" min-width="220">
            <template #default="{ row }">
              <el-progress
                :percentage="top[0]?.value ? Math.round((row.value / top[0].value) * 100) : 0"
                :show-text="false"
              />
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!top.length" description="暂无用量" :image-size="60" />
      </div>
    </template>
  </div>
</template>
