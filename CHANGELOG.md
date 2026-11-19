# Changelog

本项目的所有重要变更都记录在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-11-19

首个可用版本。控制面、数据面、管理端与用户端功能完整，已在真实 Docker / MySQL 8 /
Redis 与 `dsh-docker` 镜像上完成端到端回归。

### 新增

- **实例生命周期**：创建 / 启动 / 停止 / 重启 / 删除；每用户独立容器、资源限制、
  健康检查、失败回滚；实例级操作锁避免并发打架（`409 INSTANCE_BUSY`）。
- **访问与登录**：每用户子域名；`/go/{ticket}` 单次 token 跳转；停止态落到唤醒页
  `/w/{key}`，不泄露 token。
- **Caddy 动态路由**：路由由控制面通过 admin API 全量下发（`logging.logs` 顶层结构），
  控制台与实例共用同一入口。
- **计量与配额**：worker 每 30s 采样 Docker stats，按小时聚合落库；仅活跃时扣时长；
  配额耗尽自动停机。
- **闲置休眠**：按活动判定空闲，超时休眠；用户点击即唤醒。
- **快照与个人镜像**：数据快照往返；容器 commit 成个人镜像；版本切换与保留策略。
- **版本治理**：直接读取 GHCR registry 元数据发现新构建（不拉镜像），一键导入并设默认；
  同时支持 `docker save` tar 离线导入。
- **模型接入**：API Key + Base URL + 模型名；非 DeepSeek 模型自动生成
  `openai-completions` 提供方并写入 `agent-default-model`。Key 以 AES-256-GCM 加密存库。
- **真实 Web 终端**：管理端对实例开 `docker exec` PTY（xterm.js + 二进制帧 + resize），
  带开关、审计、空闲超时。
- **技能仓库**：管理员导入 zip/tar/md、在线创建与编辑、版本回滚、按全员/套餐/用户开放；
  用户默认全启用、可逐个关闭；物化到 `$DSH_HOME/skills` 后由 DSH 热重载。
  镜像内置技能不接管、不可禁用。
- **插件目录**：管理员导入 DSH bundle（校验 package.json / 入口 / patch / 依赖），
  按套餐或用户分配；以只读子路径挂载追加到 `/opt/dsh-plugins`，变更标记待重启；
  用户端只读，不能自选。
- **管理端**：全站实例、用户与配额、套餐、版本、邀请码、公告、工单、审计日志、节点指标。
- **无感升级**：每次启动自动把库迁到代码期望的 `head`；多 worker 用数据库锁串行化；
  兼容历史 `create_all` 库（自动补列）。
- **自检**：`app.cli doctor` 上线前检查 MySQL / Redis / Docker / Caddy / 迁移版本 / 容量。
- **CI**：GitHub Actions 构建并推送 GHCR 镜像；安全扫描覆盖工作区与全部 git 历史。

### 变更

- 前端采用「墨 + 纸」视觉：深墨侧栏、宣纸底、青墨主色、铜色点缀。
- 用户主按钮文案统一为「创建我的 DSH 云电脑」。

### 修复

真机回归中发现并修复：

- **MySQL 外键符号性**：`users.id` 为 `BIGINT UNSIGNED`，迁移写成有符号 `BIGINT`
  触发 errno 3780，整个迁移失败。迁移改用与模型一致的 `with_variant` 无符号类型。
- **同名插件冲突**：镜像自带插件与平台插件同名时，两个目录争一个包名、胜者取决于扫描
  顺序。导入与启用现在都会读取镜像清单并拒绝（`409 PLUGIN_NAME_COLLIDES`）。
- **陈旧 patch 块**：更换实例镜像后，旧镜像的插件块残留在 `cordis.patch.yml`，
  导致 `N entries did not activate` 且浏览器永久停在 `Loading plugins`。
  对账时会清理「不在镜像清单、也不属于平台插件」的块；读不到镜像清单时不清理。
- **非标准端口**：实例 URL 与 DSH 信任围栏都不带端口，`:38080` 部署下 `/go` 跳转到
  打不开的地址、WebSocket 被拒。新增 `PUBLIC_PORT`，同时作用于实例 URL、`/go` 与
  `DSH_TRUSTED_HOSTS`。
- **插件停用**：删除 patch 块后残留空表头；运行中的容器仍挂着目录时直接删会抽走文件，
  改为延迟到容器重建后回收。
- **CI 与本地不一致**：`ruff` 只有版本区间，CI 装 0.8.x 而本地更新版本，出现
  「本地全绿、CI 红」。钉住 `ruff==0.8.6`，并把 `alembic` 声明为第三方包以修正
  import 排序。
- **成员可见的运维误报**：成员也会调用 `/system/health`，Docker 不可用时被显示成
  「运行时异常」。健康探测改为仅管理员触发。

### 安全

- 仓库不含任何主机地址、端口或口令；CI 的 `security.yml` 会扫描工作区与完整历史。
- 模型 Key 加密存储，接口只回显尾 4 位；CSRF 校验、登录限流、会话 Cookie 签名。
- 实例不发布端口，只加入内部 `dsh-edge` 网络，对外由 Caddy 代理。

### 已知限制

- 插件与 DSH 版本的**硬性**兼容校验尚未实现（当前只记录并展示 `dsh_versions`）。
- 插件导致实例启动失败时，暂不自动回滚或隔离。
- 技能的「模型侧可见」未做端到端验证（需要可用的模型 Key）。
- 生产加固未验证：HTTPS + 自有域名、备份恢复演练、故障演练（杀进程、磁盘写满、OOM）、
  压测与 inotify 上限。

[0.1.0]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.0