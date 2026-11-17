export interface User {
  id: number
  username: string
  display_name: string
  email?: string | null
  role: 'user' | 'admin'
  status: 'active' | 'disabled' | 'pending'
  subdomain: string
  must_change_password?: boolean
  created_at?: string
  last_login_at?: string | null
}

export interface Quota {
  plan: string | null
  plan_code?: string | null
  monthly_minutes: number
  extra_minutes: number
  used_minutes: number
  remaining_minutes: number
  total_minutes: number
  cpu_cores: number
  memory_mb: number
  disk_gb: number
  cycle_start: string
  cycle_end: string
  max_snapshots?: number
  max_user_images?: number
}

export interface Overview {
  user: User
  instance: null | Record<string, unknown>
  quota: Quota
  base_domain?: string
  announcements: Array<{ id: number; title: string; level: string; body_md: string }>
  unread_tickets: number
  features: Record<string, boolean>
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface Plan {
  id: number
  code: string
  name: string
  description: string
  cpu_cores: number
  memory_mb: number
  disk_gb: number
  monthly_minutes: number
  max_snapshots: number
  max_user_images: number
  gpu_count: number
  is_default: boolean
  enabled: boolean
  sort_order: number
}

export interface Invite {
  id: number
  code: string
  role: 'user' | 'admin'
  max_uses: number
  used_count: number
  status: 'active' | 'expired' | 'exhausted' | 'revoked'
  note: string
  expires_at: string | null
  created_at: string
}

export interface AdminUser {
  id: number
  username: string
  display_name: string
  email: string | null
  role: 'user' | 'admin'
  status: 'active' | 'disabled' | 'pending'
  subdomain: string
  plan: { id: number; name: string; code: string } | null
  quota: {
    used_minutes: number
    remaining_minutes: number
    total_minutes: number
    cpu_cores: number | null
    memory_mb: number | null
    disk_gb: number | null
  }
  created_at: string
  last_login_at: string | null
  disabled_reason: string | null
}

export type InstanceStatus =
  | 'provisioning'
  | 'starting'
  | 'running'
  | 'sleeping'
  | 'waking'
  | 'stopping'
  | 'stopped'
  | 'error'
  | 'deleting'

export interface Instance {
  id: number
  status: InstanceStatus
  status_reason: string | null
  error: string | null
  health: string
  dsh_version: string
  image_id: number
  subdomain: string
  url: string
  spec: { cpu_cores: number; memory_mb: number; disk_gb: number }
  idle_timeout_seconds: number
  last_active_at: string | null
  last_started_at: string | null
  last_stopped_at: string | null
  created_at: string
  updated_at: string
}

export interface InstanceOptions {
  plans: Array<{
    id: number
    code: string
    name: string
    cpu_cores: number
    memory_mb: number
    disk_gb: number
    monthly_minutes: number
    is_default: boolean
  }>
  images: Array<{
    id: number
    version: string
    size_bytes: number
    is_default: boolean
    changelog: string | null
  }>
  default_image_id: number | null
  quota: Quota
  capacity: {
    cpu_cores: number
    cpu_committed: number
    cpu_limit: number
    mem_total_mb: number
    mem_committed_mb: number
    mem_limit_mb: number
    disk_total_gb: number
    disk_committed_gb: number
    disk_limit_gb: number
    can_admit: { cpu: boolean; memory: boolean; disk: boolean }
  }
  can_create: boolean
}

export interface InstanceEventItem {
  id: number
  type: string
  from: string | null
  to: string | null
  message: string
  created_at: string
}

export interface AdminQuota {
  user_id: number
  username: string
  plan: string | null
  plan_id: number | null
  monthly_minutes: number
  extra_minutes: number
  used_minutes: number
  remaining_minutes: number
  total_minutes: number
  cpu_cores: number
  memory_mb: number
  disk_gb: number
  cycle_start: string
  cycle_end: string
  extra_seconds: number
  seconds_used: number
}

export interface AuditEntry {
  id: number
  actor_id: number | null
  actor_name: string
  action: string
  target_type: string
  target_id: string
  ip: string | null
  detail: Record<string, unknown> | null
  request_id: string | null
  created_at: string
}

export interface Settings {
  base_domain: string
  console_name: string
  default_plan_id: number | null
  default_idle_minutes: number
  allow_user_version_switch: boolean
  allow_user_self_key: boolean
  snapshot_retention_days: number
  deleted_user_retention_days: number
  upload_max_mb: number
  admin_terminal_enabled: boolean
  maintenance_mode: boolean
  maintenance_message: string
  [key: string]: unknown
}
