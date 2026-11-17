# 05 部署与运维

> 目标：一台 Linux 物理机（内核 ≥ 5.13、Docker 24+、Compose v2），内网 DNS 解析 `*.dsh.example.com`。

## 1. 拓扑

```
/srv/dsh-console/
├── compose.yaml
├── .env
├── web/                     # 前端源码（构建进 Caddy 镜像）
├── api/                     # FastAPI
└── data/                    # 运行时数据（见 02 §2.2）
    ├── instances/{uid}/{data,workspace}
    ├── snapshots/
    ├── images/upload/
    ├── plugin-sets/
    └── secrets/master.key
```

compose 服务（M3+）：

| 服务 | 镜像 | 端口 | 说明 |
|---|---|---|---|
| `console-web` | 自建（Caddy + 前端 dist） | `443:443` | 唯一入口；动态路由由 API 生成 |
| `console-api` | 自建（python） | 内网 8000 | 挂 `docker.sock`、`./data`；控制面 |
| `console-worker` | 同上，`python -m app.worker` | 内网 9001 | 计量采样/落库、休眠、配额、节点指标、Caddy 日志 |
| `mysql` | `mysql:8.4` | 内网 3306 | 数据卷 |
| `redis` | `redis:7-alpine` | 内网 6379 | AOF everysec |

`console-web` 与用户实例同在 `dsh-edge` 网络；实例不发布端口，只由 Caddy 按子域名反代。

> `console-api` 挂载 `docker.sock` 等于宿主 root 权限。它只能被 Caddy 访问，**不要发布 8000**，也不要给它加额外挂载。

## 2. 本地开发（无 Docker / MySQL / Redis）

```bash
cd api
python -m pip install -e ".[dev]"
DATABASE_URL=sqlite:///./dev.db REDIS_FAKE=1 ENV=dev DOCKER_FAKE=1 CADDY_ENABLED=0 JOBS_INLINE=1 \
  DATA_ROOT=./data BOOTSTRAP_ADMIN_PASSWORD=admin12345 \
  uvicorn app.main:app --reload --port 8000

cd ../web
npm install && npm run dev      # http://127.0.0.1:5173
```

- `ENV=dev` 自动建表并注入种子数据（三个套餐 + 默认设置 + 管理员）。
- `REDIS_FAKE=1` 用 fakeredis 顶替 Redis，登录限流照常工作。
- `DOCKER_FAKE=1` 用内存驱动模拟容器；`JOBS_INLINE=1` 让创建/启停同步完成，便于本地和测试。
- 计量/休眠需要 worker，可另开一个终端：`python -m app.worker`（同样带上上面的环境变量；本地可设 `CADDY_ACCESS_LOG_ENABLED=0`）。
- 前端 Vite 已把 `/api` 代理到 `127.0.0.1:8000`，无需 CORS。
- 测试：`cd api && pytest -q`。
- 生产镜像走 `alembic upgrade head`，不依赖 `create_all`。

## 3. 宿主机准备

```bash
# 1. 内核与 Docker
uname -r                       # >= 5.13（Landlock）
docker version                 # >= 24
docker compose version         # >= v2.20

# 2. 目录与权限
sudo mkdir -p /srv/dsh-console/data/{instances,snapshots,images/upload,plugin-sets,secrets}
sudo chown -R 1000:1000 /srv/dsh-console/data
sudo chmod 700 /srv/dsh-console/data/secrets

# 3. inotify：每个实例的 dsh 都会 watch 文件；N 个实例会线性放大
sudo sysctl -w fs.inotify.max_user_watches=524288
sudo sysctl -w fs.inotify.max_user_instances=1024
printf '%s\n' 'fs.inotify.max_user_watches=524288' 'fs.inotify.max_user_instances=1024' \
  | sudo tee /etc/sysctl.d/99-dsh.conf

# 4. 443 空闲
sudo ss -ltnp 'sport = :443'
```

### 3.1 `.env`

```ini
# ── 基础（开源使用者改这里）──────────────────────────
CONSOLE_BASE_DOMAIN=dsh.example.com    # 占位符，换成自己的域名
CONSOLE_DOMAIN=console.dsh.example.com
CONSOLE_HTTPS_PORT=443
CONSOLE_NAME=优墨云舱
ENV=prod
LOG_LEVEL=INFO

# TLS: internal_ca（默认，离线可用）。acme 在 M2 与动态路由一起接入。
CONSOLE_TLS_MODE=internal_ca

# ── 安全（必须改，且备份 master key！）────────────────
SESSION_SECRET=<32B base64url>        # 会话/CSRF HMAC
MASTER_KEY=<32B base64>               # 模型 Key AES-256-GCM；丢失=所有 Key 不可解
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<强密码>      # 仅首次为空库时创建
COOKIE_SECURE=true                     # 本地 http 调试时设 false

# ── MySQL ────────────────────────────────────────────
MYSQL_ROOT_PASSWORD=<强密码>
MYSQL_DATABASE=dsh_console
MYSQL_USER=dsh
MYSQL_PASSWORD=<强密码>

# ── 容量准入（弹性，不绑定机型）──────────────────────
MEMORY_RESERVE_MB=4096                 # 预留下限；实际取 max(此值, 内存*10%)
MEMORY_RESERVE_PERCENT=10
CPU_OVERCOMMIT=4.0                     # CPU 可超卖倍数；内存不超卖
DISK_USE_RATIO=0.90                    # 数据盘分配上限
```

> 容量不是配置出来的，而是**探测出来的**：`docker info` 的 NCPU/MemTotal + `statfs(DATA_ROOT)`，每 30s 刷新。上面四个参数只决定「留多少余量、能超卖多少」，因此同一份 compose 可以跑在任何规格的机器上。

> `MASTER_KEY` 与 `SESSION_SECRET` 必须离线备份（密码管理器/纸质）。丢失 `MASTER_KEY` 后所有模型 Key 需要重新录入；丢失 `SESSION_SECRET` 只是全员重新登录。

### 3.2 启动

```bash
cd /srv/dsh-console
docker compose up -d --build
docker compose logs -f console-api     # 等 alembic upgrade head 完成、/readyz 200
```

首次启动自动：

1. 建表（Alembic，含 `dsh_images` / `instances` / `instance_events`）；
2. 种子套餐与默认设置；
3. 若 `users` 为空，用 `BOOTSTRAP_ADMIN_*` 建管理员；
4. 创建 `dsh-edge` 网络，并向 Caddy 下发全量配置（console + 已有实例路由）。

## 4. 导入第一个 DSH 版本

```bash
# 1. 先把镜像拉到本机（控制台不负责 pull）
docker pull ghcr.io/you/dsh:0.2.1-rc.5

# 2. 在控制台「版本」页登记，或调 API（M2 的登记接口）
curl -X POST https://console.dsh.example.com/api/v1/admin/images \
  -H 'Content-Type: application/json' -b cookie.txt -H "X-CSRF-Token: $CSRF" \
  -d '{"image_ref":"ghcr.io/you/dsh:0.2.1-rc.5","version":"0.2.1-rc.5",
       "changelog":"首个版本","is_default":true}'
```

> M2 只做「登记本机已有镜像」；从 Registry 拉取、tar 上传、digest/签名校验在 M4 补齐。
> 离线环境：`docker load < dsh.tar` 后再登记本地 tag。

**镜像从哪来**：由 `dsh-docker` 的 CI（`build-image.yml`）构建并推 GHCR。控制台**只导入，不构建、不派生、不修改镜像**——包括 `login-gate` 的移除也在 `dsh-docker` 仓库里完成（控制台会把「镜像里还存在 `login-gate`」作为导入时的警告显示出来）。

```bash
# 在 dsh-docker 侧构建（版本号来自 .env.example 的 DSH_VERSION）
docker build -t ghcr.io/you/dsh:0.2.1-rc.5 .
docker push ghcr.io/you/dsh:0.2.1-rc.5
```

控制台导入后 `docker pull` 到本地；`docker save` 的 tar 只用于离线环境。

## 4.1 用 GitHub Actions 构建控制台镜像

仓库自带 `.github/workflows/build-images.yml`，推 `main` 或打 `v*` tag 时自动构建并推 GHCR：

| 镜像 | 构建上下文 | 产物 |
|---|---|---|
| `dsh-console-api` | `api/`（`api/Dockerfile`） | `ghcr.io/<owner>/dsh-console-api:<tag>` |
| `dsh-console-web` | 仓库根（`deploy/Dockerfile.web`） | `ghcr.io/<owner>/dsh-console-web:<tag>` |

标签规则：分支名、`sha-<commit>`、`v1.2.3` → `1.2.3` / `1.2`、默认分支额外打 `latest`。PR 只构建不推送，缓存用 GitHub Actions cache。

- `verify` 任务先跑 `ruff` + `pytest`（SQLite/fakeredis/FakeDriver，无需服务）+ 前端 `typecheck`/`build`，失败则不构建镜像。
- 私有 PyPI 镜像或内网 Nexus：设置仓库变量 `PIP_INDEX_URL`，它会作为 `--build-arg` 传给 `api` 镜像。
- 拉取私有镜像：`echo "$GITHUB_TOKEN" | docker login ghcr.io -u <user> --password-stdin`。

`security.yml` 另跑三项守护：`scripts/check_no_secrets.py`（扫描凭据、带密码的 DSN、公网 IP，工作区与全部历史各扫一遍）、Python `pip-audit`、`npm audit`（后两者仅告警不阻塞）。

> 本地也可以随时执行同样的检查：`python scripts/check_no_secrets.py --history`。

## 5. 证书（两种模式，按 `CONSOLE_TLS_MODE` 切换）

### 5.1 `internal_ca`（默认，内网/离线/开源演示）

Caddy 用内置 CA 为 `*.{base_domain}` 与 `console.{base_domain}` 签发泛域名证书：

```bash
docker compose cp console-web:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
openssl x509 -in caddy-root.crt -noout -subject -issuer   # 应为 Caddy Local Authority
```

- Windows：双击 → 本地计算机 → 受信任的根证书颁发机构；
- macOS：钥匙串 → 系统 → 始终信任；
- Linux：`sudo cp caddy-root.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`。

> 换域名 = 重新签一次，客户端重新信任。`docker compose down -v` 会删 CA 卷，全员需重新信任——生产环境务必备份 `caddy-data` 卷。

### 5.2 `acme`（公网域名，M2 接入）

> M2 的配置生成同样固定 `internal` issuer；`CONSOLE_TLS_MODE=acme` 在后续版本接入。下面是要点，先记录避免返工。

- `*.{base_domain}` 的 DNS 解析指向本机，且 80/443 可达；
- 泛域名**必须用 DNS-01**（HTTP-01 不支持通配），需要配 `ACME_DNS_PROVIDER` 与对应凭据（Cloudflare/阿里云等 Caddy 插件）；
- 首次签发需要出网；离线环境不要用这个模式。

> 开源项目默认 `internal_ca`，因为它不需要任何公网依赖；文档中的 `dsh.example.com` 全部是占位符。

## 6. 备份与恢复

### 6.1 备份

```bash
# 每日 02:00（crontab -e）
0 2 * * * docker exec dsh-console-mysql-1 sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines dsh_console' \
  | zstd > /backup/mysql-$(date +\%F).sql.zst

0 3 * * * rsync -a --delete /srv/dsh-console/data/snapshots/ /backup/snapshots/
```

必须单独备份：`/srv/dsh-console/data/secrets/master.key`、`.env`、Caddy 的 `caddy-data` 卷（CA 与私钥）。

### 6.2 恢复

```bash
docker compose stop console-api
zstd -d < /backup/mysql-2026-11-17.sql.zst | docker exec -i mysql sh -c \
  'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" dsh_console'
# 恢复 master.key 与 snapshots
docker compose up -d
# 对账：让 DB 状态重新收敛到 Docker/Caddy（M2 起可用）
curl -X POST .../admin/system/reconcile
```

**演练**：每季度在测试机恢复一次；重点验证 `master.key` 能解密旧 Key、快照能恢复。

## 7. 升级

```bash
cd /srv/dsh-console
git pull
docker compose build console-api console-web
docker compose up -d
docker compose logs -f console-api | grep -E 'alembic|ready'
```

- API 启动时自动 `alembic upgrade head`；破坏性迁移分成两个版本发布（先加列，再删列）。
- 升级控制面**不影响**运行中的实例（容器独立）。
- 升级 dsh 版本：导入新镜像 → 设默认 → 用户自选或管理员批量迁移（M4 起）。

## 8. 日常运维

| 任务 | 方式 |
|---|---|
| 看系统健康 | `GET /admin/system/health` 或控制台首页 |
| 三方对账 | M2 起每 60s 自动；异常时 `POST /admin/system/reconcile` |
| 清理悬空资源 | M5 起 `POST /admin/system/gc`（先 `dry_run:true`） |
| 禁用用户 | `PATCH /admin/users/{id}` → 停容器 + 删 Caddy 路由 + 轮换 `.credentials.yaml`（吊销已发 cookie，M2 起自动化） |
| 强制下线 | 删除该用户全部会话（禁用/改密会自动做） |
| 扩容 | 加内存/磁盘后**无需改配置**：容量每 30s 自动重探，准入随之放宽 |
| 日志 | `docker compose logs -f console-api`；JSON 带 `request_id` |
| 监控 | M5 起 `/metrics`（Prometheus），接 Grafana |

## 9. 排障

| 现象 | 优先检查 |
|---|---|
| 控制台 502 | `docker compose ps`；caddy 到 api 的网络；`CONSOLE_DOMAIN` |
| 登录 401 循环 | `COOKIE_SECURE=true` 但用的是 http；改成 false 或上 HTTPS |
| 登录 403 `AUTH_CSRF_INVALID` | 前端是否带 `X-CSRF-Token`；刷新页面重新取 |
| 注册提示邀请码无效 | 邀请码过期/用尽/已吊销；看 `GET /admin/invites` 的 status |
| 管理端 403 | 账号 `role` 不是 `admin`；或被禁用 |
| 500 `INTERNAL_ERROR` | 用响应里的 `request_id` 搜 API 日志 |
| 启动卡在 `alembic` | MySQL 未健康；`docker compose logs mysql` |
| 中文乱码 | 数据库/连接必须是 `utf8mb4`；本仓库 DDL 已指定 |
| 时间偏差 | 容器 `TZ=Asia/Shanghai`；DB 存 UTC，展示转本地 |
| 点「打开」后停在登录页 | Caddy 的 console 站点必须把 `/go/*`、`/w/*` 反代给 API；只代理 `/api/*` 时 SPA 会吃掉这两个路径（返回 200 的 index.html 而不是 302） |
| 用户子域名 404 / 空白 200 | 用 IP 访问时 Host 匹配不到站点，属正常：必须用子域名，或带 `Host: console.<域名>` 头 |
| 停止/启动返回 409 `INSTANCE_BUSY` | 该实例正有操作在进行（快照恢复后会先重启）。前端应重试，这是预期语义，不是失败 |
| `docker commit` 报 `content digest ... not found` | 宿主镜像存储缺 blob（本机实测 `dsh` 镜像如此，其他镜像正常）。修复：`docker save <image> \| docker load` 重新入库 |
| `docker commit`/`pull` 超时 | 大镜像远超默认超时；已由 `DOCKER_COMMIT_TIMEOUT`(1800s)/`DOCKER_PULL_TIMEOUT`(3600s) 覆盖，可再调大 |
| 终端 500 / 握手失败 | WebSocket 路由不能挂 HTTP 的 CSRF 依赖；`ws_router` 单独注册，靠 cookie 鉴权 |

## 10. 安全清单（上线前逐项）

- [ ] `MASTER_KEY` / `SESSION_SECRET` 已改默认值并离线备份
- [ ] MySQL/Redis 未发布端口，密码强随机
- [ ] `docker.sock` 只挂给 `console-api`，未挂给任何实例（M2）
- [ ] 实例 `cap_drop ALL`、`no-new-privileges`、内存/CPU/pids 限制已生效（M2，`docker inspect` 抽查）
- [ ] 控制台仅 HTTPS；HSTS 开启；`Secure` cookie
- [ ] 管理端 IP 白名单（可选，Caddy `remote_ip` matcher）
- [ ] 首个管理员密码已改；邀请码有有效期
- [ ] 日志中无 API Key / token（抽查 `docker compose logs`）
- [ ] 备份任务在跑，恢复演练通过
- [ ] 磁盘/内存告警阈值已配
