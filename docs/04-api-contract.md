# 04 API 契约

> Base URL：`https://console.dsh.example.com/api/v1` · 所有请求/响应 `application/json; charset=utf-8`
> 时间：RFC 3339 UTC（如 `2026-11-17T01:02:03.456Z`）；ID 为整数（< 2^53）

## 1. 通用约定

### 1.1 响应包

成功：

```json
{ "data": { }, "request_id": "01JC8ZK3Q7X9V6M2T4R5W8N1B0" }
```

错误：

```json
{
  "error": {
    "code": "INSTANCE_BUSY",
    "message": "实例正在执行其他操作，请稍后重试",
    "details": { "status": "starting" }
  },
  "request_id": "01JC8ZK3Q7X9V6M2T4R5W8N1B0"
}
```

HTTP 状态码语义：`200` 成功、`201` 创建、`202` 已入队（异步）、`204` 无内容、`400` 参数、`401` 未登录、`403` 无权限、`404` 不存在、`409` 状态冲突、`413` 过大、`422` 校验失败、`429` 限流、`500` 服务端、`503` 依赖不可用（Docker/Caddy/DB）。

### 1.2 分页

请求：`?page=1&page_size=20`（`page_size` ≤ 100，默认 20）
响应：

```json
{ "data": { "items": [], "total": 137, "page": 1, "page_size": 20 }, "request_id": "..." }
```

### 1.3 认证

- 登录后服务端种 cookie：`dshc_sid=<43字符>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800`
- 写请求（POST/PUT/PATCH/DELETE）必须带 `X-CSRF-Token`（来自 `/auth/csrf` 或登录响应）。
- 未登录返回 `401 AUTH_SESSION_EXPIRED`；前端跳登录并带 `?next=`。
- 管理员接口额外校验 `role=admin`，否则 `403 FORBIDDEN`。

### 1.4 幂等与并发

- 创建类接口支持 `Idempotency-Key`（UUID），24 小时内同 key 返回同一结果。
- 生命周期接口对同一实例互斥；冲突返回 `409 INSTANCE_BUSY`。
- 异步接口返回 `202` + `{ "data": { "job_id": "...", "status": "starting" } }`，进度通过 SSE 或 `GET /jobs/{id}` 获取。

### 1.5 速率限制

| 范围 | 限制 |
|---|---|
| 登录 | 5 次/分钟/IP，10 次/小时/账号 |
| 注册 | 3 次/小时/IP |
| 普通 API | 300 次/分钟/用户 |
| 文件上传 | 10 次/分钟/用户，单文件 ≤ 1 GB |
| 管理员写操作 | 60 次/分钟/管理员 |

超限：`429`，`Retry-After` 头。

## 2. 认证

### POST `/auth/login`

```json
{ "username": "alice", "password": "********", "remember": true }
```

`200`：

```json
{ "data": {
    "user": { "id": 12, "username": "alice", "display_name": "Alice",
              "role": "user", "status": "active", "subdomain": "alice",
              "must_change_password": false },
    "csrf_token": "Yk3...",
    "expires_at": "2026-11-24T01:02:03Z"
}}
```

`401 AUTH_INVALID_CREDENTIALS` / `403 AUTH_ACCOUNT_DISABLED` / `429 AUTH_RATE_LIMITED`

### POST `/auth/logout`

`204`。删除当前会话。

### GET `/auth/me`

`200` 返回当前用户 + 配额摘要 + 实例摘要（同 `/me/overview` 的轻量版）。

### GET `/auth/csrf`

`200 { "data": { "csrf_token": "..." } }`（刷新页面后重新获取）

### POST `/auth/password`

```json
{ "old_password": "...", "new_password": "..." }
```

`204`；密码强度不足 `422 WEAK_PASSWORD`；改密后其他会话全部失效。

### POST `/auth/invite/register`（公开）

```json
{ "code": "INV-...", "username": "alice", "password": "...",
  "email": "a@example.com", "display_name": "Alice" }
```

`201` 同登录响应（直接登录）。错误：`INVITE_INVALID` / `INVITE_EXHAUSTED` / `USERNAME_TAKEN` / `USERNAME_RESERVED` / `USERNAME_INVALID`。

## 3. 当前用户

### GET `/me/overview`

```json
{ "data": {
  "user": { "id": 12, "username": "alice", "subdomain": "alice",
            "display_name": "Alice", "role": "user", "created_at": "..." },
  "instance": { "id": 7, "status": "running", "dsh_version": "0.2.1-rc.5",
                "url": "https://alice.dsh.example.com", "last_active_at": "..." },
  "quota": { "plan": "标准", "monthly_minutes": 7200, "extra_minutes": 0,
             "used_minutes": 1830, "remaining_minutes": 5370,
             "cpu_cores": 2, "memory_mb": 4096, "disk_gb": 50,
             "cycle_end": "2026-12-01" },
  "announcements": [ { "id": 3, "title": "11/20 维护", "level": "maintenance",
                       "body_md": "...", "publish_at": "..." } ],
  "unread_tickets": 1
}}
```

### GET `/me/usage?from=&to=&granularity=hour|day`

```json
{ "data": {
  "summary": { "active_seconds": 109800, "cpu_seconds": 219600,
               "mem_mb_seconds": 899481600, "disk_bytes_max": 32212254720,
               "net_rx_bytes": 1234, "net_tx_bytes": 5678 },
  "series": [
    { "ts": "2026-11-16T00:00:00Z", "active_seconds": 3600,
      "cpu_seconds": 7200, "mem_mb_seconds": 14745600 }
  ]
}}
```

默认 `from=now-24h`，最长 90 天。

### PATCH `/me/profile`

```json
{ "display_name": "Alice", "email": "a@example.com" }
```

### GET `/me/credentials`

```json
{ "data": {
  "platform_key_configured": true,
  "platform_base_url": "https://gateway.example.com/v1",
  "platform_model": "qwen2.5-32b-instruct",
  "user_key": { "configured": true, "hint": "…abcd",
                "base_url": "https://api.deepseek.com/v1",
                "model": "qwen2.5-32b-instruct", "updated_at": "..." },
  "effective": "user",
  "effective_base_url": "https://api.deepseek.com/v1",
  "effective_model": "qwen2.5-32b-instruct"
}}
```

`base_url` / `model` 为 `null` 表示官方默认地址与官方模型目录。`effective*` 是「用户自身优先、否则平台」的结果。

### PUT `/me/credentials`

```json
{ "api_key": "sk-...", "base_url": "https://gateway.example.com/v1",
  "model": "qwen2.5-32b-instruct" }
```

语义（字段均可省略）：

| 请求 | 行为 |
|---|---|
| `api_key` 非空 | 保存/替换 Key，并按需写入 `base_url` / `model` |
| `api_key` 空、`base_url`/`model` 非空 | 只改地址与模型，保留已有 Key |
| `base_url` / `model` 为 `""` | 恢复官方默认，Key 不变 |
| `clear_key: true` | 删除整条凭据，回退平台 Key |
| `api_key: ""` 且未传任何字段 | 兼容旧客户端的「清除」语义 |

`base_url` 必须是 `http(s)://` 完整地址，不能带查询参数；末尾 `/` 会被去掉。
`model` 只允许 `[A-Za-z0-9._:/-]`，最多 100 字符。
`200` 返回与 GET 相同的 `user_key` 结构，**不回显明文**；响应头 `X-Needs-Restart: true`。

生效方式：Key 注入容器环境变量（创建时固定）。端点/模型写入实例的
`DATA_ROOT/instances/<uid>/data/settings.yaml`：

- 只填地址 → `llm-deepseek.baseURL`，沿用 DeepSeek 模型目录；
- 地址 + 模型名 → 生成 `llm-pi-ai.providers.console-gateway`（`openai-completions`）
  并设置 `agent-default-model`，从而支持 qwen / gpt 等非 DeepSeek 模型。

重启实例时若配置哈希（环境 + 地址 + 模型 + 镜像/规格）变化，控制台会**重建容器**
（而不是复用旧环境）。

## 4. 实例（每人一个）

### GET `/instances/me`

`200`：实例对象（无实例时 `data: null`）

```json
{ "data": {
  "id": 7, "status": "running", "status_reason": null,
  "dsh_version": "0.2.1-rc.5", "image_id": 2,
  "subdomain": "alice", "url": "https://alice.dsh.example.com",
  "spec": { "cpu_cores": 2, "memory_mb": 4096, "disk_gb": 50 },
  "usage": { "disk_bytes": 8123456789 },
  "idle_timeout_seconds": 1800,
  "last_active_at": "2026-11-17T01:00:00Z",
  "created_at": "...", "updated_at": "..."
}}
```

### POST `/instances/me`

```json
{ "plan_id": 2, "image_id": 3, "idle_timeout_seconds": 1800 }
```

`202`：

```json
{ "data": { "id": 7, "status": "provisioning", "job_id": "job_..." } }
```

错误：`INSTANCE_EXISTS` / `PLAN_DISABLED` / `IMAGE_DISABLED` / `INSTANCE_QUOTA_MEMORY`（宿主内存不足）。

### POST `/instances/me/start` · `/stop` · `/restart` · `/sleep`

无请求体。`202 { "data": { "status": "starting", "job_id": "..." } }`
`409 INSTANCE_BUSY` / `INSTANCE_STATE_INVALID`（如对 `provisioning` 调 stop）。

### PATCH `/instances/me`

```json
{ "idle_timeout_seconds": 3600 }
```

### DELETE `/instances/me`

需要 `?confirm=alice`。`202`，`status=deleting`；数据保留 7 天（可配置）。

### GET `/instances/me/logs?tail=200`

`200 { "data": { "lines": ["..."], "truncated": false } }`（仅本人，最多 2000 行）

### GET `/instances/me/stats`

`200`（Docker stats 实时快照）：

```json
{ "data": { "cpu_percent": 12.5, "mem_used_bytes": 734003200,
            "mem_limit_bytes": 4294967296, "net_rx_bytes": 123, "net_tx_bytes": 456,
            "pids": 37, "read_at": "..." } }
```

## 5. 进入与唤醒

### POST `/instances/me/launch`

`200`（运行中）：

```json
{ "data": { "state": "running",
            "url": "https://console.dsh.example.com/go/{ticket}" } }
```

`202`（启动/唤醒中）：

```json
{ "data": { "state": "waking", "retry_after_ms": 1500 } }
```

### GET `/go/{ticket}`（浏览器导航，非 JSON）

- 校验一次性 ticket（60s，绑定 user+instance）。
- `302 Location: https://alice.dsh.example.com/?token={43字符}`
- 头：`Cache-Control: no-store`、`Referrer-Policy: no-referrer`。
- 失败：`410 TOKEN_UNAVAILABLE`（HTML 提示 + 重启按钮）。

### GET `/w/{wake_key}`（Caddy 内部转发，公开）

返回 HTML 唤醒页；`GET /w/{wake_key}/status` 返回：

```json
{ "data": { "state": "running", "redirect": "https://alice.dsh.example.com/" } }
```

## 6. 文件管理

根目录固定为实例 `/workspaces`；`path` 必须是以 `/` 开头的相对路径，`realpath` 后仍在根内。

### GET `/instances/me/files?path=/&page=&page_size=`

```json
{ "data": {
  "path": "/",
  "entries": [
    { "name": "notes", "type": "dir", "size": 0, "modified_at": "..." },
    { "name": "a.md", "type": "file", "size": 1234, "modified_at": "...",
      "mime": "text/markdown" }
  ],
  "total": 2, "quota": { "used_bytes": 8123456789, "limit_bytes": 53687091200 }
}}
```

### GET `/instances/me/files/download?path=/a.md`

`200` 流式下载；支持 `Range`；`Content-Disposition: attachment; filename*=UTF-8''...`

### POST `/instances/me/files/upload`（multipart）

字段：`path`（目录）、`file`。`201` 返回条目。`413 FILE_TOO_LARGE`。
> Caddy 需 `request_body max_size 1GB`；大文件走下面的分片。

### 分片上传（> 100 MB）

1. `POST /instances/me/files/uploads` `{ "path": "/", "name": "big.zip", "size": 123 }` → `{ upload_id, chunk_size }`
2. `PUT /instances/me/files/uploads/{upload_id}?offset=0`，`application/octet-stream` 原始分片
3. `POST /instances/me/files/uploads/{upload_id}/complete` `{ "sha256": "..." }`
4. `DELETE /instances/me/files/uploads/{upload_id}` 取消

### POST `/instances/me/files/mkdir`

```json
{ "path": "/", "name": "notes" }
```

### POST `/instances/me/files/rename`

```json
{ "path": "/notes", "new_name": "notes-old" }
```

### DELETE `/instances/me/files?path=/notes`

目录需 `?recursive=true`。

## 7. 快照

### GET `/instances/me/snapshots`

```json
{ "data": { "items": [
  { "id": 5, "status": "ready", "size_bytes": 1234567890,
    "dsh_version": "0.2.1-rc.5", "note": "装好 pandas 前",
    "created_at": "...", "expires_at": null }
], "total": 1 } }
```

### POST `/instances/me/snapshots`

```json
{ "note": "装好 pandas 前" }
```

`202`。实例会短暂 pause（通常 < 30s），进度通过 `job.progress` 推送（`stage=pausing|archiving|resuming`）；`instances.status` 保持 `running`。
`409 SNAPSHOT_LIMIT`。

### POST `/snapshots/{id}/restore`

`202`。恢复期间实例不可用。失败自动回滚。

### DELETE `/snapshots/{id}`

`204`。

## 8. 用户镜像

### GET `/me/images`

```json
{ "data": { "items": [
  { "id": 9, "name": "pandas-env", "size_bytes": 8123456789,
    "base_version": "0.2.1-rc.5", "status": "ready", "created_at": "..." }
], "total": 1 } }
```

### POST `/instances/me/images`

```json
{ "name": "pandas-env", "note": "预装 pandas 2.2" }
```

`202`；`409 IMAGE_LIMIT`；名称 `^[a-z0-9][a-z0-9-]{1,30}$`。

### DELETE `/me/images/{id}`

`204`；`409 IMAGE_IN_USE`（当前实例正基于它）。

### POST `/me/images/{id}/rebuild`

用该镜像重建当前实例（保留数据），`202`。

## 9. 工单与公告

### GET `/me/announcements` · GET `/me/tickets` · POST `/me/tickets`

```json
{ "subject": "无法启动", "category": "bug", "priority": "normal",
  "body": "点击启动后一直 provisioning" }
```

### GET `/me/tickets/{id}` · POST `/me/tickets/{id}/messages`

```json
{ "body": "补充：重启也不行" }
```

`409 TICKET_INVALID_STATE`（已关闭）。

## 10. 管理端：用户 / 邀请 / 套餐 / 配额

### GET `/admin/users?q=&status=&plan_id=&page=`

```json
{ "data": { "items": [
  { "id": 12, "username": "alice", "display_name": "Alice", "role": "user",
    "status": "active", "plan": { "id": 2, "name": "标准" },
    "instance": { "id": 7, "status": "running" },
    "quota": { "used_minutes": 1830, "remaining_minutes": 5370 },
    "created_at": "...", "last_login_at": "..." }
], "total": 1 } }
```

### POST `/admin/users`

```json
{ "username": "bob", "password": "临时密码", "display_name": "Bob",
  "email": "b@example.com", "role": "user", "plan_id": 1,
  "must_change_password": true }
```

### PATCH `/admin/users/{id}`

可改 `display_name/email/role/status/plan_id`。禁用时 `reason` 必填。

### POST `/admin/users/{id}/reset-password`

```json
{ "new_password": "..." }
```

### DELETE `/admin/users/{id}`

软删除；`?purge=true` 同时清数据（危险，需 `confirm=<username>`）。

### GET/POST `/admin/invites` · DELETE `/admin/invites/{id}`

```json
{ "role": "user", "max_uses": 5, "expires_in_days": 7, "note": "11 月批次" }
```

返回 `code` 与可分享链接 `https://console.dsh.example.com/register?code=...`。

### GET/POST `/admin/plans` · PATCH/DELETE `/admin/plans/{id}`

字段见 03 文档 `plans`。`409 PLAN_IN_USE`（仍有用户使用且未指定迁移套餐）。

### GET `/admin/quotas/{user_id}` · PATCH `/admin/quotas/{user_id}`

```json
{ "cpu_cores_override": null, "memory_mb_override": 6144,
  "monthly_minutes_override": null, "extra_seconds": 36000 }
```

### POST `/admin/quotas/{user_id}/grant`

```json
{ "minutes": 600, "reason": "11 月补偿" }
```

写 `quota_ledger`，返回新余额。

## 11. 管理端：镜像版本

### GET `/admin/images`

### GET `/admin/images/remote[?refresh=true]`

Published tags from the watched registry, merged with local state:

```json
{ "data": {
  "repository": "ghcr.io/zjwan461/dsh-docker",
  "checked_at": "2026-11-17T07:08:33.128154Z",
  "latest_version": "0.2.1-rc.5",
  "latest_tag": "0.2.1-rc.5",
  "current_version": "0.2.1-rc.5",
  "update_available": false,
  "stale": false,
  "items": [
    { "tag": "0.2.1-rc.5", "version": "0.2.1-rc.5", "variant": "full",
      "digest": "sha256:e63970e2…", "platform_digest": "sha256:5d1ec453…",
      "size_bytes": 1378873344, "created_at": "2026-11-17T05:59:28.166507Z",
      "platform": "linux/amd64",
      "imported": true, "matched_by": "version", "image_id": 3,
      "status": "ready", "is_default": true }
  ]
}}
```

- `refresh=true` 绕过缓存；否则按 `REGISTRY_CHECK_INTERVAL_HOURS` 复用快照。
- 仓库不可达时返回上次快照并带 `error` / `stale: true`，不返回 5xx。

### POST `/admin/images/remote/check`

强制刷新（等同 `refresh=true`），写审计。`200`。

### POST `/admin/images/remote/import`

```json
{ "tag": "0.2.1-rc.5", "is_default": true, "changelog": "可选" }
```

`201` 返回镜像记录；`409 IMAGE_DUPLICATE`（该引用已导入）；`422 IMAGE_IMPORT_FAILED`（pull 失败）。

### POST `/admin/images/upload`

`multipart/form-data`：`file`（`docker save` 的 tar）、`version`（可选）、`changelog`（可选）、`is_default`（可选）。
`201` 返回镜像记录；`413 FILE_TOO_LARGE`；`422 IMAGE_IMPORT_FAILED`。

### POST `/admin/images/pull`

```json
{ "image_ref": "ghcr.io/acme/dsh:0.2.1-rc.5", "version": "0.2.1-rc.5",
  "changelog": "...", "is_default": true }
```

`201` 返回镜像记录；`409 IMAGE_DUPLICATE`；`422 IMAGE_IMPORT_FAILED`。
另有 `POST /admin/images` 只登记本机已有镜像（不 pull）。

### PATCH `/admin/images/{id}`

```json
{ "status": "disabled", "is_default": false, "changelog": "..." }
```

设置默认版本时自动取消其他默认。

### DELETE `/admin/images/{id}`

`409 IMAGE_IN_USE`（有实例引用）；`?force=true` 仅解除控制台引用，运行中容器不受影响。

### POST `/admin/images/{id}/verify`

重新执行 `dsh --version` 校验；返回 `{ ok, version, error }`。

## 12. 管理端：实例

### GET `/admin/instances?status=&user_id=&image_id=&q=`

返回带实时 stats 的列表（stats 来自 Redis 缓存，延迟 ≤ 30s）。

### POST `/admin/instances/{id}/start|stop|restart|sleep`

同用户接口，管理员不受配额限制（但记录审计）。

### PATCH `/admin/instances/{id}`

```json
{ "image_id": 4, "plan_id": 2, "idle_timeout_seconds": 0 }
```

改镜像 = 版本迁移（自动快照，失败回滚）；`idle_timeout_seconds=0` 关闭自动休眠。

### DELETE `/admin/instances/{id}`

`?purge=true` 同时删数据（需确认用户名）。

### GET `/admin/instances/{id}/logs?tail=500`

### WS `/admin/instances/{id}/terminal`

M5 实现：管理员 cookie 鉴权后直接升级 WebSocket，转发一个真实的 `docker exec /bin/bash` TTY（`tty=True`，低层 API 保留 exec id 以便 resize）。不需要单独的 ticket 接口。

线协议：

| 方向 | 帧类型 | 含义 |
|---|---|---|
| 客户端 → 服务端 | binary | 原始按键字节，直接写入 PTY |
| 客户端 → 服务端 | text | JSON 控制：`{"type":"resize","cols":120,"rows":32}`、`{"type":"ping"}` |
| 服务端 → 客户端 | binary | PTY 输出字节，交给 xterm.js 原样渲染 |
| 服务端 → 客户端 | text | JSON 控制：`{"type":"ready","shell":"bash"}`、`{"type":"error","message":"…"}`、`{"type":"pong"}` |

- 按键走 binary，避免用户恰好输入 `{"type":...}` 时被误判为控制帧；
- `resize` 通过 `POST /exec/{id}/resize`（docker-py 的 `exec_resize(height=, width=)`）下发 SIGWINCH，全屏程序（vim/htop）才能正确布局；
- 断开连接即关闭 PTY 与 exec 进程。

## 13. 管理端：监控

### GET `/admin/metrics/overview`

```json
{ "data": {
  "users": { "total": 120, "active_7d": 43 },
  "instances": { "running": 18, "sleeping": 61, "stopped": 30, "error": 1 },
  "today": { "active_hours": 312.5, "cpu_hours": 601.2, "mem_gb_hours": 2401.0 },
  "quota_alerts": 4,
  "host": { "cpu_percent": 37.2, "mem_used_percent": 68.1,
            "disk_used_percent": 54.0, "load1": 6.1, "containers_running": 24 }
}}
```

### GET `/admin/metrics/node?from=&to=&step=30`

```json
{ "data": { "series": [
  { "ts": "...", "cpu_percent": 35.1, "mem_used_bytes": 1, "load1": 5.2,
    "net_rx_bytes": 1, "net_tx_bytes": 1, "disks": [{ "mount": "/", "used": 1, "total": 2 }] }
]}}
```

`step` ∈ {15,30,60,300,3600}；范围 ≤ 7 天时用原始点，否则自动降采样。

### GET `/admin/metrics/usage?from=&to=&group_by=user|day&metric=active_seconds`

```json
{ "data": { "rows": [
  { "key": "alice", "user_id": 12, "value": 109800 },
  { "key": "bob", "user_id": 13, "value": 72000 }
]}}
```

### GET `/admin/metrics/instances/live`

全部运行实例的实时 stats 数组（Redis 快照）。

## 14. 管理端：审计 / 公告 / 工单 / 设置 / 系统

### GET `/admin/audit?actor_id=&action=&from=&to=&page=`

```json
{ "data": { "items": [
  { "id": 991, "actor_name": "admin", "action": "instance.stop",
    "target_type": "instance", "target_id": "7",
    "ip": "10.0.0.5", "detail": { "from": "running", "to": "stopping" },
    "created_at": "..." }
], "total": 1 } }
```

### 公告

`GET/POST /admin/announcements`、`PATCH/DELETE /admin/announcements/{id}`、`POST /admin/announcements/{id}/publish`

### 工单

`GET /admin/tickets?status=`、`PATCH /admin/tickets/{id}` `{ "assignee_id": 1, "status": "processing" }`、`POST /admin/tickets/{id}/messages`

### GET/PUT `/admin/settings`

```json
{ "data": {
  "base_domain": "dsh.example.com",
  "console_name": "优墨云舱",
  "default_plan_id": 1,
  "default_image_id": 2,
  "default_idle_minutes": 30,
  "allow_user_version_switch": true,
  "allow_user_self_key": true,
  "snapshot_retention_days": 30,
  "deleted_user_retention_days": 7,
  "upload_max_mb": 1024,
  "admin_terminal_enabled": true,
  "maintenance_mode": false,
  "maintenance_message": ""
}}
```

`PUT` 支持部分字段；`base_domain` 变更需重启 Caddy 并重新签发证书（返回 `X-Needs-Caddy-Reload: true`）。

### GET/PUT `/admin/credentials/platform`

平台默认 Key 与端点，未自带凭据的用户使用它。

```json
{ "data": { "configured": true, "hint": "…abcd",
            "base_url": "https://gateway.example.com/v1", "updated_at": "..." } }
```

`PUT` 与 `/me/credentials` 同构：`{ "api_key"?, "base_url"?, "clear_key"? }`。
`base_url` 为空字符串表示恢复官方地址，`clear_key: true` 删除整条凭据。响应同样不回显明文。

### GET `/admin/system/health`

```json
{ "data": {
  "mysql": { "ok": true, "latency_ms": 2 },
  "redis": { "ok": true, "latency_ms": 1 },
  "docker": { "ok": true, "version": "27.3.1", "containers": 24 },
  "caddy": { "ok": true, "config_loaded": true, "routes": 121 },
  "disk": { "ok": true, "free_bytes": 1, "used_percent": 54.0 },
  "worker": { "ok": true, "queued": 0, "last_heartbeat": "..." },
  "capacity": {
    "probed_at": "2026-11-17T01:00:00Z",
    "cpu_cores": 32, "cpu_committed": 12.0, "cpu_overcommit": 4.0,
    "mem_total_bytes": 68719476736, "mem_committed_bytes": 42949672960,
    "mem_reserve_bytes": 6871947673.6,
    "disk_total_bytes": 1, "disk_allocated_bytes": 1,
    "can_admit": { "cpu": true, "memory": false, "disk": true }
  }
}}
```

> `capacity` 是**探测值**，不是配置：来自 Docker `info` 与 `statfs(DATA_ROOT)`，每 30s 刷新。没有 `can_admit` 时按 02 §10 的公式现场算。

### POST `/admin/system/reconcile`

`202 { job_id }`。强制三方对账。

### POST `/admin/system/gc`

```json
{ "prune_orphans": true, "prune_images": true, "purge_expired_snapshots": true,
  "purge_deleted_users": false, "dry_run": true }
```

`202`；`dry_run` 返回将被删除的清单。

## 15. 实时通道

### SSE `GET /events`

`Content-Type: text/event-stream`；`event:` 类型：

```
event: instance.status
data: {"instance_id":7,"status":"running","reason":null,"at":"..."}

event: job.progress
data: {"job_id":"job_x","percent":42,"stage":"creating_container"}

event: quota.updated
data: {"user_id":12,"remaining_minutes":5370}

event: announcement.new
data: {"id":4,"title":"..."}

event: heartbeat
data: {"at":"..."}
```

每 15s 心跳；断线由浏览器 `EventSource` 自动重连（带 `Last-Event-ID`，服务端保留 5 分钟事件）。

### WS `/admin/instances/{id}/terminal`

仅管理员（会话 cookie 鉴权）。M5 为原始字节流：输出直接推给浏览器，输入直接写入 PTY；前端的简化控制台按行发送。xterm.js 与 resize 协议在后续增强。

## 16. 错误码

| code | HTTP | 含义 |
|---|---|---|
| `AUTH_INVALID_CREDENTIALS` | 401 | 账号或密码错误 |
| `AUTH_ACCOUNT_DISABLED` | 403 | 账号已禁用 |
| `AUTH_SESSION_EXPIRED` | 401 | 会话过期 |
| `AUTH_CSRF_INVALID` | 403 | CSRF 校验失败 |
| `AUTH_RATE_LIMITED` | 429 | 登录/接口限流 |
| `WEAK_PASSWORD` | 422 | 密码强度不足 |
| `INVITE_INVALID` / `INVITE_EXHAUSTED` | 400/410 | 邀请码无效/用尽 |
| `USERNAME_TAKEN` / `USERNAME_RESERVED` / `USERNAME_INVALID` | 409/400 | 用户名问题 |
| `FORBIDDEN` | 403 | 无权限 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `VALIDATION_ERROR` | 422 | 参数校验失败（`details.fields`） |
| `INSTANCE_EXISTS` | 409 | 已有实例 |
| `INSTANCE_BUSY` | 409 | 操作进行中 |
| `INSTANCE_STATE_INVALID` | 409 | 当前状态不允许该操作 |
| `INSTANCE_NOT_RUNNING` | 409 | 实例未运行 |
| `INSTANCE_QUOTA_MEMORY` | 409 | 宿主内存不足 |
| `TOKEN_UNAVAILABLE` | 410 | 取不到 DSH token（需重启） |
| `QUOTA_EXHAUSTED` | 402 | 时长用尽 |
| `PLAN_DISABLED` / `IMAGE_DISABLED` | 409 | 套餐/版本不可用 |
| `IMAGE_DUPLICATE` / `IMAGE_IN_USE` | 409 | 镜像重复/被引用 |
| `IMAGE_IMPORT_FAILED` | 422 | 导入失败（`details.reason`） |
| `SNAPSHOT_LIMIT` / `SNAPSHOT_FAILED` / `SNAPSHOT_RESTORE_FAILED` | 409/500 | 快照问题 |
| `FILE_TOO_LARGE` / `FILE_PATH_INVALID` / `FILE_NOT_FOUND` | 413/400/404 | 文件问题 |
| `UPLOAD_INCOMPLETE` | 400 | 分片不完整/校验失败 |
| `TICKET_INVALID_STATE` | 409 | 工单已关闭 |
| `VERSION_SWITCH_FAILED` | 500 | 迁移失败已回滚 |
| `DOCKER_UNAVAILABLE` / `CADDY_UNAVAILABLE` | 503 | 依赖不可用 |
| `INTERNAL_ERROR` | 500 | 未预期错误（带 request_id） |

## 17. 版本与兼容

- 路径版本 `/api/v1`；破坏性变更升 `/api/v2`，`v1` 至少保留一个版本周期。
- 响应新增字段视为兼容；前端不得依赖字段顺序。
- 管理端与用户端共用同一前端构建，按 `role` 渲染路由。
