<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Box,
  DataAnalysis,
  Document,
  HomeFilled,
  Setting,
  Tickets,
  User as UserIcon,
  UserFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { api } from '@/api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const consoleName = ref('优墨云舱')

const activeMenu = computed(() => route.path)

const adminMenus = [
  { path: '/admin/users', label: '用户', icon: UserFilled },
  { path: '/admin/plans', label: '套餐', icon: Tickets },
  { path: '/admin/images', label: '版本', icon: Box },
  { path: '/admin/invites', label: '邀请码', icon: Document },
  { path: '/admin/audit', label: '审计', icon: DataAnalysis },
  { path: '/admin/settings', label: '系统设置', icon: Setting },
]

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
})
</script>

<template>
  <el-container class="shell">
    <el-aside width="220px" class="aside">
      <div class="brand">
        <div class="brand-mark">D</div>
        <div>
          <div class="brand-name">{{ consoleName }}</div>
          <div class="brand-sub">DeepSeek Harness</div>
        </div>
      </div>

      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item index="/">
          <el-icon><HomeFilled /></el-icon>
          <span>工作台</span>
        </el-menu-item>
        <el-menu-item index="/usage">
          <el-icon><DataAnalysis /></el-icon>
          <span>用量</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
        <template v-if="auth.isAdmin">
          <div class="menu-group">管理员</div>
          <el-menu-item v-for="item in adminMenus" :key="item.path" :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ route.meta.title ?? '工作台' }}</div>
        <el-dropdown trigger="click" @command="onCommand">
          <span class="user-chip">
            <el-avatar :size="28" class="avatar">
              {{ (auth.user?.display_name || auth.user?.username || '?').slice(0, 1) }}
            </el-avatar>
            <span class="user-name">{{ auth.user?.display_name || auth.user?.username }}</span>
            <el-tag v-if="auth.isAdmin" size="small" type="warning" effect="plain">管理员</el-tag>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="settings">
                <el-icon><UserIcon /></el-icon> 个人设置
              </el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.shell {
  min-height: 100vh;
}

.aside {
  background: #0f172a;
  color: #cbd5e1;
  display: flex;
  flex-direction: column;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 18px 16px;
}

.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, #4d6bfe, #7c9bff);
  display: grid;
  place-items: center;
  font-weight: 800;
  color: #fff;
}

.brand-name {
  color: #f8fafc;
  font-weight: 650;
  font-size: 15px;
}

.brand-sub {
  color: #64748b;
  font-size: 11px;
  margin-top: 2px;
}

.menu {
  border-right: none;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #cbd5e1;
  --el-menu-hover-bg-color: rgba(148, 163, 184, 0.12);
  --el-menu-active-color: #ffffff;
}

.menu :deep(.el-menu-item.is-active) {
  background: rgba(77, 107, 254, 0.28);
  border-radius: 8px;
  margin: 0 10px;
}

.menu :deep(.el-menu-item) {
  margin: 2px 10px;
  border-radius: 8px;
  height: 42px;
}

.menu-group {
  padding: 18px 20px 6px;
  font-size: 11px;
  letter-spacing: 0.08em;
  color: #64748b;
  text-transform: uppercase;
}

.header {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid var(--dsh-border);
}

.header-title {
  font-weight: 600;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  outline: none;
}

.avatar {
  background: #4d6bfe;
  color: #fff;
  font-weight: 600;
}

.user-name {
  font-size: 14px;
}

.main {
  padding: 0;
  background: var(--dsh-bg);
}

@media (max-width: 860px) {
  .aside {
    width: 72px !important;
  }
  .brand-name,
  .brand-sub,
  .menu span,
  .menu-group {
    display: none;
  }
  .brand {
    justify-content: center;
    padding: 16px 0;
  }
}
</style>
