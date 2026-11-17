<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'

interface UsagePayload {
  summary: {
    active_seconds: number
    cpu_seconds: number
    mem_mb_seconds: number
    disk_bytes_max: number
  }
  series: Array<Record<string, number | string>>
  note?: string
}

const loading = ref(true)
const usage = ref<UsagePayload | null>(null)

async function load() {
  loading.value = true
  try {
    usage.value = await api.get<UsagePayload>('/me/usage')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1>用量</h1>
        <p>活跃时长、CPU 核时与内存消耗，按小时聚合</p>
      </div>
    </div>

    <div class="grid grid-4">
      <div class="card stat-card">
        <div class="label">活跃时长</div>
        <div class="value">{{ ((usage?.summary.active_seconds ?? 0) / 3600).toFixed(1) }} h</div>
        <div class="hint">仅有请求/网络活动时计费</div>
      </div>
      <div class="card stat-card">
        <div class="label">CPU 核时</div>
        <div class="value">{{ ((usage?.summary.cpu_seconds ?? 0) / 3600).toFixed(1) }}</div>
        <div class="hint">按 30 秒采样折算</div>
      </div>
      <div class="card stat-card">
        <div class="label">内存 GB·时</div>
        <div class="value">
          {{ ((usage?.summary.mem_mb_seconds ?? 0) / 1024 / 3600).toFixed(1) }}
        </div>
        <div class="hint">峰值不参与计费</div>
      </div>
      <div class="card stat-card">
        <div class="label">磁盘占用</div>
        <div class="value">
          {{ (((usage?.summary.disk_bytes_max ?? 0) / 1024 / 1024 / 1024) || 0).toFixed(1) }} GB
        </div>
        <div class="hint">每小时统计</div>
      </div>
    </div>

    <div class="card" style="margin-top: 16px">
      <el-empty description="用量图表将在 M3 上线（需要实例与计量采集）" :image-size="110" />
      <p class="muted" style="text-align: center; font-size: 13px">
        当前接口已就绪，返回空序列；M3 接入 Caddy 访问日志与 Docker stats 后自动出数。
      </p>
    </div>
  </div>
</template>
