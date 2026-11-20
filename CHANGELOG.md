# Changelog

本项目的所有重要变更都记录在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.7] - 2026-11-20

一组体验与正确性修复：首启慢不再被判「失败」、慢接口不再让页面看起来卡死、
快照恢复不再被悬空链接打断、保存镜像改为异步、**改套餐会真正改变容器规格**、
时间按本地时区显示。

### 修复

- **首启超时被误判为失败**：`wait_healthy` 原本固定等 90 秒，而 dsh 首次启动要组合
  profile、生成 bind overlay，慢机器上超过 90 秒很常见——容器其实稍后就正常了，
  控制台却已经标成「异常」。现在：
  - 等待时长改为 `INSTANCE_HEALTH_TIMEOUT`（默认 **300 秒**）；
  - 更关键的是 `error` **不再是终态**：reconcile 每 60 秒复检，容器已 running 且
    healthy 就自动恢复成「运行中」，并记一条 `recovered` 事件，不需要人工重启。
- **改套餐只改了配额，没改容器**：`PATCH /admin/users` 改 `plan_id` 时只更新了
  `users.plan_id` 和 `user_quotas.plan_id`，实例里的 `cpu_cores/memory_mb/disk_gb`
  仍是创建时的快照，容器也从未重建——于是「配额页显示 4C/8G，实际跑的仍是 1C/2G」。
  现在会同步实例规格并**重建容器**（Docker 不支持在线改 `--cpus/--memory`），
  重启在后台执行，实例事件里留下 `plan_applied`。
- **快照恢复被悬空符号链接打断**：`_fix_owner` 用 `os.chown` 改属主，会跟随符号链接；
  换镜像后残留的 `profiles/node_modules/<旧插件>` 是悬空链接，抛 `ENOENT`，而代码只
  捕获了 `PermissionError`，整次恢复失败。改用 `os.lchown`（改链接本身）并逐项容忍
  `OSError`；`_safe_extract` 对链接的越界校验也从「解析目标」改成「校验链接本身」，
  既不再误伤，也没有放松安全边界（绝对链接与 `../` 逃逸仍然拒绝）。
- **保存镜像傻等**：`docker commit` 在请求里同步执行，几 GB 的容器要几分钟。
  现在接口立即返回 `202`，行以 `committing` 落库、后台提交，前端每 4s 轮询显示
  「保存中 → 可用/失败」，失败原因悬浮可见，刷新页面不丢进度。
- **按钮傻等**：实例启停/重启/删除、管理端强制启停、打开云舱等操作的按钮现在各自
  显示 loading，其它按钮同时禁用；另外新增全局顶部进度条（`TopProgress`），
  任何接口请求超过 180ms 就出现，1 秒内不闪。
- **「设为默认」点了没反应**：`setDefault` / `toggle` / `remove` 在成功后会
  `await loadCatalog()`，而它会走网络拉 GHCR 的仓库清单，缓存过期时能卡十几秒，
  期间按钮毫无反馈。现在三个按钮各自 loading、同行其它按钮临时禁用，成功后只等
  本地列表刷新，仓库清单改为后台异步刷新，不再阻塞交互；`remove` 的「取消」也不再
  被当成错误。
- **时间显示是 UTC，不是本地时间**：后端一直返回带 `Z` 的 UTC，前端却用
  `replace('T',' ').slice(0,16)` 直接截字符串，于是 11:42 显示成 03:42。新增
  `web/src/utils/datetime.ts`，用浏览器本地时区渲染，替换了 13 个页面共 19 处；
  顺带补上 `files.py` 里唯一漏掉 `Z` 的 `modified_at`（否则会按本地时间解析，反方向差 8 小时）。
- 顺带修掉 `EXCLUDES` 里的死代码：`node_modules/.cache` 之前按目录名比较，永远匹配
  不上，快照里一直带着这份缓存。

### 变更

- 「待重启」徽标文案从「插件变更」改为「配置变更」，规格调整也适用。

### 新增

- `INSTANCE_HEALTH_TIMEOUT` 配置项。
- reconcile 返回值新增 `recovered` 计数。
- 前端 `formatDateTime` / `formatShortDateTime` / `formatBucket` 三个时间格式化工具。
- 回归用例 12 条：超时后自愈、真故障不乱改、超时可配、快照悬空链接/越界/缓存排除、
  镜像异步提交与失败落库、套餐变更改规格。

## [0.1.6] - 2026-11-19

让实例启动失败时**直接看得见原因**，而不是只有一句 `container did not become healthy`。

### 修复

- 实例起不来时，错误信息附带容器日志里最后一条 error 行。例如宿主机目录属主不对时，
  页面不再只显示状态，而是直接给出
  `$DSH_HOME (/data/dsh) is not writable by uid 1000:1000; it is owned by 0:0`
  ——照着提示 `chown` 或修正 `HOST_DATA_ROOT` 即可。
- 排障表补充 `HOST_DATA_ROOT` 与项目路径不一致这一条（这是该报错最常见的原因）。
- 新增 5 条用例覆盖取错误行、回退末行、无日志、驱动异常、长度上限。

## [0.1.5] - 2026-11-19

修复「导入 GHCR 版本」点了之后一直转圈、刷新后又能点还转圈的问题。

### 修复

- **导入请求被同步拉镜像阻塞**：`/admin/images/remote/import` 在请求里直接
  `docker pull` 一个 ~1.3 GB 的镜像，前端 `await` 于是长时间无反馈；刷新页面后
  再点会**再拉一次**（上一次的行还没落库，`IMAGE_DUPLICATE` 拦不住）。
  现在接口立即返回 `202`，先以 `importing` 状态落库再后台拉取：
  - 立刻出现在列表里，重复点击返回 `409`；
  - 拉取失败写入 `status=failed` + `error`，控制台显示「失败」并在悬浮里给出原因；
  - 页面刷新后继续轮询，不会丢进度；
  - `JOBS_INLINE=1`（测试/开发）仍然同步完成，行为不变。
- 前端改为每 4s 轮询「DSH 版本」列表，导出可见的「导入中 → 可用/失败」状态流转。

## [0.1.4] - 2026-11-19

修复 `CONSOLE_BASE_DOMAIN` **从未被读取**的问题（0.1.3 只修了默认值来源，没修到这一层）。

### 修复

- **环境变量名与字段名不匹配**：字段叫 `base_domain`，pydantic 只查 `BASE_DOMAIN`；
  而 `.env.example` 和 `compose.yaml` 传的都是 `CONSOLE_BASE_DOMAIN`。
  结果是无论怎么配，`settings.base_domain` 永远是内置默认值 `dsh.example.com`，
  Caddy 也只给 `console.dsh.example.com` 签证书——真实域名在 TLS 握手阶段直接失败
  （`TLSV1_ALERT_INTERNAL_ERROR`）。
  现在两个名字都接受（`AliasChoices`），`CONSOLE_NAME` 这类本就匹配的变量不受影响。
- 新增 4 条回归用例覆盖两种变量名、默认值与字段名直传。

> 这个 bug 从 0.1.0 就存在。之前一直没暴露，是因为测试机用的正是默认域名
> `dsh.example.com`，配不配都一样。

## [0.1.3] - 2026-11-19

修复 `CONSOLE_BASE_DOMAIN` **完全不生效**的问题——部署到自己的域名后，控制台仍然只
为 `dsh.example.com` 签证书，浏览器访问真实域名在 TLS 握手阶段就被拒
（`TLSV1_ALERT_INTERNAL_ERROR`）。

### 修复

- **默认值把环境变量写死了**：`seed_settings` 把硬编码的
  `base_domain = "dsh.example.com"` 写进数据库，而 Caddy、实例地址都读数据库优先，
  于是 `CONSOLE_BASE_DOMAIN` 从第一次启动起就形同虚设。现在默认值改为从环境读取
  （`base_domain` / `console_name` / `dsh_image_repository` / `default_idle_minutes` /
  `upload_max_mb`）。
- **存量库自动纠正**：新增 `adopt_env_defaults()`，启动时若库里的值仍等于旧硬编码
  默认值、而环境要求另一个值，则采用环境值并打一条 warning。管理员**主动改过**的
  域名不会被覆盖。
- 新增 3 条回归用例（环境生效、旧值纠正、自定义值保留）。

> 立即绕过（无需升级）：把数据库里的 `base_domain` 改成真实域名，下一次 reconcile
> （≤60s）会重新下发 Caddy 配置。

## [0.1.2] - 2026-11-19

修复首次部署时 Caddy 配置发布失败、控制台停在 bootstrap 页的问题。

### 修复

- **访问日志 sink 挡住了路由下发**：Caddy 在 **加载配置时**就会去连接
  `console-worker:9001`，而 `docker compose up` 冷启动时 API 先起来、worker 还没创建，
  于是整份 `/load` 被拒（`dial tcp: lookup console-worker: no such host` → `400 Bad Request`），
  连所有路由一起失效。现在 `build_config` 会先探测 sink 是否可解析：
  解析不到就**只下发路由、省略日志 writer**（Caddy 改写到自己的 stdout），
  等 worker 起来后由下一次 reconcile 自动补回。
- 启动失败时的日志从完整 traceback 改成一行警告，避免每次冷启动都刷一段栈。

> 0.1.1 及更早版本不影响可用性：worker 启动后第一次 reconcile（约 60s 内）会补上配置，
> 只是日志里会有一段吓人的 traceback。

## [0.1.1] - 2026-11-19

修复 0.1.0 在新机器上**起不来**的问题。

### 修复

- **空的 `PUBLIC_PORT` 会让 API 启动即崩**：`compose.yaml` 用
  `PUBLIC_PORT: ${PUBLIC_PORT:-}` 传值，变量未设置时得到的是空字符串，
  pydantic 解析 `int` 失败，容器在打印任何有用日志之前就退出
  （`Input should be a valid integer, unable to parse string as an integer`）。
  现在空值（含纯空白）一律视为「未设置」，`caddy_listen_port` 同样处理。
- 新增 8 条回归用例覆盖「空串 = 未配置」这条路径。

> 受影响的是 0.1.0 的默认部署方式（不填 `PUBLIC_PORT`）。如果你的 `PUBLIC_PORT`
> 已经显式配置，0.1.0 不受影响。

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

[0.1.7]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.7
[0.1.6]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.6
[0.1.5]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.5
[0.1.4]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.4
[0.1.3]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.3
[0.1.2]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.2
[0.1.1]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.1
[0.1.0]: https://github.com/zjwan461/dsh-console/releases/tag/v0.1.0