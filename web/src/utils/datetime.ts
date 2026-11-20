/**
 * Date/time formatting.
 *
 * The API always sends ISO-8601 in UTC with a trailing `Z`. Slicing those strings
 * (`.replace('T', ' ').slice(0, 16)`) shows UTC verbatim, which is 8 hours behind
 * in Asia/Shanghai — the console read "上次检查 03:42" at 11:42 local time.
 *
 * Everything user-facing goes through here so the browser's own timezone wins.
 */

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `YYYY-MM-DD HH:mm` in local time; `{ seconds: true }` appends `:ss`. */
export function formatDateTime(
  value: string | null | undefined,
  options: { seconds?: boolean } = {},
): string {
  const date = parse(value)
  if (!date) return value ? String(value) : '—'
  const base = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
  return options.seconds ? `${base}:${pad(date.getSeconds())}` : base
}

/** `MM-DD HH:mm`, for chart axes where the year is noise. */
export function formatShortDateTime(value: string | null | undefined): string {
  const date = parse(value)
  if (!date) return value ? String(value) : '—'
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`
}

/** `MM-DD HH:mm` for `YYYY-MM-DDTHH:mm` buckets that may lack a `Z`. */
export function formatBucket(value: string): string {
  const date = parse(
    value.includes('T') && !/Z|[+-]\d{2}:?\d{2}$/.test(value) ? `${value}Z` : value,
  )
  return date ? formatShortDateTime(date.toISOString()) : value
}
