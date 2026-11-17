import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true, title: '登录' },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/RegisterView.vue'),
      meta: { public: true, title: '注册' },
    },
    {
      path: '/',
      component: () => import('@/layouts/AppLayout.vue'),
      children: [
        { path: '', name: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '工作台' } },
        { path: 'usage', name: 'usage', component: () => import('@/views/UsageView.vue'), meta: { title: '用量' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '设置' } },
        {
          path: 'admin/images',
          name: 'admin-images',
          component: () => import('@/views/admin/ImagesView.vue'),
          meta: { admin: true, title: 'DSH 版本' },
        },
        {
          path: 'admin/users',
          name: 'admin-users',
          component: () => import('@/views/admin/UsersView.vue'),
          meta: { admin: true, title: '用户管理' },
        },
        {
          path: 'admin/plans',
          name: 'admin-plans',
          component: () => import('@/views/admin/PlansView.vue'),
          meta: { admin: true, title: '套餐管理' },
        },
        {
          path: 'admin/invites',
          name: 'admin-invites',
          component: () => import('@/views/admin/InvitesView.vue'),
          meta: { admin: true, title: '邀请码' },
        },
        {
          path: 'admin/settings',
          name: 'admin-settings',
          component: () => import('@/views/admin/SystemSettingsView.vue'),
          meta: { admin: true, title: '系统设置' },
        },
        {
          path: 'admin/audit',
          name: 'admin-audit',
          component: () => import('@/views/admin/AuditView.vue'),
          meta: { admin: true, title: '审计日志' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.ready) {
    await auth.bootstrap()
  }
  if (to.meta.public) {
    if (auth.user && (to.name === 'login' || to.name === 'register')) {
      return { name: 'dashboard' }
    }
    return true
  }
  if (!auth.user) {
    return { name: 'login', query: { next: to.fullPath } }
  }
  if (to.meta.admin && !auth.isAdmin) {
    return { name: 'dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? '优墨云舱'
  document.title = `${title} · 优墨云舱`
})

export default router
