<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { Overview } from '@/types'

const loading = ref(true)
const overview = ref<Overview | null>(null)

function hours(minutes: number) {
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return h > 0 ? `${h} 小时 ${m} 分` : `${m} 分钟`
}

function gb(mb: number) {
  return `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB`
}

async function load() {
  loading.value = true
  try {
    overview.value = await api.get<Overview>('/me/overview')
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
        <h1>工作台</h1>
        <p>欢迎回来，{{ overview?.user.display_name || overview?.user.username }}</p>
      </div>
      <el-button :disabled="true">创建我的云电脑（M2）</el-button>
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
          <div class="label">CPU</div>
          <div class="value">{{ overview.quota.cpu_cores }} 核</div>
          <div class="hint">套餐：{{ overview.quota.plan ?? '未分配' }}</div>
        </div>
        <div class="card stat-card">
          <div class="label">内存</div>
          <div class="value">{{ gb(overview.quota.memory_mb) }}</div>
          <div class="hint">按套餐限制</div>
        </div>
        <div class="card stat-card">
          <div class="label">磁盘</div>
          <div class="value">{{ overview.quota.disk_gb }} GB</div>
          <div class="hint">数据目录配额</div>
        </div>
      </div>

      <div class="grid grid-2" style="margin-top: 16px">
        <div class="card">
          <div class="section-head">
            <h3>我的实例</h3>
            <el-tag type="info" effect="plain">尚未创建</el-tag>
          </div>
          <el-empty description="实例管理将在 M2 开放：创建、启停、一键进入、快照" :image-size="90" />
          <div class="instance-url mono">
            https://{{ overview.user.subdomain }}.{{ overview.base_domain ?? 'dsh.example.com' }}
          </div>
        </div>

        <div class="card">
          <div class="section-head">
            <h3>公告</h3>
          </div>
          <el-empty v-if="overview.announcements.length === 0" description="暂无公告" :image-size="70" />
          <div v-for="item in overview.announcements" :key="item.id" class="announcement">
            <el-tag size="small" :type="item.level === 'warning' ? 'warning' : 'info'">
              {{ item.level }}
            </el-tag>
            <span>{{ item.title }}</span>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top: 16px">
        <div class="section-head">
          <h3>配额周期</h3>
        </div>
        <el-progress
          :percentage="
            overview.quota.total_minutes
              ? Math.min(
                  100,
                  Math.round((overview.quota.used_minutes / overview.quota.total_minutes) * 100),
                )
              : 0
          "
          :stroke-width="12"
        />
        <p class="muted" style="margin-top: 10px; font-size: 13px">
          周期 {{ overview.quota.cycle_start }} 至 {{ overview.quota.cycle_end }}，按活跃时长扣减。
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.section-head h3 {
  margin: 0;
  font-size: 15px;
}

.instance-url {
  margin-top: 8px;
  color: var(--dsh-muted);
  font-size: 12px;
  word-break: break-all;
}

.announcement {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--dsh-border);
}
</style>
