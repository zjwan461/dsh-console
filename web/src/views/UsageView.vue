<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Coin, Connection, Cpu, Timer } from '@element-plus/icons-vue'
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
        <div class="stat-top">
          <span class="label">活跃时长</span>
          <span class="chip"
            ><el-icon><Timer /></el-icon
          ></span>
        </div>
        <div class="value">{{ hours(data?.summary.active_seconds ?? 0) }}<small>h</small></div>
        <div class="hint">仅有请求 / 网络活动时扣减</div>
      </div>
      <div class="card stat-card">
        <div class="stat-top">
          <span class="label">CPU 核时</span>
          <span class="chip slate"
            ><el-icon><Cpu /></el-icon
          ></span>
        </div>
        <div class="value">{{ hours(data?.summary.cpu_seconds ?? 0) }}</div>
        <div class="hint">30 秒采样折算</div>
      </div>
      <div class="card stat-card">
        <div class="stat-top">
          <span class="label">内存 GB·时</span>
          <span class="chip bronze"
            ><el-icon><Coin /></el-icon
          ></span>
        </div>
        <div class="value">
          {{ ((data?.summary.mem_mb_seconds ?? 0) / 1024 / 3600).toFixed(1) }}
        </div>
        <div class="hint">按实际占用累计</div>
      </div>
      <div class="card stat-card">
        <div class="stat-top">
          <span class="label">网络</span>
          <span class="chip"
            ><el-icon><Connection /></el-icon
          ></span>
        </div>
        <div class="value">
          {{
            (
              ((data?.summary.net_rx_bytes ?? 0) + (data?.summary.net_tx_bytes ?? 0)) /
              1024 /
              1024
            ).toFixed(1)
          }}
          <small>MB</small>
        </div>
        <div class="hint">出站 + 入站</div>
      </div>
    </div>

    <div class="card" style="margin-top: 18px">
      <div class="section-head">
        <h3>活跃时长</h3>
        <span class="hint">单位：小时</span>
      </div>
      <LineChart
        v-if="data && data.series.length"
        :x="labels"
        :series="[
          {
            name: '活跃小时',
            data: data.series.map((s) => +(s.active_seconds / 3600).toFixed(2)),
            color: '#2f6f63',
            area: true,
          },
        ]"
        height="300px"
      />
      <div v-else class="blank" style="margin-top: 14px">
        <strong>暂无用量数据</strong>
        实例运行后，worker 会每小时把采样落库
      </div>
    </div>

    <div class="grid grid-2" style="margin-top: 18px">
      <div class="card">
        <div class="section-head">
          <h3>CPU 核时</h3>
          <span class="hint">单位：核·小时</span>
        </div>
        <LineChart
          v-if="data && data.series.length"
          :x="labels"
          :series="[
            {
              name: 'CPU 核时',
              data: data.series.map((s) => +(s.cpu_seconds / 3600).toFixed(3)),
              color: '#5b7f9e',
            },
          ]"
          height="220px"
        />
        <div v-else class="blank" style="margin-top: 14px">
          <strong>暂无数据</strong>
          运行实例后开始累计
        </div>
      </div>
      <div class="card">
        <div class="section-head">
          <h3>内存 GB·时</h3>
          <span class="hint">按实际占用</span>
        </div>
        <LineChart
          v-if="data && data.series.length"
          :x="labels"
          :series="[
            {
              name: '内存 GB·时',
              data: data.series.map((s) => +(s.mem_mb_seconds / 1024 / 3600).toFixed(3)),
              color: '#a9713f',
            },
          ]"
          height="220px"
        />
        <div v-else class="blank" style="margin-top: 14px">
          <strong>暂无数据</strong>
          运行实例后开始累计
        </div>
      </div>
    </div>
  </div>
</template>
