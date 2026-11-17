import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, setCsrfToken } from '@/api/client'
import type { Quota, User } from '@/types'

interface AuthPayload {
  user: User
  quota?: Quota
  csrf_token: string
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const quota = ref<Quota | null>(null)
  const ready = ref(false)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function apply(payload: AuthPayload) {
    user.value = payload.user
    if (payload.quota) quota.value = payload.quota
    setCsrfToken(payload.csrf_token)
  }

  function clear() {
    user.value = null
    quota.value = null
    setCsrfToken('')
  }

  async function bootstrap() {
    try {
      const data = await api.get<AuthPayload>('/auth/me')
      apply(data)
    } catch {
      clear()
    } finally {
      ready.value = true
    }
  }

  async function login(username: string, password: string, remember: boolean) {
    const data = await api.post<AuthPayload>('/auth/login', { username, password, remember })
    apply(data)
    ready.value = true
    return data
  }

  async function register(payload: {
    code: string
    username: string
    password: string
    email?: string
    display_name?: string
  }) {
    const data = await api.post<AuthPayload>('/auth/invite/register', payload)
    apply(data)
    ready.value = true
    return data
  }

  async function logout() {
    try {
      await api.post('/auth/logout')
    } finally {
      clear()
      ready.value = true
    }
  }

  async function refresh() {
    const data = await api.get<AuthPayload>('/auth/me')
    apply(data)
  }

  return { user, quota, ready, isAdmin, bootstrap, login, register, logout, refresh, clear }
})
