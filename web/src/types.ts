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
