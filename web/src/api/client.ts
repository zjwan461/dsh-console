export class ApiError extends Error {
  code: string
  status: number
  details: Record<string, unknown>

  constructor(code: string, message: string, status: number, details: Record<string, unknown> = {}) {
    super(message)
    this.code = code
    this.status = status
    this.details = details
  }
}

let csrfToken = ''

export function setCsrfToken(token: string) {
  csrfToken = token
}

export function getCsrfToken() {
  return csrfToken
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  headers?: Record<string, string>
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { ...options.headers }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  if (csrfToken && method !== 'GET') {
    headers['X-CSRF-Token'] = csrfToken
  }

  const response = await fetch(`/api/v1${path}`, {
    method,
    credentials: 'include',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  if (response.status === 204) {
    return undefined as T
  }

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const error = payload?.error ?? {}
    if (response.status === 401 && !path.startsWith('/auth/login')) {
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    throw new ApiError(
      error.code ?? 'INTERNAL_ERROR',
      error.message ?? `请求失败（${response.status}）`,
      response.status,
      error.details ?? {},
    )
  }
  return payload?.data as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '未知错误'
}
