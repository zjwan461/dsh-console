import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'
import type { Instance, InstanceEventItem, InstanceOptions, InstanceStatus } from '@/types'

const TRANSITIONAL: InstanceStatus[] = [
  'provisioning',
  'starting',
  'waking',
  'stopping',
  'deleting',
]

export type InstanceAction = 'create' | 'start' | 'stop' | 'restart' | 'sleep' | 'remove'

interface LaunchResult {
  state: string
  url?: string
  retry_after_ms?: number
}

export const useInstanceStore = defineStore('instance', () => {
  const instance = ref<Instance | null>(null)
  const options = ref<InstanceOptions | null>(null)
  const events = ref<InstanceEventItem[]>([])
  const loading = ref(false)
  const busy = ref(false)
  // Which action is in flight, so each button can spin on its own instead of
  // every button going dead with no feedback.
  const pendingAction = ref<InstanceAction | null>(null)
  const pendingLaunch = ref(false)
  const lastError = ref('')
  let timer: number | null = null

  const isTransitional = computed(
    () => !!instance.value && TRANSITIONAL.includes(instance.value.status),
  )
  const isRunning = computed(() => instance.value?.status === 'running')

  async function load() {
    const data = await api.get<Instance | null>('/instances/me')
    instance.value = data
    if (data) {
      await loadEvents()
    } else {
      events.value = []
    }
    syncPolling()
    return data
  }

  async function loadEvents() {
    try {
      const data = await api.get<{ items: InstanceEventItem[] }>('/instances/me/events?limit=8')
      events.value = data.items
    } catch {
      // events are optional
    }
  }

  async function loadOptions() {
    options.value = await api.get<InstanceOptions>('/instances/me/options')
    return options.value
  }

  async function create(imageId: number | null, idleTimeoutSeconds?: number) {
    busy.value = true
    pendingAction.value = 'create'
    lastError.value = ''
    try {
      await api.post('/instances/me', {
        image_id: imageId ?? undefined,
        idle_timeout_seconds: idleTimeoutSeconds,
      })
      await load()
      await loadOptions().catch(() => undefined)
      startPolling()
    } finally {
      busy.value = false
      pendingAction.value = null
    }
  }

  async function act(action: 'start' | 'stop' | 'restart' | 'sleep') {
    busy.value = true
    pendingAction.value = action
    lastError.value = ''
    try {
      await api.post(`/instances/me/${action}`)
      await load()
      startPolling()
    } finally {
      busy.value = false
      pendingAction.value = null
    }
  }

  async function remove(username: string) {
    busy.value = true
    pendingAction.value = 'remove'
    try {
      await api.del(`/instances/me?confirm=${encodeURIComponent(username)}`)
      instance.value = null
      events.value = []
      await loadOptions().catch(() => undefined)
    } finally {
      busy.value = false
      pendingAction.value = null
    }
  }

  async function launch() {
    busy.value = true
    lastError.value = ''
    try {
      const result = await api.post<LaunchResult>('/instances/me/launch')
      if (result.state === 'running' && result.url) {
        window.location.href = result.url
        return
      }
      pendingLaunch.value = true
      await load()
      startPolling()
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : '打开失败'
      throw error
    } finally {
      busy.value = false
    }
  }

  function startPolling() {
    if (timer !== null) return
    timer = window.setInterval(async () => {
      try {
        await load()
        if (pendingLaunch.value && instance.value?.status === 'running') {
          pendingLaunch.value = false
          const result = await api.post<LaunchResult>('/instances/me/launch')
          if (result.url) window.location.href = result.url
        }
      } catch {
        // keep polling through transient errors
      }
    }, 2500)
  }

  function stopPolling() {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  function syncPolling() {
    if (instance.value && (TRANSITIONAL.includes(instance.value.status) || pendingLaunch.value)) {
      startPolling()
    } else if (!pendingLaunch.value) {
      stopPolling()
    }
  }

  return {
    instance,
    options,
    events,
    loading,
    busy,
    pendingAction,
    pendingLaunch,
    lastError,
    isTransitional,
    isRunning,
    load,
    loadEvents,
    loadOptions,
    create,
    act,
    remove,
    launch,
    startPolling,
    stopPolling,
  }
})
