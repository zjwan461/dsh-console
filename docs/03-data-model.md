# 03 数据模型

> MySQL 8.4 · utf8mb4_0900_ai_ci · InnoDB · 所有时间 `DATETIME(3)`，应用层统一 UTC 存储、展示按 `Asia/Shanghai`

## 1. 实体关系

```
plans 1──n users 1──1 user_quotas
  │            │ 1──n quota_ledger
  │            │ 1──n user_sessions
  │            │ 1──n provider_credentials
  │            │ 1──1 instances 1──n instance_events
  │            │            │ 1──n usage_bucket
  │            │            │ 1──n snapshots
  │            │            └─1──n user_images
  │            └ 1──n tickets 1──n ticket_messages
  └ 1──n dsh_images（套餐可限制最低版本，暂不建 FK）

invites（独立）
announcements / settings / audit_logs / node_metric（独立）
```

## 2. 建表 DDL

### 2.1 套餐

```sql
CREATE TABLE plans (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code            VARCHAR(32)  NOT NULL COMMENT '基础/basic 等，稳定标识',
  name            VARCHAR(64)  NOT NULL,
  description     VARCHAR(255) NOT NULL DEFAULT '',
  cpu_cores       DECIMAL(4,2) NOT NULL DEFAULT 1.00,
  memory_mb       INT UNSIGNED NOT NULL DEFAULT 2048,
  disk_gb         INT UNSIGNED NOT NULL DEFAULT 20,
  monthly_minutes INT UNSIGNED NOT NULL DEFAULT 3600 COMMENT '每月可用活跃分钟',
  max_snapshots   INT UNSIGNED NOT NULL DEFAULT 2,
  max_user_images INT UNSIGNED NOT NULL DEFAULT 2,
  gpu_count       TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '二期启用',
  is_default      TINYINT(1) NOT NULL DEFAULT 0,
  enabled         TINYINT(1) NOT NULL DEFAULT 1,
  sort_order      INT NOT NULL DEFAULT 0,
  created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_plans_code (code)
) ENGINE=InnoDB COMMENT='资源套餐';
```

### 2.2 用户

```sql
CREATE TABLE users (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username            VARCHAR(32)  NOT NULL COMMENT '同时是子域名标签',
  email               VARCHAR(190) NULL,
  display_name        VARCHAR(64)  NOT NULL DEFAULT '',
  password_hash       VARCHAR(255) NOT NULL COMMENT 'argon2id',
  role                ENUM('user','admin') NOT NULL DEFAULT 'user',
  status              ENUM('active','disabled','pending') NOT NULL DEFAULT 'active',
  subdomain           VARCHAR(63)  NOT NULL,
  plan_id             BIGINT UNSIGNED NULL,
  must_change_password TINYINT(1) NOT NULL DEFAULT 0,
  last_login_at       DATETIME(3) NULL,
  last_login_ip       VARCHAR(45) NULL,
  disabled_reason     VARCHAR(255) NULL,
  deleted_at          DATETIME(3) NULL,
  created_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_users_username (username),
  UNIQUE KEY uk_users_email (email),
  UNIQUE KEY uk_users_subdomain (subdomain),
  KEY idx_users_plan (plan_id),
  KEY idx_users_status (status),
  CONSTRAINT fk_users_plan FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE SET NULL
) ENGINE=InnoDB COMMENT='控制台账号';

-- 软删除必须在同一事务里把 username/subdomain 改名（如 u42_deleted_1731...），
-- 否则唯一键会挡住同名重建。不要用 (username, deleted_at) 做唯一键：
-- MySQL 的唯一索引把 NULL 视为互不相等，活跃行之间不会互相约束。
```

### 2.3 邀请码

```sql
CREATE TABLE invites (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code        CHAR(32) NOT NULL,
  role        ENUM('user','admin') NOT NULL DEFAULT 'user',
  max_uses    INT UNSIGNED NOT NULL DEFAULT 1,
  used_count  INT UNSIGNED NOT NULL DEFAULT 0,
  expires_at  DATETIME(3) NULL,
  note        VARCHAR(255) NOT NULL DEFAULT '',
  created_by  BIGINT UNSIGNED NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  revoked_at  DATETIME(3) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_invites_code (code),
  KEY idx_invites_created (created_at)
) ENGINE=InnoDB COMMENT='注册邀请码';
```

### 2.4 会话

```sql
CREATE TABLE user_sessions (
  id           CHAR(43) NOT NULL COMMENT 'base64url 32B',
  user_id      BIGINT UNSIGNED NOT NULL,
  csrf_token   CHAR(43) NOT NULL,
  ip           VARCHAR(45) NULL,
  user_agent   VARCHAR(255) NULL,
  created_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  last_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  expires_at   DATETIME(3) NOT NULL,
  revoked_at   DATETIME(3) NULL,
  PRIMARY KEY (id),
  KEY idx_sessions_user (user_id, expires_at),
  CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='服务端会话（Redis 为主，此表用于审计与强制下线）';
```

> 会话以 Redis 为准（TTL 滑动），MySQL 只做异步落库用于「查看登录设备/强制下线」。若嫌重，一期可只保留 Redis；表结构先定义。

### 2.5 配额

```sql
CREATE TABLE user_quotas (
  user_id                 BIGINT UNSIGNED NOT NULL,
  plan_id                 BIGINT UNSIGNED NULL,
  cpu_cores_override      DECIMAL(4,2) NULL,
  memory_mb_override      INT UNSIGNED NULL,
  disk_gb_override        INT UNSIGNED NULL,
  monthly_minutes_override INT UNSIGNED NULL,
  extra_seconds           BIGINT NOT NULL DEFAULT 0 COMMENT '管理员赠送-扣减的净额',
  seconds_used            BIGINT NOT NULL DEFAULT 0 COMMENT '本周期活跃秒（冗余，便于快速判断）',
  cycle_start             DATE NOT NULL,
  cycle_end               DATE NOT NULL,
  updated_at              DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (user_id),
  KEY idx_quotas_cycle (cycle_end),
  CONSTRAINT fk_quotas_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT fk_quotas_plan FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE SET NULL
) ENGINE=InnoDB COMMENT='用户配额与周期；生效值 = override ?? plan 值';
```

### 2.6 配额流水

```sql
CREATE TABLE quota_ledger (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id       BIGINT UNSIGNED NOT NULL,
  delta_seconds BIGINT NOT NULL COMMENT '正=赠送，负=扣减/纠正',
  reason        ENUM('grant','adjust','reset','plan_change','refund') NOT NULL,
  balance_after BIGINT NOT NULL,
  note          VARCHAR(255) NOT NULL DEFAULT '',
  actor_id      BIGINT UNSIGNED NULL COMMENT 'NULL=系统',
  created_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_ledger_user (user_id, created_at),
  CONSTRAINT fk_ledger_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='时长变更流水，只增不改';
```

### 2.7 DSH 版本镜像

```sql
CREATE TABLE dsh_images (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  version      VARCHAR(64) NOT NULL COMMENT '如 0.2.1-rc.5',
  image_ref    VARCHAR(255) NOT NULL COMMENT 'registry/repo:tag 或本地 tag',
  digest       CHAR(71) NULL COMMENT 'sha256:...',
  size_bytes   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  arch         VARCHAR(16) NOT NULL DEFAULT 'amd64',
  source       ENUM('pull','upload','build') NOT NULL,
  status       ENUM('importing','ready','disabled','failed') NOT NULL DEFAULT 'importing',
  is_default   TINYINT(1) NOT NULL DEFAULT 0,
  changelog    TEXT NULL,
  labels       JSON NULL,
  error        VARCHAR(500) NULL,
  created_by   BIGINT UNSIGNED NULL,
  created_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_images_ref (image_ref),
  UNIQUE KEY uk_images_digest (digest),
  KEY idx_images_status (status),
  CONSTRAINT fk_images_creator FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB COMMENT='可用的 DSH 基础镜像版本';
```

### 2.8 实例

```sql
CREATE TABLE instances (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id             BIGINT UNSIGNED NOT NULL,
  container_id        VARCHAR(64) NULL,
  container_name      VARCHAR(64) NOT NULL,
  image_id            BIGINT UNSIGNED NOT NULL,
  dsh_version         VARCHAR(64) NOT NULL,
  status              ENUM('provisioning','starting','running','sleeping','waking',
                           'stopping','stopped','error','deleting','deleted')
                      NOT NULL DEFAULT 'provisioning',
  status_reason       VARCHAR(255) NULL,
  subdomain           VARCHAR(63) NOT NULL,
  cpu_cores           DECIMAL(4,2) NOT NULL,
  memory_mb           INT UNSIGNED NOT NULL,
  disk_gb             INT UNSIGNED NOT NULL,
  data_path           VARCHAR(512) NOT NULL,
  workspace_path      VARCHAR(512) NOT NULL,
  wake_key            CHAR(43) NOT NULL,
  idle_timeout_seconds INT UNSIGNED NOT NULL DEFAULT 1800,
  last_active_at      DATETIME(3) NULL,
  last_started_at     DATETIME(3) NULL,
  last_stopped_at     DATETIME(3) NULL,
  health              ENUM('unknown','healthy','unhealthy') NOT NULL DEFAULT 'unknown',
  error               VARCHAR(500) NULL,
  created_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_instances_user (user_id) COMMENT '一期一人一实例',
  UNIQUE KEY uk_instances_container (container_id),
  UNIQUE KEY uk_instances_wake (wake_key),
  UNIQUE KEY uk_instances_subdomain (subdomain),
  KEY idx_instances_status (status),
  KEY idx_instances_image (image_id),
  CONSTRAINT fk_instances_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT fk_instances_image FOREIGN KEY (image_id) REFERENCES dsh_images (id) ON DELETE RESTRICT
) ENGINE=InnoDB COMMENT='用户 DSH 实例';
```

### 2.9 实例事件

```sql
CREATE TABLE instance_events (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  instance_id BIGINT UNSIGNED NOT NULL,
  type        VARCHAR(48) NOT NULL COMMENT 'create/start/stop/sleep/wake/quota_exhausted/...',
  from_status VARCHAR(24) NULL,
  to_status   VARCHAR(24) NULL,
  message     VARCHAR(500) NOT NULL DEFAULT '',
  actor_id    BIGINT UNSIGNED NULL COMMENT 'NULL=系统',
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_events_instance (instance_id, created_at),
  CONSTRAINT fk_events_instance FOREIGN KEY (instance_id) REFERENCES instances (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='生命周期事件，供时间线与排障';
```

### 2.10 用量桶

```sql
CREATE TABLE usage_bucket (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  instance_id      BIGINT UNSIGNED NOT NULL,
  user_id          BIGINT UNSIGNED NOT NULL,
  bucket_start     DATETIME NOT NULL COMMENT '整点（UTC）',
  active_seconds   INT UNSIGNED NOT NULL DEFAULT 0,
  cpu_seconds      DECIMAL(12,2) NOT NULL DEFAULT 0,
  mem_mb_seconds   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  disk_bytes_max   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  net_rx_bytes     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  net_tx_bytes     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  samples          INT UNSIGNED NOT NULL DEFAULT 0,
  updated_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_usage_instance_hour (instance_id, bucket_start),
  KEY idx_usage_user_time (user_id, bucket_start),
  CONSTRAINT fk_usage_instance FOREIGN KEY (instance_id) REFERENCES instances (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='小时级用量；活跃时长是计费依据';
```

> 保留 400 天。数据量大时可改为按月 RANGE 分区，键为 `bucket_start`。

### 2.11 快照

```sql
CREATE TABLE snapshots (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  instance_id BIGINT UNSIGNED NOT NULL,
  status      ENUM('creating','ready','restoring','failed') NOT NULL DEFAULT 'creating',
  path        VARCHAR(512) NOT NULL COMMENT '相对 $DATA_ROOT/snapshots',
  size_bytes  BIGINT UNSIGNED NOT NULL DEFAULT 0,
  sha256      CHAR(64) NULL,
  dsh_version VARCHAR(64) NOT NULL,
  image_digest CHAR(71) NULL,
  note        VARCHAR(200) NOT NULL DEFAULT '',
  manifest    JSON NULL,
  error       VARCHAR(500) NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  expires_at  DATETIME(3) NULL,
  PRIMARY KEY (id),
  KEY idx_snapshots_user (user_id, created_at),
  KEY idx_snapshots_instance (instance_id, created_at),
  KEY idx_snapshots_expiry (expires_at),
  CONSTRAINT fk_snapshots_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT fk_snapshots_instance FOREIGN KEY (instance_id) REFERENCES instances (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='数据目录归档';
```

### 2.12 用户镜像

```sql
CREATE TABLE user_images (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id      BIGINT UNSIGNED NOT NULL,
  instance_id  BIGINT UNSIGNED NOT NULL,
  name         VARCHAR(64) NOT NULL,
  image_ref    VARCHAR(255) NOT NULL,
  digest       CHAR(71) NULL,
  size_bytes   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  base_version VARCHAR(64) NOT NULL,
  status       ENUM('committing','ready','failed') NOT NULL DEFAULT 'committing',
  note         VARCHAR(200) NOT NULL DEFAULT '',
  error        VARCHAR(500) NULL,
  created_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_uimages_ref (image_ref),
  UNIQUE KEY uk_uimages_user_name (user_id, name),
  KEY idx_uimages_user (user_id, created_at),
  CONSTRAINT fk_uimages_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT fk_uimages_instance FOREIGN KEY (instance_id) REFERENCES instances (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='docker commit 产出的个人镜像（仅系统层）';
```

### 2.13 宿主机指标

```sql
CREATE TABLE node_metric (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ts                 DATETIME(3) NOT NULL,
  cpu_percent        DECIMAL(5,2) NOT NULL,
  load1              DECIMAL(6,2) NOT NULL DEFAULT 0,
  load5              DECIMAL(6,2) NOT NULL DEFAULT 0,
  load15             DECIMAL(6,2) NOT NULL DEFAULT 0,
  mem_total_bytes    BIGINT UNSIGNED NOT NULL,
  mem_used_bytes     BIGINT UNSIGNED NOT NULL,
  swap_used_bytes    BIGINT UNSIGNED NOT NULL DEFAULT 0,
  net_rx_bytes       BIGINT UNSIGNED NOT NULL DEFAULT 0,
  net_tx_bytes       BIGINT UNSIGNED NOT NULL DEFAULT 0,
  containers_running INT UNSIGNED NOT NULL DEFAULT 0,
  disks              JSON NULL COMMENT '[{mount,total,used}]',
  gpu                JSON NULL COMMENT '二期：dcgm 指标',
  created_at         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_node_ts (ts)
) ENGINE=InnoDB COMMENT='物理机指标；30s 一条，保留 7 天';
```

### 2.14 审计

```sql
CREATE TABLE audit_logs (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  actor_id    BIGINT UNSIGNED NULL,
  actor_name  VARCHAR(64) NOT NULL DEFAULT 'system',
  action      VARCHAR(64) NOT NULL COMMENT 'instance.start / image.import / ...',
  target_type VARCHAR(32) NOT NULL DEFAULT '',
  target_id   VARCHAR(64) NOT NULL DEFAULT '',
  ip          VARCHAR(45) NULL,
  user_agent  VARCHAR(255) NULL,
  detail      JSON NULL COMMENT '变更前后，敏感字段已脱敏',
  request_id  CHAR(26) NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_audit_actor (actor_id, created_at),
  KEY idx_audit_action (action, created_at),
  KEY idx_audit_time (created_at)
) ENGINE=InnoDB COMMENT='只增不改；应用账号不授予 DELETE';
```

### 2.15 公告 / 工单 / 设置 / 凭据

```sql
CREATE TABLE announcements (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  title      VARCHAR(120) NOT NULL,
  body_md    MEDIUMTEXT NOT NULL,
  level      ENUM('info','warning','maintenance') NOT NULL DEFAULT 'info',
  status     ENUM('draft','published','archived') NOT NULL DEFAULT 'draft',
  pinned     TINYINT(1) NOT NULL DEFAULT 0,
  publish_at DATETIME(3) NULL,
  expire_at  DATETIME(3) NULL,
  created_by BIGINT UNSIGNED NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_ann_window (status, publish_at, expire_at)
) ENGINE=InnoDB;

CREATE TABLE tickets (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  subject     VARCHAR(200) NOT NULL,
  category    ENUM('usage','quota','bug','other') NOT NULL DEFAULT 'other',
  priority    ENUM('low','normal','high') NOT NULL DEFAULT 'normal',
  status      ENUM('open','processing','resolved','closed') NOT NULL DEFAULT 'open',
  assignee_id BIGINT UNSIGNED NULL,
  last_reply_at DATETIME(3) NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_tickets_user (user_id, created_at),
  KEY idx_tickets_status (status, updated_at),
  CONSTRAINT fk_tickets_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE ticket_messages (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ticket_id   BIGINT UNSIGNED NOT NULL,
  author_id   BIGINT UNSIGNED NOT NULL,
  body        MEDIUMTEXT NOT NULL,
  attachments JSON NULL COMMENT '[{name,path,size}]',
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_tmsg_ticket (ticket_id, created_at),
  CONSTRAINT fk_tmsg_ticket FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE settings (
  k          VARCHAR(64) NOT NULL,
  v          JSON NOT NULL,
  updated_by BIGINT UNSIGNED NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (k)
) ENGINE=InnoDB COMMENT='全局设置，带类型校验的 JSON';

CREATE TABLE provider_credentials (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  scope      ENUM('platform','user') NOT NULL,
  user_id    BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'platform 固定 0',
  provider   VARCHAR(32) NOT NULL DEFAULT 'deepseek',
  ciphertext VARBINARY(1024) NOT NULL COMMENT 'AES-256-GCM，nonce 前置',
  key_hint   VARCHAR(8) NOT NULL DEFAULT '' COMMENT '仅掩码尾 4 位',
  created_by BIGINT UNSIGNED NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_cred_scope (scope, user_id, provider)
) ENGINE=InnoDB COMMENT='模型 API Key，加密存储';
```

## 3. Redis 键

| 键 | 类型 | TTL | 用途 |
|---|---|---|---|
| `sess:{sid}` | Hash | 7d 滑动 | 会话（user_id, csrf, ip, ua） |
| `sess:user:{uid}` | Set | 7d | 该用户全部会话，用于强制下线 |
| `login:fail:ip:{ip}` | String | 60s | 登录限流 |
| `login:fail:user:{name}` | String | 1h | 账号锁定 |
| `dsh:token:{instance_id}` | String | 同容器生命周期 | DSH 本次启动 token |
| `inst:last_active:{id}` | String(epoch) | 2h | 最近活动时间 |
| `inst:net:{id}` | Hash | 1h | 上次网络计数（算增量） |
| `usage:{id}:{yyyymmddHH}` | Hash | 3h | 活跃秒/CPU/内存待落库计数 |
| `quota:used:{user_id}` | String | 周期末 | 快速剩余时长（与 DB 双写，DB 为准） |
| `lock:instance:{id}` | String | 5m | 生命周期互斥锁 |
| `lock:reconcile` | String | 1m | 对账互斥 |
| `wake:{wake_key}` | String | 10m | 唤醒去重 |
| `ticket:{ticket}` | String | 60s | 一次性登录票据 |
| `job:{job_id}` | Hash | 1d | 长任务进度 |
| `caddy:routes` | Hash | — | 已下发路由缓存（对账用） |
| `arq:*` | — | — | 任务队列 |

## 4. 状态机

### 4.1 实例

```
                 ┌──────────────┐
   create ──────>│ provisioning│──fail──> error
                 └──────┬───────┘
                        │ healthy
                        v
   start ─────────> starting ──fail──> error
                        │
                        v
      ┌───────────> running <───────────┐
      │                │                │
   wake │           idle timeout      start
      │                v                │
      │            stopping ────────> sleeping
      │                │                │
      │                v                │
      └──────────── stopped <───────────┘
                        │
                     delete
                        v
                    deleting ──> deleted（记录保留，数据按策略 GC）
```

- `sleeping` 与 `stopped` 都表示容器已停；区别是**谁触发的**（自动 / 用户），用于 UI 文案与统计。
- `error` 必须带 `error` 字段；可从 `error` 直接重试 start。
- 所有转换写 `instance_events` + SSE。

### 4.2 快照 / 镜像

```
snapshot: creating ──> ready ──restore──> restoring ──> ready
                └──fail──> failed            └──fail──> failed（自动回滚）
image:    importing ──> ready <──> disabled
                └──fail──> failed
user_image: committing ──> ready
                └──fail──> failed
```

## 5. 一致性与并发

- **配额扣减**：`UPDATE user_quotas SET seconds_used = seconds_used + ? WHERE user_id=? AND seconds_used + ? <= limit`；影响行数 0 即超限。Redis 计数只做加速，定时与 DB 对齐。
- **创建实例**：`INSERT ... UNIQUE(user_id)` 捕获重复；失败回滚目录。
- **生命周期**：先抢 `lock:instance:{id}`，再改状态；重复请求 409。
- **对账**：以 DB 为期望状态，Docker/Caddy 为实际状态；修正动作同样加锁。
- **计量落库**：`INSERT ... ON DUPLICATE KEY UPDATE active_seconds = active_seconds + VALUES(...)`，按小时幂等（用 Redis 增量后清零，避免重复累加）。

## 6. 保留与清理

| 数据 | 保留 | 清理方式 |
|---|---|---|
| `usage_bucket` | 400 天 | 月度任务删旧分区/行 |
| `node_metric` | 7 天原始 + 1 年小时聚合 | 每日降采样后删除 |
| `instance_events` | 90 天 | 每日删除 |
| `audit_logs` | 180 天 | 归档到文件后删除（仅 DBA 手动） |
| 快照 | 套餐上限 + 用户删除后 7 天 | worker GC |
| 用户数据 | 删除后 7 天 | worker GC（先 dry-run 日志） |
| 用户镜像 | 随用户删除 | `docker rmi`，有引用则跳过 |

## 7. 迁移与种子

- Alembic 单 linear revision；`alembic upgrade head` 在 API 启动前执行（compose 的 `command` 或 init job）。
- 种子数据（幂等）：
  - 三个默认套餐（基础/标准/高性能）；
  - 默认 `settings`：`base_domain`、`console_name`、`default_idle_minutes=30`、`snapshot_retention_days=30`、`deleted_user_retention_days=7`、`upload_max_mb=1024`、`allow_user_version_switch=true`、`admin_terminal_enabled=true`；
  - 首个管理员：环境变量 `BOOTSTRAP_ADMIN_USERNAME/PASSWORD`（仅当 users 为空时创建），或 CLI `python -m app.cli create-admin`。
- 回滚策略：每个 revision 提供 `downgrade`；破坏性变更（删列）分两个版本发布。
