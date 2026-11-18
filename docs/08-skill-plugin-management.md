# 08 · Skill 与插件管理设计

> 状态：技能与插件全部实现，并在真实 Docker / MySQL / dsh-docker 镜像上通过回归（`scripts/e2e_skills_plugins.py`，30 项全绿）
> 前置决策（2026-11-18）：
> 1. 用户对「管理员开放的 skill」**默认全启用**；
> 2. 用户在 workspace 里自己放 `.dsh/skills` 属个人行为，**不做监管**，控制台只提供平台技能仓库；
> 3. **不接管** `dsh-docker` 镜像内置 skill，内置不可禁用——那是 `dsh-docker` 仓库的职责；
> 4. 插件**不给用户开关**，只做管理员 / 套餐分配。

## 1. 范围与边界

### 1.1 控制台负责什么

| 对象 | 控制台职责 | 不负责 |
|---|---|---|
| 平台 Skill | 仓库、导入、在线创建/编辑、版本、发布、开放范围、用户启用偏好、物化到实例 | 镜像内置 skill 的增删；用户 workspace 里的私有 skill |
| 平台 Plugin | 目录、导入、版本、兼容性、按套餐/用户分配、物化、patch 管理、失败回滚 | 镜像内置插件；用户自选插件 |

### 1.2 生效后的技能来源

```
用户可用 skill
  = dsh-docker 镜像内置（/opt/dsh-skills，始终生效，控制台不碰）
  + 控制台平台技能（物化到 $DSH_HOME/skills）
  + 用户自己在 workspace 放的 .dsh/skills / .agents/skills（控制台不碰）
```

三者按 DSH 原生优先级合并：项目技能 > 用户技能 > 内置。控制台只保证“平台技能”这一段可管、可审计。

### 1.3 为什么不做内置技能接管

- 内置技能随镜像版本走，与 `dsh-docker` 的发布节奏绑定；
- 如果控制台设置 `DSH_BUNDLED_SKILL_DIR` 接管，镜像升级时内置技能会“消失”，必须额外做镜像内容同步与迁移；
- 决策已明确：内置不可禁用，职责留在 `dsh-docker`。

## 2. DSH 机制摘要（实现依据）

### 2.1 Skill

- 一个 skill = 目录 + `SKILL.md`（frontmatter 至少 `name`、`description`，可选 `whenToUse`、`invocation.modelInvocable/userInvocable`），可带 `scripts/`、`assets/`；
- 六个根，优先级从高到低：`project-dsh`、`project-agents`、`custom`、`user-dsh`（`$DSH_HOME/skills`）、`user-agents`、`bundled`（`/opt/dsh-skills`）；
- `$DSH_HOME/skills` 在容器内是 `/data/dsh/skills`，对应宿主 `<instance.data_path>/skills`，**宿主可写、热重载**；
- 没有“禁用”字段：不放进任何根，就等于不存在。

### 2.2 Plugin

- 插件是 DSH bundle（`package.json` + 入口 + 可选 `dsh.bundle.patch`）；
- 容器启动时 `entrypoint.sh` 对 `DSH_PLUGIN_ROOT` 下每个目录：
  1. 软链到 `$DSH_HOME/profiles/node_modules/<name>`；
  2. 把 patch 层追加一次到 `$DSH_HOME/cordis.patch.yml`（home 级 patch）；
- patch 典型内容：

  ```yaml
  - insert:
      - id: file-download
        name: '@local/dsh-file-download'
  ```

- 插件集合变化需要**重启**；entrypoint 只加不删，禁用必须同时移除软链与 patch 块，否则启动会 `plugin tree failed to load`。

## 3. Skill 设计

### 3.1 数据模型

```text
skills
  id, name(unique), display_name, description, when_to_use,
  model_invocable, user_invocable,
  status(draft/published/disabled), source(create/upload),
  current_revision_id, created_by, created_at, updated_at

skill_revisions
  id, skill_id, rev, storage_path, checksum, size_bytes,
  manifest_json, changelog, created_by, created_at

skill_grants
  id, skill_id, scope(all/plan/user), target_id

user_skill_prefs
  id, user_id, skill_id, enabled, updated_at
  unique(user_id, skill_id)
```

- 内容存 `DATA_ROOT/skills/<skill_id>/<rev>/`，DB 存元数据与校验和；
- 一个 revision 是完整目录快照（含 `scripts/`、`assets/`、权限位）；
- `source` 只有 `create` / `upload`，**没有 image**（不接管内置）；
- 删除采用 `status=disabled` 软删，保留 revision、授权和审计。

### 3.2 可见性与默认值

- 允许集合 = `status=published` 且满足任一 grant：`all` / 用户所在套餐 / 指定用户；
- 用户偏好缺省视为 **enabled=true**；
- 用户可把自己不需要的关掉；管理员后续新增的 skill 对老用户同样默认启用；
- grant 被收回时偏好保留但不生效；重新开放后恢复原选择（若用户没改过则仍是启用）。

### 3.3 生命周期

```
草稿 draft ──发布──> published ──停用──> disabled
   ↑                    │
   └──── 回滚 revision ──┘
```

- 新建 / 导入 → `draft`；
- 编辑已发布 skill → 生成新 revision，**保持 published**，保存即热更新到实例；
- 草稿编辑仍是草稿，发布后才进入用户可见集合；
- 发布：写 DB + 触发受影响实例同步（热重载）；
- 停用：从所有实例移除，不删历史；
- 回滚：切回旧 revision 并发布。

### 3.4 导入与编辑

**导入格式**

- `.zip` / `.tar.gz`；
- 允许两种布局：单个顶层目录（含 `SKILL.md`），或根目录直接是 `SKILL.md`；
- 也支持单文件 `.md`（DSH 的单文件 skill 形式）。

**校验（导入与保存都跑）**

- 安全：拒绝 `..`、绝对路径、符号链接、设备文件、超长路径、隐藏文件外的可执行位异常；
- 大小：单 skill ≤ 20 MB，文件数 ≤ 300，`SKILL.md` ≤ 1 MB；
- frontmatter：YAML 可解析，`name` 必填且匹配 `[a-z0-9][a-z0-9-]{0,63}`，`description` 必填 ≤ 500 字；
- 与现有 skill 同名：返回冲突，管理员选择「新版本」或「改名」；
- 可选静态扫描：`curl ... | bash`、`base64 -d | sh`、写 `~/.ssh` 等，仅告警。

**在线创建 / 编辑**

- 表单字段：name、display_name、description、whenToUse、modelInvocable、userInvocable；
- 正文 Markdown 编辑器（保存生成新 revision，旧 revision 不可变）；
- 脚本/附件随导入包一起保存，详情页展示文件清单；
- 文件树在线编辑/重命名留到后续版本（当前用「导入新版本」替换附件）。

### 3.5 物化（关键）

**路径**

```
<instance.data_path>/skills/<skill-name>/
    SKILL.md
    scripts/...
    assets/...
<instance.data_path>/.console/skills-manifest.json
```

容器内即 `/data/dsh/skills/<name>`。

**规则**

- 只增删 manifest 里登记过的条目；用户自己放进来的目录一律不动；
- 写临时目录 → `rename` 替换，避免 DSH watcher 读到半个文件；
- 目录名用 frontmatter `name`（DSH 以 name 为准，目录名只影响可读性）；
- 单文件 skill 写成 `<name>.md`；
- manifest 记录 `{name: rev, checksum}`，用于检测漂移和增量同步；
- **不设置 `DSH_BUNDLED_SKILL_DIR`**，内置 skill 保持镜像默认。

**生效**

- 写入后由 DSH watcher 热重载；控制台不需要重启实例；
- 若实例处于 stopped/sleeping，等下次启动时同步即可；
- Windows/macOS 挂载偶发 watcher 不触发，回退为“下次启动生效”，不阻塞保存。

### 3.6 同步触发与漂移

| 事件 | 动作 |
|---|---|
| 管理员发布 / 停用 / 回滚 | 异步同步所有受影响实例 |
| 用户修改启用偏好 | 同步该用户实例 |
| 实例创建 / 启动 | 全量同步（幂等） |
| worker reconcile（60s） | 对比 manifest 与期望哈希，修复漂移 |
| 实例被手工改坏 | reconcile 修复，并记录事件 |

同步以 `user_id → instance` 为单位；当前一人一实例，后续多实例直接循环。

### 3.7 API

```text
GET    /admin/skills?status=&q=
POST   /admin/skills                     # 在线创建（draft）
GET    /admin/skills/{id}
PUT    /admin/skills/{id}                # 元数据 / 正文 -> 新 revision
DELETE /admin/skills/{id}                # 软删
POST   /admin/skills/import              # multipart zip/tar/md
GET    /admin/skills/{id}/revisions
POST   /admin/skills/{id}/publish
POST   /admin/skills/{id}/rollback       # {revision_id}
PUT    /admin/skills/{id}/grants         # {grants: [{scope, target_id}]}

GET    /me/skills                        # 允许集合 + 我的启用状态
PUT    /me/skills                        # {enabled_ids}
```

### 3.8 管理端 / 用户端

**管理端「技能」页**

- 列表：名称、描述、来源、状态、开放范围、revision、更新时间；
- 详情弹窗：frontmatter 表单 + Markdown 正文 + 文件清单 + 版本历史/回滚；
- 导入按钮、发布/停用、开放范围（全员 / 套餐 / 用户）；
- 操作写审计：`admin.skill_create/import/update/publish/grant/disable`。

**用户端「设置 → 技能」**

- 分组展示管理员开放的技能，带 description / whenToUse；
- 开关默认全开；「全部启用 / 全部关闭 / 恢复默认」；
- 保存后即时同步，文案提示“已应用，无需重启”；
- 不显示 skill 文件内容（避免把内部脚本暴露给终端用户）。

## 4. 插件设计

### 4.1 治理模型

| 维度 | 决定 |
|---|---|
| 谁能装 | 仅管理员 |
| 谁能用 | 按套餐 / 指定用户分配 |
| 用户能否开关 | **不能**，用户侧最多只读展示 |
| 镜像内置插件 | 不接管，始终生效 |
| 生效方式 | 物化 + **重启实例**（需要重建容器） |
| 版本兼容 | 记录 `peerDependencies` / `dsh` 范围并在界面展示；硬性拒绝放到真机验证阶段 |

原因：插件是跑在 DSH 服务进程里的 Node 代码，能读 `$DSH_HOME/.credentials.yaml`、模型 Key 环境变量和网络，不是 agent 沙箱内的内容。用户可开关插件等于让用户给自己容器装任意后端代码。

### 4.2 数据模型

```text
plugins
  id, package_name(unique), display_name, version, description,
  dsh_versions(兼容范围), manifest_json, status(enabled/disabled),
  current_revision_id, created_by, timestamps

plugin_revisions
  id, plugin_id, version, storage_path, checksum, size_bytes,
  manifest_json, patch_files_json, created_by, created_at

plugin_grants
  id, plugin_id, scope(all/plan/user), target_id

plugin_assignments（可选视图，也可实时计算）
```

### 4.3 物化：只读子路径挂载 + 受控 patch

**不能把 `DSH_PLUGIN_ROOT` 指到平台目录**，否则镜像内置插件会全部消失。实现改用**逐插件子路径挂载**：

```
宿主机 <instance.data_path>/plugins/<flat>/
    -> 容器 /opt/dsh-plugins/<flat>   (ro)
```

- `DSH_PLUGIN_ROOT` 保持默认 `/opt/dsh-plugins`，entrypoint 照常扫描，内置插件天然保留；
- 平台插件按分配结果复制/移除，每个插件一条 bind mount；
- 挂载点变化会进入 `config_hash`，实例启动/重启时自动重建容器；
- `flat` 是包名扁平化（`@scope/name` → `scope__name`），因为 entrypoint 只扫描一层目录；
- 平台插件与内置插件同名时，容器内挂载会遮蔽内置目录——真机阶段需要增加同名检测（见 4.5 待办）。

**patch 管理**

entrypoint 在容器启动时只为**新出现的插件**播种 patch，且从不删除。控制台在禁用插件时：

- 先删 patch 块并让 `_spec()` 不再带这条挂载；**目录本身推迟到容器重建后回收**（运行中的容器可能还挂着它，直接删会 EBUSY 或抽走正在使用的文件）；
- 同时从 `$DSH_HOME/cordis.patch.yml` 删除该插件的 `# bundled plugin: <name>` 块；
- 只删平台插件表里存在的包名，镜像内置插件的块不动；
- 删块后实例标记 `needs_restart`，重启即可正常启动（不会 `plugin tree failed to load`）。

块边界按 entrypoint 自己写的格式识别：从 `# bundled plugin: <name>` 到下一个
`# bundled plugin:` / `# --- bundled plugins` 或文件末尾。

**启用**则不需要控制台写 patch：新挂载让 entrypoint 认为它是“未播种”的插件，自动追加。

### 4.4 生效与失败处理

- 分配/撤销后实例标记 `needs_restart=true`，前端显示「待重启」；
- 管理员可重启（或下次用户启动时生效）；
- 重启后健康检查；失败自动回滚插件集合并隔离插件——**待在真机阶段实现并验证**；
- 记录事件：`plugins_changed`、`restart_required`、`plugin_rollback`。

### 4.5 兼容性

- 导入时解析 `package.json` 的 `peerDependencies` / `dsh` 字段，记录支持的 DSH 版本范围，界面展示；
- 分配时对比实例 `dsh_version` 并硬性拒绝——**待在真机阶段实现**；
- 每次 DSH 版本升级后，worker 扫描已分配插件并标记不兼容项——**待在真机阶段实现**；
- 同名遮蔽检测（平台插件 vs 镜像内置）——**待在真机阶段实现**。

### 4.6 API

```text
GET    /admin/plugins
POST   /admin/plugins/import          # multipart zip/tar（bundle）
GET    /admin/plugins/{id}
POST   /admin/plugins/{id}/enable
POST   /admin/plugins/{id}/disable
PUT    /admin/plugins/{id}/grants     # {grants: [{scope, target_id}]}

GET    /me/plugins                    # 只读：已分配插件 + 是否待重启
```

用户端不提供写接口。

## 5. 实例配置与状态

### 5.1 新增字段

```text
instances
  skills_revision     # 已完成同步的技能集合哈希
  plugins_revision    # 已完成物化的插件集合哈希
  needs_restart       # 插件变更但容器未重建
  config_synced_at
```

### 5.2 期望状态计算

```text
skills_desired  = hash(sorted(published & granted & user_enabled revisions))
plugins_desired = hash(sorted(granted plugin revisions))
```

- `skills_desired != skills_revision` → 热同步（不重建）；
- `plugins_desired != plugins_revision` → 物化 + `needs_restart=true`；
- 启动/重建时两者都对齐，清空 `needs_restart`。

### 5.3 与现有配置哈希的关系

当前容器 label `dsh.console.config_hash` 覆盖 env、mounts、资源、base_url/model。新增：

- 平台插件的**挂载列表**变化 → 进 config_hash（重建容器）；
- skill 集合**不进** config_hash（热重载，重建无意义）。

## 6. 迁移与兼容

1. **先上 Skill**：只做加法，物化到 `$DSH_HOME/skills`，不设 `DSH_BUNDLED_SKILL_DIR`；现有实例重启后多出平台技能，内置不变；
2. **插件后上**：没有分配就没有额外挂载，`DSH_PLUGIN_ROOT` 保持默认，现有实例零变化；
3. 首次开启插件管理时，对已有实例不自动分配任何平台插件；
4. DB 迁移新增表，`instances` 加列，全部可空/有默认；
5. 回滚：停用所有平台 skill/plugin → 删除物化目录即可恢复原状。

## 7. 边界情况

| 场景 | 处理 |
|---|---|
| skill 同名 | 导入冲突，选择新版本或改名；`name` 全局唯一 |
| 管理员停用 skill | 从所有实例移除，用户偏好保留 |
| 用户关闭后管理员更新 | 保持关闭，不强制打开 |
| 实例正在运行 | skill 热同步；plugin 标记待重启 |
| 实例停止/休眠 | 只记期望状态，启动时物化 |
| 用户自己放了同名 skill | 项目/用户根优先级更高，控制台不覆盖、不删除 |
| 插件 patch 被手改坏 | 备份 + 回滚 + quarantine |
| DSH 升级导致插件不兼容 | 标记不兼容，拒绝新分配，提示处理 |
| 多实例（未来） | 同步按 user 的所有实例循环 |

## 8. 非目标

- 不接管 / 不禁用镜像内置 skill；
- 不监管用户 workspace 里的私有 skill；
- 不提供用户级插件开关；
- 不做 skill 市场、评分、付费；
- 不在控制台执行 skill 里的脚本（只存与分发）。

## 9. 实施计划

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0-1 | 数据模型 + 迁移 + skill 导入/校验/存储 | ✅ |
| P0-2 | 管理端技能 CRUD / 编辑器 / 发布 / 授权 | ✅ |
| P0-3 | 用户端技能开关 + 物化 + 热同步 + reconcile | ✅ |
| P0-4 | 测试：frontmatter/zip 安全/默认启用/同步/漂移 | ✅ |
| P1-1 | 插件模型 + bundle 校验 + 兼容性记录 | ✅ |
| P1-2 | 子路径挂载 + patch 清理 + 待重启 | ✅（离线） |
| P1-3 | 管理端插件页 + 套餐/用户分配 | ✅ |
| P1-4 | 真机验证（插件加载、同名冲突、陈旧块清理） | ✅ 见 §12 |

## 10. 测试要点

- frontmatter 解析、非法 name、超限、路径穿越、zip 炸弹；
- 授权并集与默认启用；
- 物化只动 manifest 条目，用户目录不受影响；
- 热同步后 `skills-manifest.json` 与期望哈希一致；
- 插件 patch 块写入/删除幂等，重复同步不产生重复块；
- 禁用插件后重启能正常启动（无 `plugin tree failed to load`）；
- 插件启动失败自动回滚；
- DSH 版本不兼容时拒绝分配；
- 审计覆盖所有管理操作。

## 11. 待确认

1. 平台 skill 的正文是否对普通用户可见？当前设计：列表只显示 description，不展示脚本内容。
2. 插件 patch 的选项（如登录门禁账号）是否需要在管理端提供表单？建议 P2 再做。
3. 是否需要“按 DSH 版本锁定 skill revision”（同一 skill 对不同镜像版本用不同内容）？当前设计：不锁定，所有版本共用。

## 12. 真机验证记录（2026-11-18）

环境：真实 Docker 29.6、MySQL 8.0.39、镜像 `ghcr.io/zjwan461/dsh-docker:0.2.1-rc.5`，脚本 `scripts/e2e_skills_plugins.py`（30 项检查全绿）。

### 12.1 已验证

| 项 | 结果 |
|---|---|
| MySQL 迁移到 `c9f1a2b3d4e5`，7 张新表 | ✅ |
| 技能 CRUD / 授权 / 发布在 MySQL 上 | ✅ |
| 技能物化到容器 `/data/dsh/skills/<name>/SKILL.md` | ✅ |
| 用户关闭技能后容器内目录被移除，重新开启后恢复 | ✅ |
| 插件只读挂载到 `/opt/dsh-plugins/<flat>`，镜像内置插件仍在 | ✅ |
| entrypoint 打印 `bundled plugin <name>: enabled`，无 `plugin tree failed to load` | ✅ |
| `node -e "import('<包名>')"` 从 `/data/dsh/profiles/web` 解析成功 | ✅ |
| `dsh --profile web --dump-config` 组合结果包含该插件 | ✅ |
| 停用后 patch 块被删除，重启后容器正常启动、挂载消失 | ✅ |
| 插件变更 → `needs_restart` → 重启后清除 | ✅ |
| 镜像已有同名插件时导入被拒（409 `PLUGIN_NAME_COLLIDES`） | ✅ |

技能侧补充：`skill-filesystem` 在顶层是 `disabled: true`，但在默认的 `preset-standard` 智能体预设里是**启用**的，根目录含 `$DSH_HOME/skills`，因此物化路径正确。DSH 不提供技能列表 API/页面，脚本级发现未直接观测；如需最终确认，配好模型 Key 后让会话列出技能即可。

### 12.2 真机暴露并已修复的问题

1. **MySQL 外键符号性**：`users.id` 是 `BIGINT UNSIGNED`，迁移里写成有符号 `BIGINT` 导致 errno 3780。迁移改用与 `models.py` 一致的 `with_variant(mysql.BIGINT(unsigned=True))`。
2. **同名插件冲突**：镜像自带 `@local/dsh-file-download`、`@local/dsh-mobile-layout`；平台再导入同名包会让两个目录争一个包名、胜者取决于扫描顺序。现在导入与启用都会读镜像清单并拒绝（`assert_not_builtin`）。
3. **陈旧 patch 块**：换镜像后旧镜像的块（`dsh-minimax-h3`、`@local/dsh-login-gate`）留在 `cordis.patch.yml`，DSH 报 “2 entries did not activate”，**浏览器会永远卡在 Loading plugins**。现在 reconcile 会清理「不在镜像清单里、也不是平台插件」的块；读不到镜像清单时**不清理**（避免把可用实例改坏）。
4. **非标准端口**：实例 URL 与 DSH `--trusted-host` 都不带端口，`:38080` 部署下 `/go` 跳到打不开的地址、WebSocket 被信任围栏拒绝。新增 `PUBLIC_PORT`，同时作用于实例 URL、`/go` 跳转和 `DSH_TRUSTED_HOSTS`。
5. **删块留下空表头**：反复启停会累积 `# --- bundled plugins ---` 空行，现在删除最后一个块时一并去掉表头。
6. **运行中删插件目录**：容器可能还挂着该目录，改为停用后延迟到容器重建再回收。

### 12.3 尚未覆盖

- 插件与 DSH 版本的**硬性**兼容校验（当前只记录并在界面展示 `dsh_versions`）。
- 插件导致启动失败时的自动回滚 / quarantine。
- 技能的端到端「模型可见」确认（需模型 Key，见 12.1 末）。
- 容器重建期间的用户可见状态提示（现在只有「待重启」标签）。
