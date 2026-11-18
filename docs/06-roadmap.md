# 06 路线图、风险与决策记录

> 本文是执行的唯一进度基线：里程碑、验收、二期清单、风险登记。变更请改这里，不要口头约定。

## 1. 里程碑

估算按「1 名熟悉 FastAPI/Vue 的全职开发」计，单位人日；不含需求反复与等硬件。

### M0 设计确认（已完成，2026-11-17）

- [x] 需求澄清（两轮，16 + 5 项决策）
- [x] 架构 / 数据模型 / API 契约 / 部署运维 / 前端设计
- [x] 第二轮确认：弹性容量、占位域名、镜像只导入、注册后走创建向导
- 验收：文档评审通过，无未决的架构级分歧。

### M1 地基（已完成，2026-11-17）

- [x] 仓库骨架：`api/`（FastAPI + SQLAlchemy + Alembic）、`web/`（Vue3 + TS + Element Plus）、`compose.yaml`
- [x] MySQL/Redis 接线、Alembic 初始迁移、种子数据、健康检查
- [x] 认证：登录/登出/改密/邀请注册、服务端会话、CSRF、限流、角色
- [x] 用户/邀请/套餐/配额 CRUD + 审计
- [x] 前端：登录/注册、布局壳、路由守卫、用户/套餐/邀请/审计/系统设置页
- [x] 本地开发模式：SQLite + fakeredis（`ENV=dev`、`REDIS_FAKE=1`）
- [x] 冒烟测试：`pytest` 4 条，覆盖登录、邀请注册、越权、配额、审计

**验收**：管理员建邀请码 → 新用户注册 → 登录；错误密码触发 429；审计有记录。

### M2 实例生命周期（已完成，2026-11-17）

- [x] DockerDriver（create/start/stop/remove/inspect/stats/logs）+ FakeDriver
- [x] 容量探测与准入（`docker info` + `statfs`，每次创建时计算）
- [x] 实例创建（目录、权限、容器参数、标签）、启停/重启/删除
- [x] Caddy 全量配置生成与热加载、启动重放
- [x] token 解析（日志正则 + Redis 缓存）、`/launch` + `/go/{ticket}` 302、唤醒页
- [x] SSE 状态推送、实例事件时间线
- [x] 前端：实例卡、创建向导、状态轮询、打开/停止/重启/删除
- [x] 管理员：镜像登记/启停/默认、实例列表/强制操作/日志

**验收**：FakeDriver 下创建 → 运行 → 打开 302 → 停止 → 启动 → 删除全链路测试通过；`docker inspect` 资源限制在真实 Docker 环境联调。

> 真实 Docker 联调待远程环境；`DOCKER_HOST=tcp://...` 即可切换，无需改代码。

### M3 计量、配额与休眠（已完成，2026-11-17）

- [x] Caddy 访问日志采集（worker 内 TCP collector，更新 last_active）
- [x] Docker stats 30s 采样、Redis 小时桶、每分钟落库
- [x] 配额扣减（活跃秒）、80%/100% 告警、超额停机
- [x] 休眠判定 + 唤醒页 + 路由切换
- [x] 用户用量页（24h/7d/30d）+ ECharts

**验收**：单元测试覆盖 record → flush → `seconds_used`；idle 扫描把实例置为 `sleeping` 并写 `status_reason=idle_timeout`。

### M4 快照、镜像与版本（已完成，2026-11-17）

- [x] 快照创建（stop + tar.zst + manifest）、恢复（含回滚）、删除、限额
- [x] `docker commit` 用户镜像（清空敏感 ENV）、从镜像重建
- [x] 管理端镜像登记与 `docker pull`、启用/禁用/默认、删除
- [x] 用户版本切换（重建容器、保留数据）

**验收**：快照恢复后 workspace 内容回到快照时点；commit 镜像 `docker inspect` 中 `DEEPSEEK_API_KEY` 为空（真实 Docker 回归时核对）。

### M5 管理与体验（已完成，2026-11-17）

- [x] 管理端：全站实例、强制操作、日志、WebSocket 终端
- [x] 监控：宿主指标采样、CPU/内存曲线、用量 Top、容量
- [x] 文件管理（列表/上传/下载/重命名/删除/新建目录，路径越界防护）
- [x] 公告（发布/置顶/过期）、工单（用户提交、管理员回复/状态）
- [x] 审计查询、系统健康、对账入口

**验收**：文件路径穿越返回 400；工单双向往返；管理员终端 Fake 驱动可回显。

### M6 加固与验收（约 5 人日）

- 安全清单逐项（05 §10）、依赖漏洞扫描
- 故障演练：杀 Caddy / 杀 worker / 停 MySQL / 磁盘写满 / 单实例 OOM
- 2 小时 30 并发实例压测；inotify 上限验证
- 运维手册、用户手册、演示数据

**验收**：故障演练全部按预期降级与自愈；压测无控制面 5xx。

**合计约 47 人日**（不含二期）。可并行：前端在 M2 起可独立推进。

## 2. 二期清单（已登记，禁止遗忘）

### 2.1 GPU 支持（二期第一优先）

- 宿主：NVIDIA 驱动 ≥ 550、`nvidia-container-toolkit`、Docker 配置 `nvidia` runtime；
- 实例：`device_requests=[DeviceRequest(count=1, capabilities=[["gpu"]])]` 或 `--gpus device=N`；
- 镜像：基于 `dsh-docker` 的 `INSTALL_CUDA=1` 变体，另加 CUDA runtime；一个 GPU 版本对应一个 `dsh_images` 记录，增加 `variant='cuda'` 字段；
- 监控：`dcgm-exporter` → `node_metric.gpu`（利用率、显存、温度、功耗）；
- 调度：默认整卡独占（`plan.gpu_count=1`，实例绑定 `gpu_ids`）；MIG 分区作为增强；不做时间片共享；
- 计费：`usage_bucket` 增加 `gpu_seconds`，按卡秒扣减套餐；
- 数据模型已预留：`plans.gpu_count`、`instance.gpu_ids`（新增列）、`node_metric.gpu`；
- 验收：两个用户各分到不同卡，`nvidia-smi` 只看到自己那张；显存打满不影响他人；GPU 看板有曲线。

### 2.2 Key 隔离网关（1.5 期第一项，先于 GPU）

- 每个实例出口走 `llm-gateway`（LiteLLM 或自研 FastAPI 代理）：
  - 实例内 `settings.yaml` 的 `llm-deepseek.baseURL` 指向网关（如 `http://llm-gateway:4000/v1`），`apiKeyEnv` 仍是 `DEEPSEEK_API_KEY`，但值是**每实例随机虚拟 key**；
  - 网关按虚拟 key 找到用户，换成真实平台 Key / 用户自带 Key，转发到 `api.deepseek.com`；
  - 记录 `prompt_tokens/completion_tokens` 到 `usage_bucket`（一期只展示，不计费）；
- 收益：① 平台 Key 不再出现在用户容器里；② 每人每日 token/费用；③ 可做限流与预算上限。
- 代价：多一个故障点；控制台需按实例生成 `settings.yaml`（`baseURL` 一行）。
- **待验证**：`DEEPSEEK_API_KEY` 同时被 `web_search` 使用（见 dsh-docker 的 settings.yaml 注释）。上网关前必须确认 `web_search` 是否也走 `baseURL`；若它直连 `api.deepseek.com`，虚拟 key 会让搜索失效，需要单独的搜索代理或对该能力降级。
- 验收：容器内 `env` 看不到真实 Key；网关断开时模型调用返回明确 502 而非挂起；token 用量与 DeepSeek 账单误差 < 3%。

### 2.3 多节点 / K8s

- 不自己造调度器：多机直接上 K8s，`NodeDriver` 增加 `K8sDriver`；
- 需要：PVC（每用户）、Ingress 或 Gateway API、Pod 安全上下文、镜像预热；
- 单机 DockerDriver 保留作为开发/小规模形态。

### 2.4 其他

- MFA（TOTP）、管理员 IP 白名单、登录告警；
- 邮件/Webhook 通知（配额、工单、故障）；
- 快照存 S3/MinIO、跨机恢复；
- 共享镜像模板（管理员把优秀用户镜像提升为公共模板）；
- 用户自定义域名（CNAME + 按需 TLS）；
- 高可用（MySQL 主从、Redis Sentinel、多 API 副本）；
- 用户协议/合规（多租户共享内核的告知）。

## 3. 风险登记

| # | 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|---|
| R-01 | **平台 Key 注入容器环境，用户可 `env` 看到** | 高：一人泄露全平台 | 高 | 一期按设计接受：平台 Key 只对可信用户开 + 设消费上限 + 审计；`llm-gateway`（虚拟 key）列为 1.5 期第一项。见 §2.2 |
| R-02 | `docker.sock` 等于宿主 root | 高 | 中 | API 不公网、参数白名单、最小镜像、管理员 MFA（二期）；把 API 视为 root 级资产 |
| R-03 | **禁用用户后，其 DSH cookie 仍有效 30 天** | 高：禁用形同虚设 | 高 | 禁用/删除时：停容器 + 删 Caddy 路由 + 轮换 `$DSH_HOME/.credentials.yaml`（使全部 cookie 失效）+ 清 Redis 会话 |
| R-04 | token 出现在浏览器历史 | 中 | 中 | `no-store`/`no-referrer`；token 随重启失效；文档提示；可选「每次进入前重启」严格模式（代价大） |
| R-05 | Caddy admin API 无鉴权 | 中 | 低 | 只绑内部网络、不发布；网络策略限制只有 api 可访问 2019 |
| R-06 | inotify 耗尽导致 dsh 热重载失效 | 中 | 高 | 宿主调大 `max_user_watches`；M6 压测验证；监控 `fs.inotify` |
| R-07 | 磁盘限额是软限制（无 XFS prjquota） | 中 | 中 | 优先 XFS + prjquota；否则 du 每小时检查 + 超限告警/停机；上传前预检 |
| R-08 | 单机 MySQL/Redis 单点 | 中 | 中 | 备份 + 快速恢复；控制面故障不影响运行实例；二期 HA |
| R-09 | 容器共享内核的逃逸风险 | 高 | 低 | 无特权/cap_drop/no-new-privileges/pids 限制；用户协议；二期评估 gVisor/Kata |
| R-10 | 镜像体积大（Office/Chrome/CUDA），拉取慢、占盘 | 中 | 高 | 预拉取到本地；导入时记录大小；GC 未使用镜像；分层 tag |
| R-11 | 用户误删数据 | 中 | 中 | 删除二次确认 + 7 天保留；快照；恢复演练 |
| R-12 | 版本跨代不兼容导致数据损坏 | 高 | 中 | 切换前强制快照；失败自动回滚；管理员标注「兼容基线」 |
| R-13 | Caddy 重启后路由丢失窗口期 | 低 | 高 | 启动对账 60s 内恢复；监控 `config_loaded` |
| R-14 | 配额计量误差导致纠纷 | 中 | 中 | 30s 采样、双记录（活跃/运行）、账单明细可导出；误差 ≤5% 写入 SLA |

## 4. 决策记录（2026-11-17）

### 第一轮

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| 1 | 后端栈 | Python + FastAPI | 与 dsh-docker 同栈；docker SDK / 资源采集最顺 |
| 2 | 部署形态 | 先单机，预留多节点 | 多节点后期直接上 K8s，不自己造调度器 |
| 3 | 访问方式 | 每用户子域名 | DSH cookie 绑 authority，子域名天然隔离 |
| 4 | 进入方式 | 控制台账号 + 自动注入 token | 用户无感；后端有读容器日志权限 |
| 5 | 时长口径 | 双记录，按活跃会话扣 | 防忘关容器耗光时长 |
| 6 | 快照/镜像 | 两者都做 | 数据归档 + 系统层 commit，语义不同 |
| 7 | 配额模型 | 套餐 + 单用户覆盖 | 批量管理、个体可调 |
| 8 | 闲置策略 | 自动休眠 + 点击唤醒 | 省内存、少扣时长 |
| 9 | 前端 | Element Plus | 后台组件全 |
| 10 | 一期附加 | 文件管理、公告工单、自选版本、管理员终端 | 运维与体验必需 |
| 11 | 域名布局 | `console.` + `{user}.` | 同一泛域名，隔离清晰 |
| 12 | 实例数量 | 每人 1 个 | 配额与内存核算最简单 |
| 13 | 模型 Key | 平台默认 + 用户可覆盖 | 开箱即用且能分清成本 |
| 14 | Token 计量 | 一期不做 | 需出口网关，二期再说 |
| 15 | GPU | 二期，一期只预留字段与接口 | 硬件与调度复杂度高 |
| 16 | TLS | 泛域名 + Caddy internal CA | 内网无需公网 DNS |

### 第二轮

| # | 问题 | 结论 | 落点 |
|---|---|---|---|
| 17 | 物理机规格 | **弹性**：运行时探测 + 动态准入，不做机型适配 | 02 §10、05 §3.1；M2 实现 |
| 18 | 基础域名 | 保持占位；全部走配置；TLS 支持 internal_ca / acme | 02 §3、05 §5 |
| 19 | 镜像来源 | **只导入**；不构建、不派生；`login-gate` 由 `dsh-docker` 侧移除 | 02 §4、05 §4 |
| 20 | 注册后行为 | 进入创建向导，不自动建实例 | 01 US-02、07 §4.2 |
| 21 | 平台 Key 风险 | 一期接受（仅可信用户 + 消费上限 + 审计）；网关列 1.5 期第一项 | 02 §9、§2.2、R-01 |

## 5. 真机回归（2026-11-17，远端主机）

M3–M5 完成后在真实 Docker（Engine 29.6.2 / API 1.55）+ MySQL 8.0.39 + Redis 7 上跑通全链路，脚本见 `scripts/e2e_remote.py`、`scripts/e2e_terminal.py`。

**结果：全部通过**（1 项因宿主镜像存储问题跳过）。

真机测试发现并修复的 **5 个 bug**（都是本地 FakeDriver 测不出来的）：

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | Caddy 拒绝整份配置（400） | 访问日志写在了 `servers.<name>.logs`；正确位置是**顶层** `logging.logs`，server 只声明 `default_logger_name` | `services/caddy.py` 按 `caddy adapt` 的实际结构生成；新增 `tests/test_caddy.py` 锁住形状 |
| 2 | 点「打开」回到登录页 | `/go/{ticket}`、`/w/{wake_key}` 注册在 `/api/v1` 前缀下，而 Caddy 与 launch URL 用根路径 → 被 SPA 兜底成 200 index.html | 公共入口路由挂到根（`app.include_router(instances.public_router)`），Caddy console 站点显式反代 `/go/*`、`/w/*` |
| 3 | 停止/启动偶发「返回 202 但什么都没发生」 | 后台任务抢不到实例锁时直接 `return`，请求已回 202 | 锁改为**请求内获取**：拿不到返回 `409 INSTANCE_BUSY`；Redis 故障时 **fail-open**（宁可并发也不要吞操作） |
| 4 | 大镜像 `docker commit` 必失败 | docker-py 默认 30s 超时，28 层约 2GB 镜像远超 | `DOCKER_TIMEOUT=300` / `DOCKER_COMMIT_TIMEOUT=1800` / `DOCKER_PULL_TIMEOUT=3600` |
| 5 | 管理员终端握手 500 | WebSocket 路由继承了 HTTP 的 `csrf_guard` 依赖（`csrf_guard` 需要 `Request`，拿到的是 `WebSocket`）；且断开后再次 `receive()` 抛 `RuntimeError` 而非 `WebSocketDisconnect` | 终端单独用 `ws_router`（cookie 鉴权）；新增 `_safe_receive()` 统一处理断开 |

**已知环境限制**：`dsh:0.2.1-rc.5` 在这台宿主上 `docker commit` 报 `content digest ... not found`（同宿主 redis / console-api 等镜像 commit 均正常），属宿主镜像存储缺 blob；修复命令 `docker save dsh:0.2.1-rc.5 | docker load`。用户镜像功能代码路径由单测（FakeDriver）与其它镜像的 commit 验证覆盖。

> **仓库卫生**：回归脚本里的主机地址、端口、数据库口令全部改为环境变量（`scripts/_remote.py` + git-ignored 的 `scripts/.remote.env`）。此前一次提交曾硬编码过一台测试机的 IP，该提交已被 amend 覆盖并 `reflog expire` + `gc --prune=now` 清除，当前对象库中不含该地址。CI 里 `scripts/check_no_secrets.py --history` 会持续守住这条线。

## 5.1 版本自动发现（2026-11-17）

需求：除了导入 tar 包，还要能发现 `dsh-docker` 在 GitHub 上发布的新包。

实现（`services/registry.py` + `services/image_catalog.py`）：

- 走 Registry HTTP API（`/v2/` 挑战 → bearer token → `tags/list` → `manifests/<tag>` → `config` blob），**只读元数据不拉镜像**。
- 只把 `x.y.z` / `x.y.z-pre` / `v…` / `…-slim` 当版本；`latest`、`sha-*` 过滤掉。
- 合并本地状态：先按完整引用匹配，再退回按 `version` 匹配，所以手工登记的 `dsh:0.2.1-rc.5` 也算「已导入」。
- 定时（默认 6h）+ 手动检查；结果缓存进 `settings.registry_state`，仓库不可达时**保留上次快照**并标注错误。
- 一键导入 = `docker pull` + 入库；tar 上传 = 写盘 + `docker load` + 入库。

真机验证（`scripts/e2e_registry.py`，对 `ghcr.io/zjwan461/dsh-docker`）：

```
21 个标签 → 1 个版本标签 0.2.1-rc.5，1.28 GB，digest sha256:e63970e2…，created 2026-11-17T05:59:28Z
docker pull 到入库 144s，页面状态变为「已导入」
```

修掉的 bug：`created` 一直为空 —— BuildKit 写 9 位纳秒，Python 3.11 的 `fromisoformat` 只接受 ≤6 位，`parse_timestamp()` 先截断再解析（有单测）。

单元测试 12 条（`tests/test_registry.py`），全部用 `httpx.MockTransport`，不碰网络。

## 6. M6 加固（进行中）

真机回归之后做的一轮生产化补齐，都是离线可验证的：

- [x] **管理员终端治理**：`admin_terminal_enabled` 真正生效（此前只是个设置项）；每次开/关都写审计（操作者、实例、IP、UA、时长、字节数、关闭原因）；`TERMINAL_IDLE_SECONDS` 空闲自动断开；前端按 `/system/info.features.terminal` 置灰入口。
- [x] **上线前自检 `app.cli doctor`**：数据库/迁移版本/Redis/Docker/实例网络/默认镜像/数据目录/密钥强度/Caddy 一次跑完，`FAIL` 退出码 1，可直接做部署门禁。
- [x] **容量探测降级**：Docker 不可用时监控页不再 500，返回 `docker_ok=false` 并拒绝新建实例（503 `DOCKER_UNAVAILABLE`）。
- [x] **启动自动迁移**：`ensure_schema()` 每次启动把库收敛到 head（dev/prod 同一套），MySQL 用 `GET_LOCK` 串行化多 worker；worker 启动也检查。新增迁移不再需要手工执行。
- [x] **模型端点可配**：Key 可绑定 Base URL 与模型名；非 DeepSeek 模型生成 `openai-completions` 提供方，重启按配置哈希重建容器。
- [ ] 备份/恢复演练（mysqldump + data + Caddy 证书）
- [ ] 故障演练：杀 Caddy / worker / MySQL、磁盘写满、单实例 OOM
- [ ] 压测与 inotify 上限
- [ ] 指标导出（Prometheus `/metrics`：实例状态、配额余量、worker 落库延迟）

## 7. 下一步

M1–M5 已交付并完成真机回归。剩余工作：

1. 生产化收尾：HTTPS + 域名（`internal_ca`）、`COOKIE_SECURE=true`、备份演练；
2. 故障演练（杀 Caddy / worker / MySQL、磁盘写满、单实例 OOM）、压测、inotify 上限；
3. 二期：GPU、`llm-gateway`（Key 隔离）、多节点/K8s。
