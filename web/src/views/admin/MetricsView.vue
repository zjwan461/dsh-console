<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import LineChart from '@/components/LineChart.vue'

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
const node = ref<Array<{ ts: string; cpu_percent: number; mem_used_bytes: number; mem_total_bytes: number }>>([])
const top = ref<Array<{ key: string; value: number }>>([])

const nodeLabels = computed(() => node.value.map((p) => p.ts.slice(5, 16).replace('T', ' ')))
const nodeCpu = computed(() => node.value.map((p) => p.cpu_percent))
const nodeMem = computed(() =>
  node.value.map((p) => (p.mem_total_bytes ? +((p.mem_used_bytes / p.mem_total_bytes) * 100).toFixed(1) : 0)),
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
          <div class="label">运行实例</div>
          <div class="value">{{ overview.instances.running }}</div>
          <div class="hint">
            休眠 {{ overview.instances.sleeping }} · 停止 {{ overview.instances.stopped }}
          </div>
        </div>
        <div class="card stat-card">
          <div class="label">今日活跃</div>
          <div class="value">{{ overview.today.active_hours }} h</div>
          <div class="hint">CPU {{ overview.today.cpu_hours }} 核时</div>
        </div>
        <div class="card stat-card">
          <div class="label">内存分配</div>
          <div class="value">
            {{ gb(overview.capacity.mem_committed_mb) }} / {{ gb(overview.capacity.mem_total_mb) }} GB
          </div>
          <div class="hint">可分配上限 {{ gb(overview.capacity.mem_limit_mb) }} GB</div>
        </div>
        <div class="card stat-card">
          <div class="label">用户</div>
          <div class="value">{{ overview.users.total }}</div>
          <div class="hint">7 日活跃 {{ overview.users.active_7d }}</div>
        </div>
      </div>

      <div class="grid grid-2" style="margin-top: 16px">
        <div class="card">
          <h3 style="margin: 0 0 8px">宿主 CPU（近 1 小时）</h3>
          <LineChart
            v-if="node.length"
            :x="nodeLabels"
            :series="[{ name: 'CPU %', data: nodeCpu, color: '#4d6bfe', area: true }]"
            height="240px"
          />
          <el-empty v-else description="暂无节点指标" :image-size="60" />
        </div>
        <div class="card">
          <h3 style="margin: 0 0 8px">宿主内存使用率</h3>
          <LineChart
            v-if="node.length"
            :x="nodeLabels"
            :series="[{ name: '内存 %', data: nodeMem, color: '#f59e0b' }]"
            height="240px"
          />
          <el-empty v-else description="暂无节点指标" :image-size="60" />
        </div>
      </div>

      <div class="card" style="margin-top: 16px">
        <h3 style="margin: 0 0 12px">用户活跃时长 Top</h3>
        <el-table :data="top" stripe>
          <el-table-column prop="key" label="用户" />
          <el-table-column label="活跃时长" width="180">
            <template #default="{ row }">{{ (row.value / 3600).toFixed(1) }} h</template>
          </el-table-column>
          <el-table-column label="占比" min-width="220">
            <template #default="{ row }">
              <el-progress
                :percentage="
                  top[0]?.value ? Math.round((row.value / top[0].value) * 100) : 0
                "
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
