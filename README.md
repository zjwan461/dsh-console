# 优墨云舱（dsh-console）

> 优墨系列第三款：给每个人一间随开随用的云端工作舱。

把 [`dsh-docker`](../dsh-docker) 的单用户部署，变成**多用户、可计量、可运维**的「DSH 云电脑」平台：

- 用户登录控制台 → 看到自己的 harness 实例 → 一键启停 / 进入 / 看用量 / 存快照；
- 管理员维护 DSH 镜像版本、分配套餐与时长、看全机与全站监控。

**为什么叫「云舱」**：每个用户拿到的不是一个抽象容器，而是一间独立的舱——有自己的子域名、数据目录、配额与时长；离开时自动休眠，回来点一下就能继续。舱与舱之间隔离，管理员在舱外统一调度。英文名可写作 **YouMo Cloud Cabin**。

> **当前状态：M1–M5 已完成，并在真实环境跑通端到端回归。**
>
> - 功能：认证/用户/套餐/配额/审计、实例生命周期与 Caddy 路由、计量/休眠/配额扣减、快照/镜像/版本切换、文件管理、管理员终端、监控大盘、公告/工单。
> - 验证：单元测试 17 条（SQLite + fakeredis + FakeDriver）；真机回归脚本 `scripts/e2e_remote.py`（Docker 29 + MySQL 8 + Redis 7）与 `scripts/e2e_terminal.py`，结果见 [路线图 §5](docs/06-roadmap.md)。
> - 真机回归修掉的 5 个 bug（Caddy 日志配置、`/go` 路由、实例锁、commit 超时、WS 鉴权）也记录在路线图里。

## 快速开始（Docker）

```bash
cp .env.example .env
# 必改：SESSION_SECRET / MASTER_KEY / MYSQL_* / BOOTSTRAP_ADMIN_PASSWORD
# 若项目不在 /srv/dsh-console，把 HOST_DATA_ROOT 改成 "<项目绝对路径>/data"
mkdir -p data
docker compose up -d --build
docker compose logs -f console-api
```

打开 `https://console.dsh.example.com`（先把该域名解析到本机；Caddy 用内部 CA，需要信任根证书，见 [05 文档](docs/05-deployment-ops.md)）。

先以管理员登录 → **版本** → 登记一个已经 `docker pull` 到本机的镜像（例如 `ghcr.io/you/dsh:0.2.1-rc.5`），再让用户创建实例。

> M2 起 `console-api` 挂载 `/var/run/docker.sock` 与 `./data`，并加入 `dsh-edge` 网络。这是 root 等价权限，务必只让 Caddy 访问 API，不要把 8000 端口发布出去。

## 本地开发（不需要 Docker / MySQL / Redis）

```bash
cd api
python -m pip install -e ".[dev]"
DATABASE_URL=sqlite:///./dev.db REDIS_FAKE=1 ENV=dev DOCKER_FAKE=1 CADDY_ENABLED=0 JOBS_INLINE=1 \
  DATA_ROOT=./data \
  uvicorn app.main:app --reload --port 8000
# 首次启动会用 BOOTSTRAP_ADMIN_PASSWORD 建管理员；未设置时随机生成并打印在日志里
```

> `DOCKER_FAKE=1` 用内存驱动模拟容器（创建/启停/日志 token 都可用），因此**不装 Docker 也能走通创建实例 → 打开 → 停止的完整链路**。

```bash
cd web
npm install
npm run dev          # http://127.0.0.1:5173，/api 代理到 :8000
```

- `ENV=dev` 时自动 `create_all`，不需要 Alembic；生产由容器 entrypoint 跑 `alembic upgrade head`。
- `REDIS_FAKE=1` 使用进程内 fakeredis，登录限流与会话不受影响。
- 计量与休眠需要 worker：另开终端 `python -m app.worker`（同样环境变量，本地加 `CADDY_ACCESS_LOG_ENABLED=0`）。
- 接口文档：`http://127.0.0.1:8000/api/docs`。

## 真机回归脚本

`scripts/` 下是可复现的远端验证工具（不依赖本地 Docker）。**所有主机信息都从环境变量读取，仓库里不含任何 IP、端口或口令。**

```bash
cp scripts/.remote.env.example scripts/.remote.env   # 该文件已被 .gitignore 忽略
$EDITOR scripts/.remote.env                          # 填自己的 Docker/MySQL/Redis/域名
```

| 脚本 | 作用 |
|---|---|
| `provision_remote.py` | 建网络 `dsh-console` / `dsh-edge`、数据目录、专用 Redis |
| `build_remote.py` | 把 `api/` 与 `web/dist` 打成 tar 流，在远端 daemon 构建镜像 |
| `deploy_remote.py` | 起/更新 `console-api`、`console-worker`、`console-web`（纯 HTTP 模式，便于用 IP 验收） |
| `e2e_remote.py` | 端到端回归：容器资源限制、dsh 探活、token 302、计量落库、快照往返、文件越界、wake 页、停机/启动 |
| `e2e_terminal.py` | 管理员 WebSocket 终端（真实 `docker exec`） |
| `remote_exec.py` | 在远端容器里跑一段本地 Python 文件（排障用） |
| `clear_login_limit.py` | 清掉 Redis 里的 `rl:login:*`（回归脚本反复登录会触发限流） |

> 回归脚本每次运行都会真实登录，`LOGIN_RATE_PER_HOUR` 很容易打满；被限流时先跑 `clear_login_limit.py`，或临时调大该变量。

## 技术栈（已确认）

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Pinia + Vue Router + Element Plus + ECharts | 控制台 SPA；监控图用 ECharts |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 | 同步 SQLAlchemy（线程池执行）；docker-py 走 unix socket |
| 任务 | `console-worker`（asyncio 常驻循环） | 计量采样/落库、休眠、配额、节点指标、Caddy 日志采集。未引入 ARQ：任务都是周期性的，换队列不影响业务层 |
| 数据库 | MySQL 8.4 | 业务数据；utf8mb4 |
| 缓存/队列 | Redis 7 | 会话、token 缓存、计数器、分布式锁、ARQ |
| 容器 | Docker Engine API（`/var/run/docker.sock`） | 单机；后期换 K8s 时加 Driver |
| 入口 | Caddy 2（管理 API 动态路由） | 唯一 TLS 终结者；泛域名内部 CA |

## 文档索引

| 文档 | 内容 |
|---|---|
| [01 需求与范围](docs/01-requirements.md) | 角色、用户故事、验收标准、一期/二期边界、已确认决策记录 |
| [02 架构设计](docs/02-architecture.md) | 组件、网络、Caddy 动态路由、token 注入、休眠唤醒、计量、快照、安全边界 |
| [03 数据模型](docs/03-data-model.md) | MySQL DDL、Redis 键、状态机、保留策略 |
| [04 API 契约](docs/04-api-contract.md) | REST/WS/SSE、请求响应示例、错误码、限流 |
| [05 部署与运维](docs/05-deployment-ops.md) | compose 拓扑、宿主机准备、首个管理员、备份恢复、升级、排障 |
| [06 路线图与风险](docs/06-roadmap.md) | 里程碑、验收、二期 GPU 方案、风险登记、待确认项 |
| [07 前端设计](docs/07-frontend.md) | 路由与页面、布局、状态管理、关键交互、工程结构 |

## 一句话架构

```
浏览器 ──443──> caddy ──┬── console.dsh.example.com ──> console-api（SPA + /api）
                        └── {user}.dsh.example.com ──> dsh-u-{id}:3080（运行中）
                                                       └─ 休眠时 ──> console-api 的唤醒页
```

控制面（FastAPI + worker）通过 Docker API 管理每用户一个的 DSH 容器；Caddy 通过 Admin API 按用户动态挂路由；用户的登录 token 由控制面从容器日志里取出后以 302 注入，用户不需要看见 token。

## 目录结构

```
dsh-console/
├── api/                 # FastAPI 控制面
│   ├── app/             # 路由、模型、服务、安全
│   ├── alembic/         # 迁移
│   └── tests/           # SQLite + fakeredis 冒烟测试
├── web/                 # Vue3 + Element Plus SPA
├── deploy/              # Dockerfile.api / Dockerfile.web / Caddyfile
└── docs/                # 设计与运维文档
```

## 与 dsh-docker 的关系

- **镜像底座**：复用 `dsh-docker/Dockerfile` 构建的 `dsh:<version>`，一个版本一个镜像 tag；控制台只管理「已导入且启用」的版本。
- **不修改 dsh 本体**：所有多租户能力都在外层（容器编排、反代、计量），升级 dsh 版本不需要改控制台代码。
- **实例差异**：每个实例有独立的 `DSH_HOME`、workspace、子域名、`DSH_TRUSTED_HOSTS`；`login-gate` 插件在控制台模式下**关闭**（认证由控制台 + DSH token 承担，见 02 文档）。
