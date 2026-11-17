# 02 架构设计

> 版本：v0.1 · 依赖：`dsh-docker`（镜像底座） · 目标：单物理机，多租户

## 1. 总览

```
                                   ┌──────────────────────────── 物理机 ────────────────────────────┐
                                   │                                                                │
 浏览器 ──HTTPS:443──> caddy ──────┼─ console.dsh.example.com ──> 静态 SPA（file_server）      │
                                   │        └─ /api,/ws ─────────> console-api:8000              │
                                   │                                                                │
                                   │   {user}.dsh.example.com ──> dsh-u-{id}:3080  (running)        │
                                   │        └─ 休眠时 ──────────> console-api /w/{wake_key}           │
                                   │                                                                │
                                   │   console-api ── docker.sock ──> dsh-u-* (create/start/stop/   │
                                   │        │                          stats/logs/exec/commit)       │
                                   │        ├── mysql:3306                                           │
                                   │        ├── redis:6379                                           │
                                   │        └── caddy admin:2019                                     │
                                   │                                                                │
                                   │   console-worker ── ARQ ──> 生命周期 / 计量落库 / 休眠扫描 /     │
                                   │                            快照 / 镜像导入 / 对账 / 配额重置     │
                                   │                                                                │
                                   │   node-exporter:9100  (host /proc,/sys,/ ro)                    │
                                   └────────────────────────────────────────────────────────────────┘
```

**关键原则**

1. **Caddy 是唯一入口**，也是唯一 TLS 终结者；不再叠加 nginx（沿用 dsh-docker 的结论）。
2. **实例不发布端口**，只挂在内部 bridge 网络上，由 Caddy 按子域名反代。
3. **控制面是唯一事实源**：DB 记录期望状态，worker 负责收敛；Docker/Caddy 的漂移由对账任务修正。
4. **不改 dsh 本体**：多租户能力全部在容器编排、反代与计量层。
5. **控制面崩溃不影响已运行实例**：实例 `restart: no`，由控制面管理；控制面恢复后自动接管。

## 2. 组件

| 组件 | 职责 | 关键约束 |
|---|---|---|
| `caddy` | TLS、静态 SPA、`/api` 反代、按子域名动态路由、访问日志 | 通过 Admin API 被控制面改路由；配置不落盘，重启后由对账重建 |
| `console-api` | REST/WS/SSE、认证、权限、实例/快照/镜像/文件/工单、Caddy 路由管理、日志采集 | 挂 `docker.sock`（root 等价）、挂数据根目录；只被 Caddy 访问 |
| `console-worker` | ARQ 消费 + cron：生命周期、计量落库、休眠扫描、快照、commit、镜像导入、对账、配额重置、GC | 与 API 同镜像不同入口；同一把实例锁 |
| `mysql` | 业务与计量数据 | 单实例；每日备份 |
| `redis` | 会话、token 缓存、活跃计数、配额计数、分布式锁、ARQ 队列 | 持久化 AOF everysec |
| `node-exporter` | 宿主机指标 | 只读挂载，不对外发布 |
| `dsh-u-{id}` | 用户实例 | 无特权、资源受限、无端口发布 |

### 2.1 进程与队列

- API 只做**短事务**：写 DB → 投递 ARQ job → 立即返回 `202` + `instance.status`。
- Worker 串行化同一实例的操作：`SET lock:instance:{id} NX PX 300000`；重复请求返回 `409 INSTANCE_BUSY`。
- 长任务（快照、镜像导入）有进度：Redis Hash `job:{id}`，前端轮询或 SSE。

### 2.2 目录布局（宿主机）

```
/srv/dsh-console/                    # 项目根（compose、.env、web/dist）
└── data/                            # DATA_ROOT，挂进 console-api
    ├── instances/{user_id}/data        # -> /data/dsh        (DSH_HOME)
    ├── instances/{user_id}/workspace   # -> /workspaces
    ├── snapshots/{user_id}/{snap_id}.tar.zst
    ├── images/upload/{upload_id}/      # 分片上传临时区
    ├── plugin-sets/{set}/              # 挂给实例的 /opt/dsh-plugins（只读）
    ├── caddy/access.log                # 访问日志（net 输出失败时的兜底文件）
    └── secrets/master.key              # AES-GCM 主密钥（0400，root）
```

`console-api` 以 root 运行并挂载 `DATA_ROOT` 与 `docker.sock`。这是有意的取舍：文件管理、chown、快照都需要宿主文件权限。安全边界见 §9。实例的 bind mount 源路径一律由 `DATA_ROOT + instances/{id}` 推导，**绝不接受用户输入**。

## 3. 网络与域名

| 名称 | 用途 | 说明 |
|---|---|---|
| `dsh-edge` | caddy ↔ 实例 | bridge；实例加入后 Caddy 可按容器名/别名访问 |
| `dsh-console` | caddy ↔ api/mysql/redis | 控制面内部网络，不发布 |
| `console.dsh.example.com` | 控制台 | Caddy 提供 SPA 与 `/api` |
| `{username}.dsh.example.com` | 用户实例 | 每个用户一个 host 路由 |
| `*.dsh.example.com` | 泛域名证书 | Caddy internal CA 签发，客户端装一次根证书 |

**域名占位**：文档里的 `dsh.example.com` 只是占位符。所有主机名都来自配置（`CONSOLE_BASE_DOMAIN`），开源使用者填自己的域名即可；控制面不写死任何域名，Caddy 配置由模板按配置生成。TLS 模式也做成可配置：`internal_ca`（默认，内网/离线可用）或 `acme`（公网域名，DNS-01/HTTP-01）。

**DNS**：把 `*.{base_domain}` 与 `console.{base_domain}` 解析到本机即可。Caddy 只按 Host 匹配，不解析上游域名之外的任何东西。

**保留子域名**：`console`、`api`、`admin`、`www`、`status`、`mail`、`caddy`、`wildcard`、`dsh`。注册时拒绝，且大小写不敏感。

**Cookie 隔离**：DSH 的 cookie 绑定 authority（`host:port`），每个子域名天然独立；控制台自己的会话 cookie 只在 `console.` 域，两者不共享。这是子域名方案的核心收益。

## 4. 实例规格

每个实例由控制面创建，参数如下（`docker-py`）：

| 项 | 值 | 说明 |
|---|---|---|
| 镜像 | 选中的 `dsh_images.image_ref` | 版本由管理员启用 |
| 名称 | `dsh-u-{user_id}` | 标签 `dsh.console.managed=true` |
| user | `1000:1000`（可配） | 与数据目录属主一致 |
| working_dir | `/workspaces` | |
| 环境变量 | 见下表 | |
| 挂载 | `instances/{uid}/data:/data/dsh`、`instances/{uid}/workspace:/workspaces`（可选 `plugin-sets/{set}:/opt/dsh-plugins:ro`） | **目录挂载**（单文件挂载会导致 rename EBUSY，dsh-docker README 已论证） |
| CPU | `nano_cpus = cpu_cores * 1e9` | 套餐值 |
| 内存 | `mem_limit = memory_mb m`，`memswap_limit` 相同 | 禁用 swap，避免 OOM 拖垮整机 |
| pids | `pids_limit=4096` | 防 fork 炸弹 |
| shm | `shm_size=512m` | headless Chrome 需要 |
| 安全 | `cap_drop=["ALL"]`、`security_opt=["no-new-privileges:true"]` | 不 privileged、不挂 docker.sock |
| 网络 | `dsh-edge`，别名 `dsh-u-{id}` | 不发布端口 |
| 日志 | json-file，`max-size=10m`，`max-file=3` | token 要从日志读，保留足够窗口 |
| 重启 | `no` | 由控制面决定 |
| 健康检查 | 读 `/data/dsh/.runtime/bind-host` 请求 `/`，接受 200/401 | 与 dsh-docker 一致 |
| 标签 | `dsh.console.instance_id/user_id/version/image_id` | 对账依据 |
| 停止宽限 | `stop_grace_period=15s` | dsh 优雅退出 ≤5s |

环境变量：

```ini
DSH_HOME=/data/dsh
DSH_BIND_HOST=auto                 # 入口脚本探测容器自身 IPv4；0.2.1 拒绝 0.0.0.0
DSH_BIND_PORT=3080
DSH_TRUSTED_HOSTS={username}.{base_domain}   # 必须是裸域名，不能带 :443
DEEPSEEK_API_KEY={effective_key}   # 用户 Key 优先，否则平台 Key
LITELLM_API_KEY={optional}
DSH_PERMISSION_MODE=workspace-write
DSH_TELEMETRY_DISABLED=1
DSH_PLUGIN_ROOT=/opt/dsh-plugins
TZ=Asia/Shanghai
NO_PROXY=127.0.0.1,localhost,172.16.0.0/12
no_proxy=127.0.0.1,localhost,172.16.0.0/12
```

> **为什么 `DSH_TRUSTED_HOSTS` 是裸域名**：dsh-docker README 已核对源码——无端口条目按 hostname 比较，`host:443` 永远匹配不上浏览器默认端口的 Host 头。带端口 = 403。

> **`login-gate` 不在控制台侧处理**：它是纯浏览器 UI 门禁（`sessionStorage` 解锁），与「控制台 token 直达」叠加会变成二次登录，且不构成安全边界。**由 `dsh-docker` 仓库直接移除该插件**；控制台只导入「已移除 login-gate」的镜像，**不做派生、不改镜像内容**。导入页对镜像做一次提示性检查（`/opt/dsh-plugins/login-gate` 是否存在），存在则警告「该版本会二次登录」，但不阻止导入（管理员可自行决定）。

## 5. Caddy 动态路由

### 5.1 全量生成，而不是增量拼接

Caddy 的 HTTP 配置由控制面**整体生成**并 `POST /load` 到 Admin API，而不是逐条插路由：

- 路由顺序、兜底、TLS 自动化策略、访问日志全部在一个代码模板里，**不存在 Caddyfile 漂移**；
- 每次变更（实例启动/休眠/删除、域名变更）重建完整 JSON，Caddy 原子校验后热加载；校验失败保留旧配置并告警；
- 规模 ≤ 200 实例时全量 load < 100ms；超过 1000 实例再改增量（`@id` + PATCH）。
- 变更用 `lock:caddy:config` 串行化；load 前后读取 `GET /config/` 的 ETag/版本，供对账比对。

> 为什么不用 Caddyfile：Caddyfile 适合静态站点，但「按用户动态增删 host」会让「具体 host vs 通配 host 的匹配顺序」变成隐式行为；全量生成把顺序写成代码，可测试、可回放。

### 5.2 生成的配置结构

```json
{
  "admin": { "listen": "0.0.0.0:2019" },
  "apps": {
    "http": {
      "servers": {
        "srv0": {
          "listen": [":443"],
          "routes": [
            { "match": [{ "host": ["console.dsh.example.com"] }],
              "handle": [{ "handler": "subroute", "routes": [
                { "match": [{ "path": ["/api/*", "/ws/*"] }],
                  "handle": [{ "handler": "reverse_proxy",
                               "upstreams": [{ "dial": "console-api:8000" }],
                               "flush_interval": -1 }] },
                { "handle": [{ "handler": "encode", "encodings": { "zstd": {}, "gzip": {} } }] },
                { "match": [{ "path": ["/assets/*", "/favicon.ico"] }],
                  "handle": [{ "handler": "file_server", "root": "/srv/console-web" }] },
                { "handle": [
                    { "handler": "rewrite", "uri": "/index.html" },
                    { "handler": "file_server", "root": "/srv/console-web" }] }
              ] }],
              "terminal": true },

            { "match": [{ "host": ["alice.dsh.example.com"] }],
              "handle": [{ "handler": "reverse_proxy",
                           "upstreams": [{ "dial": "dsh-u-42:3080" }],
                           "flush_interval": -1 }],
              "terminal": true },

            { "match": [{ "host": ["bob.dsh.example.com"] }],
              "handle": [
                { "handler": "rewrite", "uri": "/w/{wake_key}" },
                { "handler": "reverse_proxy", "upstreams": [{ "dial": "console-api:8000" }] }
              ],
              "terminal": true },

            { "match": [{ "host": ["*.dsh.example.com"] }],
              "handle": [{ "handler": "static_response",
                           "status_code": 404, "body": "Not Found" }],
              "terminal": true }
          ],
          "logs": {
            "default_logger_name": "access",
            "logs": {
              "access": {
                "writer": { "output": "net", "address": "console-api:9001" },
                "encoder": { "format": "json" },
                "level": "INFO"
              }
            }
          }
        }
      }
    },
    "tls": {
      "automation": {
        "policies": [{
          "subjects": ["*.dsh.example.com", "console.dsh.example.com"],
          "issuers": [{ "module": "internal" }]
        }]
      }
    }
  }
}
```

要点：

- **顺序即代码顺序**：console → 运行中实例 → 休眠实例 → 泛域名 404。不依赖 Caddy 的自动排序，避免「通配把具体 host 吃掉」。
- SPA：`/assets/*` 与静态文件直接 `file_server`；其余路径 rewrite 到 `/index.html`（Vue Router history 模式）。`/api`、`/ws` 必须先匹配。
- `Host` 头保持原样（Caddy 默认），否则 DSH 信任围栏 403。
- `flush_interval: -1` 给 API（SSE）和实例（DSH 的长响应/SSE），避免被缓冲。
- TLS 用 `tls.automation.policies` 显式声明泛域名 + console，issuer = internal；换域名只改这一处。
- 访问日志走 `net` 到 API 的 TCP 收集器；API 不可达时 Caddy 丢弃日志而不阻塞请求，另有文件兜底（见 §8）。

> admin API 默认只监听 localhost；容器内必须 `admin 0.0.0.0:2019` 才能被 api 访问。**不要发布 2019 端口**，它只在 `dsh-console` 网络内可达。

### 5.3 运行中的用户路由

```json
{
  "match": [{ "host": ["alice.dsh.example.com"] }],
  "handle": [{
    "handler": "reverse_proxy",
    "upstreams": [{ "dial": "dsh-u-42:3080" }],
    "flush_interval": -1,
    "transport": { "protocol": "http", "dial_timeout": "5s" },
    "headers": {
      "request": {
        "set": {
          "X-Forwarded-Proto": ["https"],
          "X-Forwarded-Host": ["alice.dsh.example.com"]
        }
      }
    }
  }],
  "terminal": true
}
```

`Host` 头**必须保持原样**（Caddy 默认保留），否则 DSH 的 Host/Origin 信任围栏会 403。

### 5.4 休眠中的用户路由

```json
{
  "match": [{ "host": ["alice.dsh.example.com"] }],
  "handle": [
    { "handler": "rewrite", "uri": "/w/{wake_key}" },
    { "handler": "reverse_proxy", "upstreams": [{ "dial": "console-api:8000" }] }
  ],
  "terminal": true
}
```

`wake_key` 是 32 字节随机值（base64url），只出现在 Caddy 配置与 DB 中，用于防止任意人触发唤醒。唤醒页**不返回 token**，只负责拉起实例并引导回控制台（见 §6.3）。

### 5.5 路由生命周期

| 事件 | 动作 |
|---|---|
| 实例创建 | 生成「唤醒中」路由 |
| 容器 healthy | 换成容器上游 |
| 停止/休眠 | 换回唤醒路由 |
| 删除 | 移除该 host 路由 |
| Caddy 重启 | 检测配置为空/版本不符 → 从 DB 全量重放 |
| 定期对账 | 比对路由集合，不一致则重放（同一把锁） |

> Caddy 的 admin 配置**不持久化**，重启后为空。控制面在 worker 启动时与每 60s 对账时重建，通常在 3 秒内恢复。

## 6. 关键流程

### 6.1 创建实例

```
用户 POST /instances/me
  → 校验：无实例、配额、版本启用、用户名合法
  → DB 插入 instances(status=provisioning)
  → ARQ: instance.provision
      1. mkdir data/workspace，chown 1000:1000，写 .credentials.yaml? （由首启生成）
      2. docker create（§4 参数）+ labels
      3. 插入 Caddy 唤醒路由（此时访问子域名会显示"正在准备"）
      4. docker start
      5. 等 healthy（最多 90s）
      6. 读日志拿 token → Redis
      7. 换 Caddy 路由为容器上游
      8. status=running，SSE 推送
  → 失败：status=error，保留容器与日志，供管理员诊断
```

### 6.2 一键进入（token 注入）

```
用户点「打开」
  → POST /api/v1/instances/me/launch
      1. 若 stopped/sleeping → 触发 start/wake，返回 202 + 前端转圈
      2. 若 running → 生成 ticket（32B，Redis 60s，绑定 user+instance，单次）
      3. 返回 { "url": "https://console.../go/{ticket}" }
  → 浏览器导航到 /go/{ticket}
      1. 校验 ticket（GETDEL）
      2. 解析 token（Redis 缓存 → docker logs 兜底）
      3. 302 → https://alice.dsh.example.com/?token={43字符}
         Cache-Control: no-store, Referrer-Policy: no-referrer
  → DSH 校验 token → Set-Cookie（HttpOnly, SameSite=Strict）→ 302 /
```

**为什么必须 302 到子域名**：DSH 的 cookie 只能在它自己的 authority 上种；控制台域无法替子域种 cookie。

**Token 解析**：

1. Redis `dsh:token:{instance_id}`（TTL = 容器生命周期）；
2. 未命中 → `docker logs --since {started_at} --tail 1000`，正则 `[?&]token=([A-Za-z0-9_-]{20,})`；
3. 仍未命中（日志被轮转）→ 返回 `TOKEN_UNAVAILABLE`，前端提示「重启实例后重试」，提供一键重启。

**残留风险**：token 会短暂出现在浏览器历史与（若用户复制）地址栏。缓解：`no-store`、`no-referrer`、token 随进程重启失效、30 天 cookie 让用户很少再走这条路。**不把 token 写进控制台 API 响应体**，只存在于 302 的 Location 中。

### 6.3 休眠与唤醒

**活跃判定**（worker 每 30s）：

- Caddy 访问日志里该 host 的任意请求 → 更新 `last_active_at`；
- Docker stats 网络增量 > 64 KB / 30s（覆盖 WebSocket/SSE 长连接心跳）；
- 两者都无，且超过 `idle_timeout`（默认 30 分钟）→ 休眠。

**休眠**：`docker stop`（15s 宽限）→ `status=sleeping` → Caddy 换唤醒路由 → 不扣时长。

**唤醒**：

```
访问 alice.dsh.example.com（休眠中）
  → Caddy → console-api /w/{wake_key}
  → 返回 200 HTML（"正在唤醒…"）+ JS 轮询 /w/{wake_key}/status
  → 后端投递 instance.wake（加锁，幂等）
  → 容器 healthy 后：Caddy 换回容器路由
  → 轮询返回 {state:"running", redirect:"https://alice.dsh.example.com/"}
  → 浏览器跳转：
      已有 DSH cookie → 直接进 GUI
      没有 cookie    → 401 页面，引导到 console 登录后「打开」
```

**为什么唤醒页不直接发 token**：子域名是公开可达的，任何知道用户名的人都能访问；若唤醒页下发 token，等于绕过控制台登录。已有 cookie 的用户（绝大多数）无感；新设备走控制台登录。

### 6.4 计量与配额

```
Caddy 日志 ──> LogCollector(TCP 9001) ──> Redis last_active / 请求计数
Docker stats 每 30s ──> worker ──> Redis 计数器（按小时）
                                   │
                    每 60s flush ──┴──> MySQL usage_bucket（UPSERT）
                                   │
                quota 检查（每 60s）──> seconds_used + remaining
```

- **活跃秒**：某分钟内至少一次请求或网络活动 → 该分钟计 60 秒（按 30s tick 累计，误差 ≤ 30s/次）。
- **CPU 核时**：`cpu_percent/100 * 30s` 累加；`cpu_percent` 已按整机核数归一化。
- **内存 GB·时**：`mem_usage_bytes/1e9 * 30s`。
- **磁盘**：每小时 `du -sb` 一次，记峰值；不参与秒级计费，只做配额展示与告警。
- **配额扣减**：只扣活跃秒（决策 #5）。`remaining = plan.monthly_minutes*60 + grants - used`。
- **阈值**：80% 站内提醒；100% 停止实例并置 `quota_exhausted`，管理员加时后用户可自行启动。
- **周期重置**：自然月 1 日 00:00 `Asia/Shanghai`，写 `quota_ledger(reset)`，清零 `seconds_used`（历史 bucket 保留）。

### 6.5 快照

```
POST /instances/me/snapshots
  → 加锁 → docker pause（10s 超时）
  → tar --zstd -C instances/{uid} data workspace
      --exclude=data/.runtime --exclude=**/node_modules/.cache
  → 写 manifest.json（dsh_version、image_digest、文件数、sha256）
  → docker unpause
  → snapshots(status=ready)
```

- 归档落 `$DATA_ROOT/snapshots/{uid}/{id}.tar.zst`；单文件直读，不经 Docker。
- **恢复**：stop → 当前目录重命名为 `.restore-backup-{ts}` → 解包 → `chown -R 1000:1000` → start → healthy 检查；失败自动回滚并保留现场。
- 保留：套餐 `max_snapshots`；超限拒绝新建；删除用户后随数据保留期一起 GC。

### 6.6 用户镜像（commit）

```
POST /instances/me/images
  → 校验配额/名称
  → docker commit
      --change 'ENV DEEPSEEK_API_KEY='
      --change 'ENV LITELLM_API_KEY='
      --change 'LABEL dsh.console.user_image=true'
      --change 'LABEL dsh.console.base_version={version}'
      --change 'LABEL dsh.console.owner={user_id}'
      container  dsh-user/{username}:{yyyymmddHHMM}
  → inspect 大小/digest → user_images(status=ready)
```

**关键事实**：`/data/dsh` 与 `/workspaces` 是 bind mount，**不会进入镜像**。所以 commit 只保存系统层（apt/pip/npm 安装、`/etc` 配置等），这正是「环境镜像」的语义；要连数据一起保存用快照。UI 必须写清楚这一点。

### 6.7 镜像版本导入

| 方式 | 流程 |
|---|---|
| Registry | `docker pull repo:tag`（凭据来自 `settings` 或环境变量）→ inspect → 记录 digest → 校验 label `dsh.version` |
| 上传 tar | 分片上传到 `$DATA_ROOT/images/upload/{id}` → 合并 → sha256 → `docker load` → inspect → 入库 |
| 校验 | 必须能 `docker run --rm image dsh --version`；可选 `dsh web --dump-config` 冒烟 |

- 同一 digest 唯一；重复导入返回已有记录。
- `status=disabled` 的版本不能新建/切换，但已运行实例不动。
- 删除版本 = 先从库里移除引用，再由 GC `docker rmi`（有实例引用则跳过并提示）。

### 6.8 对账（reconcile）

每 60s 与启动时执行：

| 检查 | 修正 |
|---|---|
| DB `running` 但容器不存在/已退出 | 置 `error`，记事件，通知管理员 |
| 容器存在但 DB 无记录 | 打标 `orphan`，7 天后 GC（不自动删，防误删） |
| DB `running` 但 Caddy 路由缺失/错误 | 重插路由 |
| Caddy 有路由但实例已停/删 | 删除路由 |
| 容器 healthy 但 token 缓存缺失 | 重新解析 |
| 实例超配额 | 停止并置 `quota_exhausted` |

## 7. 认证与授权

- **会话**：登录成功 → 服务端 session（Redis，7 天滑动）→ cookie `dshc_sid`（HttpOnly、Secure、SameSite=Lax、Path=/）。
- **CSRF**：`GET /auth/csrf` 下发 token；所有写请求要求 `X-CSRF-Token`；与 session 绑定，双提交校验。
- **密码**：argon2id（memory 64MB, iterations 3）；失败计数 Redis（IP 5/min、账号 10/h），超限 429 + 指数退避。
- **邀请码**：`invites` 表；一次性或限次；注册时事务内 `used_count+1` 防并发超发。
- **权限**：路由级 `require_user` / `require_admin`；管理员操作全部写审计。
- **管理员终端**：独立开关 + 审计；WS 握手校验 session + CSRF 一次性票据。

## 8. 监控采集

| 指标 | 来源 | 频率 | 存储 |
|---|---|---|---|
| 宿主机 CPU/内存/磁盘/网络/负载 | node-exporter | 30s | `node_metric`（7 天）+ 小时聚合（1 年） |
| 容器 CPU/内存/网络/磁盘写 | Docker stats | 30s | Redis 实时 + `usage_bucket`（小时） |
| 实例状态 | 事件 + 对账 | 事件驱动 | `instance_events`（90 天） |
| 请求量/延迟/错误 | Caddy access log + API 中间件 | 实时 | Redis 分钟桶 + Prometheus |
| 业务大盘 | SQL 聚合 | 按需 | 视图/缓存 30s |

GPU（二期）：`dcgm-exporter` → `node_metric.gpu` JSON；`plan.gpu_count`、`instance.gpu_ids` 已预留。

## 9. 安全边界

必须写清楚，因为这套设计的信任模型不对称：

1. **`docker.sock` = root**。`console-api` 能创建任意容器，因此它就是宿主 root。防护：不暴露到公网（只被 Caddy 反代）、镜像最小化、API 参数全部白名单（用户不能传任意 `binds`/`privileged`/`cap_add`）、管理接口仅 `admin`。
2. **`DATA_ROOT` 挂进 API**。文件管理因此能直接读写；路径必须 `realpath` 后校验前缀，禁止符号链接逃逸。
3. **实例之间共享内核**。无特权 + cap_drop + no-new-privileges + pids/mem 限制 + 默认 seccomp；不防内核 0day，这是容器多租户的固有风险，需在用户协议中说明。二期可评估 userns-remap / gVisor / Kata。
4. **平台 Key** 会被注入所有未自带 Key 的实例；容器内可见（环境变量）。这是平台统一 Key 的固有代价；用户自带 Key 可避免。日志与审计永不打印 Key。
5. **Token 暴露面**：只在 302 Location 与容器日志里；控制台 API 不返回；日志采集只取 token 用于缓存，不落库。
6. **Caddy admin API** 在容器内监听 `0.0.0.0:2019` 仅为让 `console-api` 通过 `dsh-console` 网络访问；该端口**绝不发布到宿主**，也不加入 `dsh-edge`。Caddy admin 无鉴权，能访问它 = 能改全部路由。
7. **上传**：文件名净化、大小限制、类型不信任、解包时防 zip-slip。
8. **审计**：append-only（应用层禁止 UPDATE/DELETE，DB 账号无 DELETE 权限可选）。
9. **平台 Key 对用户可见**：`DEEPSEEK_API_KEY` 是容器环境变量，用户在自己的实例里执行 `env` 就能看到（agent 有 shell）。这是「平台统一 Key」的固有缺陷，不是实现 bug。一期对策：平台 Key 只对可信用户开放 + 设消费上限 + 审计；`llm-gateway`（每实例随机虚拟 key，真实 Key 留在网关）已列为 **1.5 期第一项**，见 06 文档 R-01 / §2.2。
10. **禁用/删除用户必须吊销已发出的 DSH cookie**：DSH 的 cookie 默认 30 天且由容器内的 `.credentials.yaml` 签名，停容器/删路由都不足以阻止「重新启动后旧 cookie 仍有效」。正确动作是**轮换该实例的 `$DSH_HOME/.credentials.yaml`**（删除后下次启动重新生成），使全部旧 cookie 立即失效；同时清 Redis 会话、停容器、删 Caddy 路由。见 06 文档 R-03。

## 10. 容量与规格（弹性，不针对某一台机器适配）

**不做机型适配**：控制面在启动时与每 30s 探测宿主容量，准入按实时余量计算，同一套代码在 8 核 16G 的笔记本和 64 核 256G 的服务器上都能跑。

探测来源：

| 指标 | 来源 |
|---|---|
| CPU 核数 | Docker `info.NCPU` |
| 内存总量 | Docker `info.MemTotal`（cgroup 限制内） |
| 数据盘容量/余量 | `statfs(DATA_ROOT)` |
| 实时负载 | node-exporter（`/metrics`），不可用时退化为 Docker info + psutil |
| 已分配 | `SUM(instances.memory_mb / cpu_cores / disk_gb)`，按状态排除 `deleted` |

准入公式（创建、改套餐、版本切换时校验）：

```
mem_total      = Docker info MemTotal
mem_reserve    = max(4096 MB, 10% * mem_total)        # 可配
mem_committed  = Σ memory_mb（status != deleted）
mem_ok         = mem_committed + new_mem <= mem_total - mem_reserve

cpu_committed  = Σ cpu_cores
cpu_ok         = cpu_committed + new_cpu <= NCPU * CPU_OVERCOMMIT   # 默认 4.0

disk_ok        = Σ disk_gb + new_disk <= 数据盘容量 * 0.90
```

- CPU 允许超卖（默认 4×，因为实例大部分时间空闲），**内存不超卖**——内存打满会 OOM 掉别人的实例。
- 任一不满足返回 `409 INSTANCE_QUOTA_MEMORY`，`details` 里带上 `total/committed/required`，前端显示「宿主余量不足，请联系管理员」。
- 容量快照缓存在 Redis（30s），管理端健康页直接展示；不落库、不做历史（历史看 `node_metric`）。

默认套餐只是**种子数据**，管理员可随时增删改（`plans` 表），不绑定任何机型：

| 套餐 | CPU | 内存 | 磁盘 | 月时长 | 快照 | 镜像 |
|---|---|---|---|---|---|---|
| 基础 | 1 核 | 2 GB | 20 GB | 60 h | 2 | 2 |
| 标准 | 2 核 | 4 GB | 50 GB | 120 h | 4 | 4 |
| 高性能 | 4 核 | 8 GB | 100 GB | 240 h | 8 | 8 |

实例首次启动的额外开销：拉镜像（若本地无）、`dsh` 首次初始化 profile（约 10–30s）、Chrome/LibreOffice 不用时不占内存。这些不写进准入公式，靠 `start_period` 与健康检查吸收。

## 11. 多节点演进（二期，只做接口）

```python
class NodeDriver(Protocol):
    async def create(...) -> ContainerHandle: ...
    async def start/stop/remove(...): ...
    async def stats/logs/exec/commit(...): ...
```

- `nodes` 表：`id, name, driver('docker'|'k8s'), endpoint, labels, capacity, status, last_heartbeat`。
- 调度器一期只有 `local` 节点；二期按 `plan` 与剩余内存选节点。
- Caddy 路由的 upstream 改为 `{node_addr}:{port}` 或 K8s Service DNS；其余业务层不变。
- 明确：**多节点不自己造调度器**，直接上 K8s；DockerDriver 只服务单机。

## 12. 故障模式

| 故障 | 影响 | 处理 |
|---|---|---|
| API 崩溃 | 控制台不可用；运行中实例不受影响 | 容器 `restart: unless-stopped`；恢复后对账 |
| Worker 崩溃 | 启停/计量暂停 | 队列持久化；重启后重放；计量最多丢 5 分钟 |
| Caddy 崩溃/重启 | 全部入口短暂 502 | 重启后 60s 内重建路由 |
| MySQL 宕机 | 控制台只读/不可用；实例继续跑 | 健康检查降级提示；恢复后对账 |
| Redis 宕机 | 会话丢失（需重登）、计量暂停 | AOF 恢复；token 缓存可从日志重建 |
| Docker daemon 重启 | 容器按 `restart:no` 不会自启 | 对账把 DB 期望 `running` 的实例重新拉起（可配） |
| 磁盘满 | 快照/上传失败；容器可能写失败 | 提前 85% 告警；GC；拒绝新快照 |
| 单实例 OOM | 只影响该实例 | `mem_limit` 已隔离；事件记录，通知用户 |
| token 丢失 | 用户无法一键进入 | 自动重启实例重新生成 |
