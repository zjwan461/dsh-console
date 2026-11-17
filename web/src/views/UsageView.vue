<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import LineChart from '@/components/LineChart.vue'

interface UsagePayload {
  summary: {
    active_seconds: number
    cpu_seconds: number
    mem_mb_seconds: number
    disk_bytes_max: number
    net_rx_bytes: number
    net_tx_bytes: number
  }
  series: Array<{
    ts: string
    active_seconds: number
    cpu_seconds: number
    mem_mb_seconds: number
    net_rx_bytes: number
    net_tx_bytes: number
  }>
}

const loading = ref(true)
const range = ref<'24h' | '7d' | '30d'>('24h')
const granularity = computed(() => (range.value === '24h' ? 'hour' : 'day'))
const data = ref<UsagePayload | null>(null)

function fromDate() {
  const now = new Date()
  const hours = range.value === '24h' ? 24 : range.value === '7d' ? 24 * 7 : 24 * 30
  return new Date(now.getTime() - hours * 3600 * 1000).toISOString()
}

async function load() {
  loading.value = true
  try {
    const now = new Date().toISOString()
    data.value = await api.get<UsagePayload>(
      `/me/usage?from=${encodeURIComponent(fromDate())}&to=${encodeURIComponent(now)}&granularity=${granularity.value}`,
    )
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function setRange(value: '24h' | '7d' | '30d') {
  range.value = value
  load()
}

function hours(seconds: number) {
  return (seconds / 3600).toFixed(1)
}

const labels = computed(() =>
  (data.value?.series ?? []).map((item) =>
    item.ts.includes('T') ? item.ts.slice(5, 16).replace('T', ' ') : item.ts.slice(5),
  ),
)

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1>用量</h1>
        <p>活跃时长、CPU 核时与内存消耗，按小时/天聚合</p>
      </div>
      <el-radio-group v-model="range" @change="setRange(range)">
        <el-radio-button value="24h">24 小时</el-radio-button>
        <el-radio-button value="7d">7 天</el-radio-button>
        <el-radio-button value="30d">30 天</el-radio-button>
      </el-radio-group>
    </div>

    <div class="grid grid-4">
      <div class="card stat-card">
        <div class="label">活跃时长</div>
        <div class="value">{{ hours(data?.summary.active_seconds ?? 0) }} h</div>
        <div class="hint">仅有请求/网络活动时扣减</div>
      </div>
      <div class="card stat-card">
        <div class="label">CPU 核时</div>
        <div class="value">{{ hours(data?.summary.cpu_seconds ?? 0) }}</div>
        <div class="hint">30 秒采样折算</div>
      </div>
      <div class="card stat-card">
        <div class="label">内存 GB·时</div>
        <div class="value">
          {{ ((data?.summary.mem_mb_seconds ?? 0) / 1024 / 3600).toFixed(1) }}
        </div>
        <div class="hint">按实际占用累计</div>
      </div>
      <div class="card stat-card">
        <div class="label">网络</div>
        <div class="value">
          {{ (((data?.summary.net_rx_bytes ?? 0) + (data?.summary.net_tx_bytes ?? 0)) / 1024 / 1024).toFixed(1) }}
          MB
        </div>
        <div class="hint">出站 + 入站</div>
      </div>
    </div>

    <div class="card" style="margin-top: 16px">
      <h3 style="margin: 0 0 8px">活跃时长（小时）</h3>
      <LineChart
        v-if="data && data.series.length"
        :x="labels"
        :series="[
          {
            name: '活跃小时',
            data: data.series.map((s) => +(s.active_seconds / 3600).toFixed(2)),
            color: '#4d6bfe',
            area: true,
          },
        ]"
        height="300px"
      />
      <el-empty v-else description="暂无用量数据（运行实例后由 worker 每小时落库）" :image-size="100" />
    </div>

    <div class="grid grid-2" style="margin-top: 16px">
      <div class="card">
        <h3 style="margin: 0 0 8px">CPU 核时</h3>
        <LineChart
          v-if="data && data.series.length"
          :x="labels"
          :series="[
            {
              name: 'CPU 核时',
              data: data.series.map((s) => +(s.cpu_seconds / 3600).toFixed(3)),
              color: '#10b981',
            },
          ]"
          height="220px"
        />
        <el-empty v-else description="暂无数据" :image-size="60" />
      </div>
      <div class="card">
        <h3 style="margin: 0 0 8px">内存 GB·时</h3>
        <LineChart
          v-if="data && data.series.length"
          :x="labels"
          :series="[
            {
              name: '内存 GB·时',
              data: data.series.map((s) => +(s.mem_mb_seconds / 1024 / 3600).toFixed(3)),
              color: '#f59e0b',
            },
          ]"
          height="220px"
        />
        <el-empty v-else description="暂无数据" :image-size="60" />
      </div>
    </div>
  </div>
</template>
