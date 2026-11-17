# 07 前端设计

> Vue 3 + TypeScript + Vite + Pinia + Vue Router + Element Plus + ECharts

## 1. 路由与页面

### 用户端（`role=user` 或 `admin`）

| 路径 | 页面 | 说明 |
|---|---|---|
| `/login` | 登录 | 用户名/密码；`?next=` 回跳 |
| `/register` | 邀请注册 | `?code=` 预填；实时校验用户名可用性 |
| `/` | 工作台 | 实例卡（状态/规格/剩余时长）+ 快捷「打开」+ 公告 |
| `/instance` | 实例详情 | 规格、版本、事件时间线、日志（折叠） |
| `/usage` | 用量 | 汇总卡 + 趋势图（24h/7d/30d）+ 配额进度 |
| `/snapshots` | 快照 | 列表、创建、恢复、删除 |
| `/images` | 我的镜像 | commit、重建、删除 |
| `/files` | 文件 | 面包屑、列表、上传（拖拽/分片）、下载、重命名 |
| `/settings` | 设置 | 资料、改密、模型 Key、休眠阈值 |
| `/tickets` | 工单 | 列表 + 对话式详情 |
| `/admin/*` | 管理端 | 仅 `admin`，见下 |

### 管理端

| 路径 | 页面 |
|---|---|
| `/admin` | 总览（在线数、运行实例、今日用量、告警） |
| `/admin/users` | 用户列表/详情/配额/强制操作 |
| `/admin/invites` | 邀请码 |
| `/admin/plans` | 套餐 |
| `/admin/images` | DSH 版本（导入/启用/禁用/默认/校验） |
| `/admin/instances` | 全站实例 + 强制操作 + 终端入口 |
| `/admin/usage` | 用量大屏（Top N、趋势、下钻） |
| `/admin/node` | 物理机监控 |
| `/admin/audit` | 审计查询 |
| `/admin/announcements` | 公告 |
| `/admin/tickets` | 工单队列 |
| `/admin/system` | 设置、健康、对账、GC |

## 2. 布局

- **用户端**：顶栏（Logo、公告铃、用户菜单）+ 左侧可折叠导航 + 内容区；移动端导航变底部 Tab（工作台/用量/文件/我的）。
- **管理端**：深色侧边栏 + 浅色内容区；表格页统一「筛选栏 + 表格 + 分页」三段式。
- **大屏**：`/admin/usage`、`/admin/node` 支持全屏模式（隐藏导航），ECharts 深色主题，5s 轮询或 SSE 增量。

## 3. 状态与数据

```
stores/
├── auth.ts        # user, csrf, login/logout, 权限判断
├── instance.ts    # 当前实例、状态机、操作 loading、SSE 合并
├── quota.ts       # 配额、剩余时长（SSE quota.updated 更新）
├── notifications.ts
└── admin.ts       # 管理端筛选条件缓存
```

- **SSE**：`useEventSource()` 组合式函数，全局单例；断线指数退避重连；`instance.status` 直接 patch store，页面不轮询。
- **请求**：`api/client.ts` 统一 `fetch` 封装：带 cookie、`X-CSRF-Token`、`request_id` 透传、401 跳登录、错误 toast 统一文案。
- **轮询兜底**：SSE 不可用时，实例状态 5s、用量 30s、监控 15s。
- **乐观更新**：仅用于「休眠阈值」「备注」这类低风险字段；启停一律等 SSE。

## 4. 关键交互

### 4.1 「打开」按钮状态机

```
idle ──click──> launching（转圈）
  ├─ 200 running → location.href = url
  ├─ 202 waking  → 显示「正在唤醒…」+ 进度，等 SSE running 后重新 launch
  └─ error       → 错误提示 + 「重启实例」
```

休眠实例：按钮文案「唤醒并打开」；预计 10–30s。

### 4.2 创建向导

三步：选套餐（卡片，显示规格/时长）→ 选版本（仅启用项，显示 changelog）→ 确认（子域名预览 `https://alice.dsh.example.com`）。创建后进入进度页，展示 `job.progress` 阶段。

### 4.3 文件管理

- 列表：名称/大小/修改时间/操作；目录双击进入；面包屑可点。
- 上传：拖拽区 + 进度条；>100 MB 自动切分片，支持暂停/续传（按 `upload_id`）。
- 下载：直接 `window.location` 到 download URL（带 cookie）。
- 删除：目录需二次确认；批量选择（二期）。

### 4.4 管理端终端

- `@xterm/xterm` + `FitAddon`（`components/TerminalPanel.vue`）；cookie 鉴权直接连 WS，不需要 ticket。
- 按键经 `onData` 编码为二进制帧发送；服务端 PTY 输出为二进制帧，直接 `term.write`，ANSI 颜色/光标/清屏由 xterm 处理。
- 容器尺寸变化由 `ResizeObserver` + `fit()` 驱动，`onResize` 发送 `{"type":"resize"}`，服务端转发 SIGWINCH。
- 顶部显示连接状态与快捷键提示；暗色主题与「墨」色板一致；断开显示原因。
- 不上报命令历史，也不在浏览器持久化任何输入。

### 4.5 镜像导入

- 两种 Tab：Registry / 上传 tar。
- 进度：拉取层（若后端能拿到）→ 校验 → 入库；失败展示 `details.reason` 与重试。
- 导入成功后可「设为默认」；禁用中的版本显示灰色标签。

## 5. 视觉与规范

- Element Plus 默认主题 + CSS 变量覆盖：主色 `#4d6bfe`（DeepSeek 蓝），成功/警告/危险沿用 EP。
- 状态色：`running` 绿、`sleeping` 蓝灰、`starting/waking` 蓝（脉冲动画）、`error` 红、`stopped` 灰。
- 数字统一 `Intl.NumberFormat('zh-CN')`；字节用 `B/KB/MB/GB/TB`；时长 `xh ym`。
- 时间：相对时间（“3 分钟前”）+ tooltip 绝对时间。
- 空状态：插画 + 主行动按钮（如「创建我的云电脑」）。
- 加载：骨架屏（表格/卡片），避免整页 spinner。
- 可访问性：焦点可见、表单 label、对比度 ≥ 4.5:1；暗色大屏单独校验。

## 6. 工程结构

```
web/
├── index.html
├── vite.config.ts          # /api、/ws 代理到 localhost:8000（开发）
├── src/
│   ├── main.ts
│   ├── router/             # 路由 + 守卫（登录/角色）
│   ├── layouts/            # UserLayout / AdminLayout / BlankLayout
│   ├── views/              # 按路由分目录
│   ├── components/         # StatusTag、QuotaBar、InstanceCard、FileTable...
│   ├── stores/
│   ├── api/                # 按模块拆：auth.ts instance.ts admin.ts ...
│   ├── composables/        # useEventSource、usePolling、useUpload
│   ├── utils/              # format、validate、download
│   └── styles/
└── tests/                  # Vitest 单测（stores/format/validate）+ Playwright 冒烟
```

- 构建产物 `dist/` 进 Caddy 镜像（`/srv/console-web`）。
- 环境变量：`VITE_API_BASE=/api/v1`（同源，无需 CORS）。
- 代码规范：ESLint + Prettier；提交前 `typecheck`。
- 测试：登录跳转、实例状态机、文件路径校验、配额显示为必测。

## 7. 性能

- 路由懒加载；Element Plus 按需引入（`unplugin-vue-components`）。
- ECharts 按需注册（line/bar/pie + canvas renderer）。
- 首屏目标 < 1.5s（内网）；表格 > 200 行用虚拟滚动。
- 大屏 5s 刷新只更新数据，不重建图表实例。
