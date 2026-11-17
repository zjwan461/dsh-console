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

### M2 实例生命周期（约 10 人日）

- DockerDriver（create/start/stop/remove/inspect/stats/logs）
- 容量探测与准入（`docker info` + `statfs` + 每 30s 刷新，见 02 §10）
- 实例创建（目录、权限、容器参数、标签）、启停/重启/删除
- Caddy 全量配置生成与热加载、启动对账
- token 解析（日志正则 + Redis 缓存）、`/launch` + `/go/{ticket}` 302
- SSE 状态推送、实例事件时间线
- 前端：实例卡片、创建向导、进度、打开按钮

**验收**：新用户 60 秒内创建实例并从控制台无感进入 GUI；`docker inspect` 确认资源限制；停/启/删状态正确。

### M3 计量、配额与休眠（约 8 人日）

- Caddy 访问日志 TCP 采集器（含断线回退文件）
- Docker stats 30s 采样、Redis 小时桶、每分钟落库
- 配额扣减（活跃秒）、80%/100% 阈值、超额停机
- 休眠判定 + 唤醒页 + 路由切换
- 用户用量页（24h/7d/30d）、配额卡

**验收**：连续使用 1 小时，`usage_bucket` 与预估误差 ≤ 5%；30 分钟无活动自动休眠；访问子域名能唤醒。

### M4 快照、镜像与版本（约 8 人日）

- 快照创建（pause + tar.zst + manifest）、恢复（含回滚）、删除、限额
- `docker commit` 用户镜像（清空敏感 ENV）、从镜像重建
- 管理端版本导入（pull/上传）、启用/禁用/默认、校验、删除
- 用户版本切换（自动快照 + 失败回滚）

**验收**：快照恢复后 `settings.yaml` 与文件一致；commit 镜像 `docker inspect` 中 `DEEPSEEK_API_KEY` 为空；禁用版本后不能新建。

### M5 管理与体验（约 10 人日）

- 管理端：用户详情、全站实例、强制操作、版本管理 UI
- 监控：node-exporter 采集、宿主机看板、用量大屏、Top N
- 文件管理（列表/上传/下载/删除/重命名/分片）
- 管理员 Web 终端（WS + xterm.js + 审计）
- 公告、工单、审计查询页、系统设置、健康页、GC/对账按钮

**验收**：管理员能在 3 次点击内定位一个异常实例并看到日志；大屏 5 秒刷新；文件上传 1 GB 成功。

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

## 5. 下一步

M1 已交付，下一步 M2：

1. DockerDriver + 容量探测；
2. 实例创建/启停/删除 + Caddy 全量配置；
3. token 注入与 `/go/{ticket}`；
4. 前端实例卡片与创建向导。

远程 Docker 环境就绪后，用 `DOCKER_HOST` 指向它即可联调；本地仍用 SQLite + fakeredis 跑控制面。
