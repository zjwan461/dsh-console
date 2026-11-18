<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bell,
  Box,
  Camera,
  ChatDotRound,
  Connection,
  Cpu,
  Document,
  FolderOpened,
  HomeFilled,
  Monitor,
  PriceTag,
  Setting,
  Star,
  SwitchButton,
  Tickets,
  TrendCharts,
  UserFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { api } from '@/api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const consoleName = ref('优墨云舱')
const runtimeOk = ref<boolean | null>(null)

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => (route.meta.title as string) ?? '工作台')
const isAdminArea = computed(() => route.path.startsWith('/admin'))

const workspaceMenus = [
  { path: '/', label: '工作台', icon: HomeFilled },
  { path: '/usage', label: '用量', icon: TrendCharts },
  { path: '/snapshots', label: '快照', icon: Camera },
  { path: '/images', label: '我的镜像', icon: Box },
  { path: '/files', label: '文件', icon: FolderOpened },
  { path: '/tickets', label: '工单', icon: ChatDotRound },
  { path: '/settings', label: '设置', icon: Setting },
]

const adminMenus = [
  { path: '/admin/metrics', label: '监控', icon: Monitor },
  { path: '/admin/instances', label: '实例', icon: Cpu },
  { path: '/admin/users', label: '用户', icon: UserFilled },
  { path: '/admin/plans', label: '套餐', icon: PriceTag },
  { path: '/admin/images', label: '版本', icon: Box },
  { path: '/admin/skills', label: '技能', icon: Star },
  { path: '/admin/plugins', label: '插件', icon: Connection },
  { path: '/admin/invites', label: '邀请码', icon: Tickets },
  { path: '/admin/tickets', label: '工单', icon: ChatDotRound },
  { path: '/admin/announcements', label: '公告', icon: Bell },
  { path: '/admin/audit', label: '审计', icon: Document },
  { path: '/admin/settings', label: '系统设置', icon: Setting },
]

const initial = computed(() =>
  (auth.user?.display_name || auth.user?.username || '?').slice(0, 1).toUpperCase(),
)

async function onCommand(command: string) {
  if (command === 'logout') {
    await auth.logout()
    router.push({ name: 'login' })
  } else if (command === 'settings') {
    router.push({ name: 'settings' })
  }
}

onMounted(async () => {
  try {
    const info = await api.get<{ name: string }>('/system/info')
    if (info.name) consoleName.value = info.name
  } catch {
    // non-blocking: the default name is fine
  }
  // `/system/health` is admin-only, so only ask for it when the user can see it.
  if (auth.isAdmin) {
    try {
      const health = await api.get<{ docker: { ok: boolean } }>('/system/health')
      runtimeOk.value = health.docker.ok
    } catch {
      runtimeOk.value = false
    }
  }
})
</script>

<template>
  <div class="shell">
    <aside class="aside">
      <router-link to="/" class="brand">
        <span class="brand-mark">墨</span>
        <span class="brand-text">
          <span class="brand-name">{{ consoleName }}</span>
          <span class="brand-sub">DeepSeek Harness</span>
        </span>
      </router-link>

      <nav class="nav">
        <div class="nav-label">工作区</div>
        <el-menu :default-active="activeMenu" router class="menu">
          <el-menu-item v-for="item in workspaceMenus" :key="item.path" :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </el-menu-item>
        </el-menu>

        <template v-if="auth.isAdmin">
          <div class="nav-label">
            管理
            <span class="nav-label-tag">ADMIN</span>
          </div>
          <el-menu :default-active="activeMenu" router class="menu">
            <el-menu-item v-for="item in adminMenus" :key="item.path" :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
            </el-menu-item>
          </el-menu>
        </template>
      </nav>

      <el-dropdown trigger="click" class="account" @command="onCommand">
        <div class="account-card">
          <span class="avatar">{{ initial }}</span>
          <span class="account-text">
            <span class="account-name">{{ auth.user?.display_name || auth.user?.username }}</span>
            <span class="account-role">
              {{ auth.isAdmin ? '管理员' : '成员' }} · @{{ auth.user?.username }}
            </span>
          </span>
          <el-icon class="account-caret"><SwitchButton /></el-icon>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="settings">个人设置</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </aside>

    <div class="body">
      <header class="header">
        <div class="crumbs">
          <span class="crumb-root">{{ isAdminArea ? '管理' : '工作区' }}</span>
          <span class="crumb-sep">/</span>
          <span class="crumb-current">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <span v-if="runtimeOk !== null" class="runtime" :class="{ bad: !runtimeOk }">
            <span class="dot" />
            {{ runtimeOk ? '运行时正常' : '运行时异常' }}
          </span>
        </div>
      </header>

      <main class="main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  background: var(--paper);
}

/* ── sidebar ─────────────────────────────────────────────────────────── */

.aside {
  width: 244px;
  flex: 0 0 244px;
  background:
    radial-gradient(360px 220px at 20% 0%, rgba(47, 111, 99, 0.22), transparent 70%), var(--ink-900);
  color: #c6cfd8;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  border-right: 1px solid rgba(255, 255, 255, 0.04);
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 22px 20px 20px;
  color: inherit;
}

.brand-mark {
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: #f5f2ec;
  color: var(--ink-900);
  font-family: 'Songti SC', 'STSong', Georgia, serif;
  font-size: 19px;
  font-weight: 700;
  line-height: 1;
  box-shadow: inset 0 0 0 1px rgba(15, 21, 27, 0.08);
}

.brand-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.brand-name {
  color: #f3f1ec;
  font-weight: 600;
  font-size: 15px;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.brand-sub {
  color: #6d7c8a;
  font-size: 10.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-top: 3px;
}

.nav {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0 12px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.12) transparent;
}

.nav-label {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 22px 8px;
  font-size: 10.5px;
  letter-spacing: 0.14em;
  color: #64727f;
  text-transform: uppercase;
}

.nav-label-tag {
  font-size: 8.5px;
  letter-spacing: 0.1em;
  padding: 1px 5px;
  border-radius: 4px;
  color: #b99a72;
  border: 1px solid rgba(185, 154, 114, 0.32);
}

.menu {
  border-right: none;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #aeb9c4;
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.055);
  --el-menu-active-color: #ffffff;
  --el-menu-item-height: 38px;
}

.menu :deep(.el-menu-item) {
  margin: 1px 10px;
  border-radius: 8px;
  height: 38px;
  line-height: 38px;
  font-size: 13.5px;
  padding-left: 14px !important;
  position: relative;
}

.menu :deep(.el-menu-item .el-icon) {
  color: #7d8b98;
  margin-right: 10px;
  font-size: 16px;
  transition: color 0.15s ease;
}

.menu :deep(.el-menu-item:hover) {
  color: #eef2f5;
}

.menu :deep(.el-menu-item:hover .el-icon) {
  color: #cfd8e0;
}

.menu :deep(.el-menu-item.is-active) {
  background: rgba(255, 255, 255, 0.075);
  color: #fff;
  font-weight: 500;
}

.menu :deep(.el-menu-item.is-active::before) {
  content: '';
  position: absolute;
  left: 0;
  top: 10px;
  bottom: 10px;
  width: 2.5px;
  border-radius: 3px;
  background: #7fbfae;
}

.menu :deep(.el-menu-item.is-active .el-icon) {
  color: #9ed3c4;
}

/* ── account ─────────────────────────────────────────────────────────── */

.account {
  padding: 12px 12px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  cursor: pointer;
}

.account-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 10px;
  transition: background 0.15s ease;
}

.account-card:hover {
  background: rgba(255, 255, 255, 0.06);
}

.avatar {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: rgba(127, 191, 174, 0.16);
  color: #a9d6c8;
  font-weight: 600;
  font-size: 13px;
}

.account-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.account-name {
  color: #eef2f5;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.account-role {
  color: #71808e;
  font-size: 11px;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.account-caret {
  color: #5f6e7c;
  font-size: 14px;
}

/* ── main column ─────────────────────────────────────────────────────── */

.body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.header {
  height: 56px;
  flex: 0 0 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 32px;
  background: rgba(245, 242, 236, 0.82);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 10;
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--muted);
}

.crumb-sep {
  color: #c4bcae;
}

.crumb-current {
  color: var(--text);
  font-weight: 500;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
}

.runtime {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #4d7a63;
  background: #eef5f0;
  border: 1px solid #dcebe1;
  padding: 4px 10px;
  border-radius: 999px;
}

.runtime .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4f8a68;
  box-shadow: 0 0 0 3px rgba(79, 138, 104, 0.14);
}

.runtime.bad {
  color: var(--danger);
  background: var(--danger-soft);
  border-color: #efd6d2;
}

.runtime.bad .dot {
  background: var(--danger);
  box-shadow: 0 0 0 3px rgba(169, 74, 63, 0.14);
}

.main {
  flex: 1;
  min-width: 0;
}

@media (max-width: 1080px) {
  .aside {
    width: 76px;
    flex-basis: 76px;
  }
  .brand {
    justify-content: center;
    padding: 18px 0 14px;
  }
  .brand-text,
  .nav-label,
  .account-text,
  .account-caret {
    display: none;
  }
  .menu :deep(.el-menu-item) {
    justify-content: center;
    padding-left: 0 !important;
    margin: 2px 12px;
  }
  .menu :deep(.el-menu-item span) {
    display: none;
  }
  .menu :deep(.el-menu-item .el-icon) {
    margin-right: 0;
  }
  .menu :deep(.el-menu-item.is-active::before) {
    display: none;
  }
  .account {
    display: flex;
    justify-content: center;
    padding: 12px 0 16px;
  }
  .account-card {
    justify-content: center;
    padding: 6px;
  }
  .header {
    padding: 0 20px;
  }
}

@media (max-width: 680px) {
  .runtime {
    display: none;
  }
}
</style>
