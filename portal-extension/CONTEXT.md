# Portal Extension — 项目上下文

> 整个项目的持久上下文:目标、架构、代码结构、约束、约定、已知限制、教训、运维。
> 接手前先读 `README.md` + `docs/README.md`;历史 PRD/ISSUES/NOTES/handoff 已归档到 `docs/archive/`。
> `docs/archive/HANDOFF-phase3.md` 记录 Phase 3 早期操作历史,本文 §9 已内联所有长期有效的运维命令,不再依赖 HANDOFF。

## 1. 项目概述与目标

在 RAGFlow v0.26.0 的 iframe 嵌入方案之上,增加权限控制层,解决三个企业落地阻碍:

1. **租户级 Token 泄露风险** —— RAGFlow iframe URL 直接含租户 API Token,任何拿到 URL 的用户都能访问该租户全部资源
2. **用户无法管理历史会话** —— Chat iframe 会话存 `API4Conversation` 表,与官方 Chat API 用的 `Conversation` 表不互通,用户关闭页面后会话「丢失」
3. **缺乏用户/角色/审计能力** —— RAGFlow `tenant` 是资源租户,不提供门户级用户、用户组、分享页 ACL、管理员审计

### 解决方案:同源架构(门户 + 网关 + RAGFlow 原生 iframe)

1. **权限门户(同源)**:自建账号体系,管理用户/用户组/分享页/ACL;门户只存身份、权限与会话归属映射,消息正文、引用、文档信息以 RAGFlow `API4Conversation` 为唯一事实源
2. **嵌入访问网关(同源)**:校验用户与分享页授权,签发短期可撤销资源受限令牌;通过 iframe URL `auth` 参数注入令牌(RAGFlow 前端 `getAuthorization()` 优先读 URL `?auth=`,回退读 localStorage,租户 Token 全程不离开网关);代理流式 SSE 并校验 `session_id` 归属;删除会话双删门户与 RAGFlow 两侧
3. **RAGFlow 最小扩展**:新增 3 个 `API4Conversation` 受控端点(GET/PATCH/DELETE sessions),复用现有 service,无新逻辑。不重写聊天 UI

一期仅覆盖全屏 Chat(`embed_type=fullscreen` + `ragflow_type=chat`),同源部署,`X-Frame-Options: SAMEORIGIN`。

## 2. 部署架构(172.16.10.180)

同源架构,三层容器/进程,全部经 nginx :80 对外:

```
浏览器 → nginx :80(容器 portal-nginx)
          ├─ /portal/assets/     → rewrite ^/portal/(.*)$ /$1 break → http://172.17.0.1:8000(portal uvicorn,宿主裸进程)
          ├─ /portal/            → rewrite ^/portal/(.*)$ /$1 break → http://172.17.0.1:8000
          ├─ = /portal           → 301 重定向到 /portal/
          ├─ ~ ^/api/v1/(chatbots|agentbots)/.*completions$  → http://172.17.0.1:8000(SSE,proxy_buffering off,proxy_read_timeout 3600s)
          ├─ ~ ^/api/v1/(chatbots|agentbots)/  → http://172.17.0.1:8000(非 SSE,/info、/inputs、/sessions)
          ├─ = /api/v1/thumbnails              → http://172.17.0.1:8000(引用文档范围校验)
          ├─ ^~ /api/v1/documents/images/      → http://172.17.0.1:8000(短期图片票据)
          └─ /                   → http://172.16.10.180:8080(容器 docker-ragflow-cpu-1,容器内 :80 → 主机 8080)
```

**容器与进程清单**(2026-07-08 验证):

| 组件 | 类型 | 监听 | 说明 |
|---|---|---|---|
| portal-nginx | 容器 | 0.0.0.0:80→80 | nginx 配置在 `~/portal-nginx/conf.d/default.conf` |
| portal | 宿主裸进程(uvicorn) | 0.0.0.0:8000 | `start.sh` 启动,加载 `~/portal-extension/.env` |
| docker-ragflow-cpu-1 | 容器 | 172.16.10.180:8080→80, 127.0.0.1:9380-9384, 443 | RAGFlow 官方 v0.26.0 镜像,`SVR_WEB_HTTP_PORT=172.16.10.180:8080`(腾出 :80 给 nginx) |
| docker-mysql-1 | 容器 | 127.0.0.1:3306 | RAGFlow 数据库 `rag_flow` |
| docker-es01-1 | 容器 | 127.0.0.1:1200→9200 | Elasticsearch |
| docker-minio-1 | 容器 | 127.0.0.1:9000-9001 | 对象存储 |
| docker-redis-1 | 容器 | 127.0.0.1:6379 | 缓存 |
| vllm-embed | 容器 | 0.0.0.0:8801→8000 | 嵌入模型 |

**注意**:portal upstream 用 `http://172.17.0.1:8000`(docker bridge 网关 IP,容器访问宿主进程);RAGFlow upstream 用 `http://172.16.10.180:8080`(主机 IP,RAGFlow :8080 绑定在 172.16.10.180 非 0.0.0.0,服务器本地 curl 须用 `http://172.16.10.180:8080/` 而非 `127.0.0.1:8080`)。

## 3. 代码结构

```
portal-extension/
├── CONTEXT.md                # 本文件(项目上下文)
├── README.md
├── pyproject.toml            # 后端依赖(uv 管理)
├── start.sh                  # portal 启动脚本(版本控制)
├── .env / ~/.env             # 配置(本地/服务器)
├── portal/                   # 后端(FastAPI)
│   ├── main.py               # app = create_app(),中间件装配
│   ├── routes.py             # 路由(用户/组/分享页/会话/管理/公开)
│   ├── gateway.py            # 网关:SSE 代理/TokenStore/SessionStore/上游 RAGFlow 调用
│   ├── models.py             # ORM 模型(User/Group/SharePage/Grant/SessionOwner/...)
│   ├── auth.py               # 鉴权依赖(get_current_user/require_org_admin)
│   ├── password.py           # 密码哈希(argon2)
│   ├── oidc.py               # SSO(Authlib)
│   ├── config.py             # load_settings()
│   ├── db.py                 # 数据库引擎
│   └── tasks.py              # 后台任务(双删重试等)
├── frontend/                 # 前端(React + Vite + TS)
│   ├── src/
│   │   ├── main.tsx          # Router basename='/portal/'
│   │   ├── App.tsx
│   │   ├── api/client.ts     # API_BASE='/portal'(import.meta.env.PROD),所有 API 调用。Slice 44 加 NoCacheHtmlMiddleware 根治 API/SPA URL 重叠导致的缓存污染(非改 prefix)
│   │   ├── auth/AuthContext.tsx
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx
│   │   │   ├── SharePagesPage.tsx
│   │   │   ├── SharePageDetailPage.tsx   # 分享页详情(iframe + 会话列表)
│   │   │   └── admin/         # 管理后台(Users/Groups/SharePages/Grants/Sessions/AuditLogs)
│   │   ├── components/        # AdminLayout/AdminRoute/AppHeader/ProtectedRoute
│   │   ├── hooks/             # useAdminList/useOptimisticToggle
│   │   └── utils/
│   ├── tests/                # Vitest(89 tests)
│   ├── vite.config.ts        # base='/portal/'
│   └── vitest.config.ts
├── tests/                    # 后端 pytest(422 passed + 5 skipped)
│   ├── conftest.py
│   └── test_slice*.py        # 按 slice 组织的 E2E + 单测
└── docs/
    ├── README.md             # 文档目录
    ├── architecture.md       # 当前架构
    ├── configuration.md      # 配置说明
    ├── api.md                # API 说明
    ├── data-model.md         # 数据模型
    ├── development-and-testing.md
    ├── deployment-and-operations.md
    └── archive/              # 历史 PRD / ISSUES / NOTES / handoff / 早期决策
```

## 4. 硬约束(不可违反)

- **Portal 不得修改 RAGFlow 侧代码** —— portal 是代理层,不重写 RAGFlow 业务逻辑。本地 fork 对 RAGFlow 源码的改动(如 `bot_api.py` 扩展端点)通过 docker cp 部署到容器,不纳入 portal-extension 仓库
- **内部 RAGFlow 调用用 `RAGFLOW_HOST=:8080`**(直连,不经 nginx)
- **浏览器 RAGFlow 访问用 `RAGFLOW_BROWSER_ORIGIN=`**(相对路径,经 nginx + portal 代理)
- **Portal token 必须用 `pt_` 前缀**,与 RAGFlow beta token 区分(集中用 `PORTAL_TOKEN_PREFIX` 常量)
- **路由文件 `routes.py` 在 middleware 重构期间不得修改**
- **RAGFlow 容器是官方 v0.26.0 镜像**,不含本地 fork 改动。`api/apps/restful_apis/bot_api.py` 的 chatbot sessions 端点(GET/PATCH/DELETE)通过 docker cp 部署,容器重建会丢失
- **Portal 重启必须先 pkill 旧进程** —— `start.sh` 用 `exec` 不自动 pkill,否则端口被占用,新进程不启动,旧代码继续跑
- **数据文件必须与代码分离** —— `portal.db` 等运行时数据文件不能放在项目目录内,否则 `rsync --delete` 会删掉(Slice 34 已修复:portal.db 现位于 `~/portal-data/portal.db`)

## 5. 工程约定

- 所有 middleware 用 async/await,不用 callback 风格
- Portal token 前缀知识集中用 `PORTAL_TOKEN_PREFIX` 常量
- SSE 响应必须解析出 `session_id`,同时支持 flat `{session_id}` 与 nested `{data:{session_id}}` 结构
- Session 消息数通过 `SessionStore.increment_message_count(+1)` 在 SSE stream 成功后更新
- Portal 重启必须用 `start.sh` 脚本(加载 `.env`)
- 消息数语义:`message_count = len(RAGFlow history.messages)`(Slice 26 改动,非 Q&A 轮次)
- Session 归属:网关在 SSE 成功后从响应绑定 `session_id` 到当前用户(Slice 22),不依赖 iframe URL 预带 `session_id`
- iframe URL 不预带 `session_id`(Slice 22 根因:RAGFlow 前端不读 URL session_id,预创建产生孤儿 session + 后续 403)
- Portal chat/agent iframe URL 统一带 `default_theme=light`;该参数是默认值而非强制值,不得改成会覆盖显式选择的 `theme=light`
- Portal 嵌入主题使用 RAGFlow 独立 localStorage key `ragflow-portal-embed-ui-theme`;无 `default_theme` 的独立 RAGFlow 页面继续使用 `ragflow-ui-theme`
- 部署 rsync 必须排除 `--exclude='*.db'` 与 `--exclude='.env'`(数据文件与配置不得被 `--delete` 清掉)
- CSS flex column 容器内的滚动子元素需显式 `flex-shrink: 0`,否则会话增多时被压缩而非触发滚动条(Slice 31 修复)

## 6. 关键技术决策

### 6.1 令牌注入机制

RAGFlow 前端 `getAuthorization()` 优先读 URL `?auth=` 参数,回退读 `localStorage`。网关签发短期 portal token(`pt_` 前缀),通过 iframe URL `auth` 参数注入,租户级 beta Token 全程不离开网关。

### 6.1.1 Portal 嵌入主题初始化

网关构造 chat/agent iframe URL 时附加 `default_theme=light`。RAGFlow `RootProvider` 只在参数值为 `light`/`dark` 时使用该默认值和独立的 `ragflow-portal-embed-ui-theme` 存储 key；无参数或非法值保持原有深色默认值与 `ragflow-ui-theme`。因此 Portal 新会话与历史 iframe 重载均默认为浅色，同时不会读取、覆盖独立 RAGFlow 应用或管理后台的主题偏好。独立 key 中已有明确值时仍优先于默认值。

### 6.2 会话归属映射

门户 `SessionOwner` 表记录 `user_id ↔ session_id` 映射,消息正文与引用以 RAGFlow `API4Conversation` 为唯一事实源。网关在 SSE 成功后绑定 session 到用户(Slice 22),不依赖前端传 session_id。

### 6.3 双删策略

删除会话时先删门户 `SessionOwner` 记录,再调 RAGFlow `DELETE` 端点。RAGFlow 失败则门户记录标记 `pending_deletion`,后台任务重试(Slice 6/12)。

### 6.4 RAGFlow bot segment 不一致

| 端点类型 | chat | agent | RAGFlow 路径来源 |
|---|---|---|---|
| completions | `/api/v1/chatbots/<id>/completions` | `/api/v1/agentbots/<id>/completions` | `bot_api.py` |
| sessions(GET/PATCH/DELETE) | `/api/v1/chatbots/<id>/sessions/<sid>` | `/api/v1/agents/<id>/sessions/<sid>` | chat 在 `bot_api.py`(fork),agent 在 `agent_api.py`(官方) |

portal 网关 `_ragflow_bot_segment` 用于 completions(agent → "agentbots"),sessions 端点需用 `_ragflow_sessions_segment`(agent → "agents")。Slice 30 修复 agent sessions URL。

Issue 82 的引用资源链：成功恢复且通过归属校验的 history 响应把引用 `doc_id` 绑定到当前 `pt_`；`/api/v1/thumbnails` 只能查询该集合。非 base64 缩略图路径会附加绑定 token/doc/image 的 `pit_` 票据，`<img>` 请求凭同源 Portal cookie + 票据取图，不需要 RAGFlow 登录态。无 Portal token/票据的原生 RAGFlow 请求保持透传。

### 6.5 UI 重设计边界(2026-07-09 grilling 决策)

> 这组边界描述的是 2026-07-09 的首轮视觉迁移。2026-07-13 的生产化收口 Issue 75–81 已明确追加纯前端工作，其中 Issue 77 覆盖下述第 3 项并实现移动卡片/侧栏，Issue 79 覆盖第 4–5 项并引入统一弹窗组件，Issue 78 以构建开关收口未配置的 SSO 入口。仍然有效的边界是：不新增后端接口、不实现设计稿中缺少后端能力的功能、不替换 RAGFlow 原生 chat UI。

基于 `project-materials/portal-ui-redesign/` 设计稿(Open Design 产出,D1 Graphite 方向,9 屏 HTML 原型 + 完整设计系统 token)。本轮为 **纯视觉换皮**,以下决策已锁定:

1. **范围:纯视觉换皮**。现有功能边界不动,不排后端工作。设计稿里的新元素(顶部全局搜索、stats 统计卡、通知铃铛、批量导入)本轮砍掉或装饰占位,不做真功能。补功能后续独立 issue。
2. **Admin 布局:横向 tab → topbar + 左侧栏**。逻辑不动(保留 `<Outlet/>`),只改 JSX 骨架 + CSS。sidebar 是 admin 专属(share-list / share-detail / login 不套)。理由:6+ 项左侧栏更适合管理后台(DESIGN.md 结论)。
3. **响应式:桌面优先(1024+),小屏不崩**(<768 内容可滚、不裁切)。不实现移动端卡片态 / sidebar 抽屉 / 菜单按钮。移动端全响应式适配后续独立 issue。
4. **迁移方式:直接迁 CSS class**,不抽 React UI 组件。以设计稿 `project-materials/portal-ui-redesign/css/styles.css` 为基础替换现有 `styles.css`,JSX 改 className 对齐。**必须保留并合并**现有已验证的布局修复:Slice 31 `flex-shrink:0`、Slice 38 `min-height:0` + `.detail-grid flex:1`、Slice 51 `.admin-main > section` 滚动容器。
5. **交互模式:保留现有交互**(内联表单 / window.prompt / window.confirm),不引入设计稿的 drawer 抽屉。drawer 后续可独立 issue。
6. **stats 统计卡:砍掉**。Admin 页直接 filter + table,不显示统计卡(后端无聚合接口,占位假数据会误导)。
7. **设计方向:D1 Graphite**(`css/styles.css` 已实现)。D2 Midnight / D3 Coral 为对比稿,不采用。

细节决策:图标用内联 SVG(设计稿已是,不引 icon 库);topbar 砍全局搜索/通知,保留 brand + avatar + 登出;sidebar 保留"分享页"入口(回用户侧)+ 6 项 admin,砍"系统-设置"(无页);表格操作保留文字按钮,不改 icon-btn。

## 7. 已知限制(待后续 issue 修复)

- **agent 类型 sessions 端点 404**:portal 网关对 agent sessions 用 "agentbots",但 RAGFlow 官方只有 `/agents/<id>/sessions/<sid>`。Slice 30 修复(拆分 segment 函数),但 agent 类型暂时搁置不验收
- **RAGFlow `web` 侧 Jest 跑不起来**(`umi/test` 模块缺失),靠 `npm run build` 兜底验证
- **Issue 16 AC2 悬浮组件 UI 未实现**:`/widget/<id>` 仅占位 HTML,React 悬浮组件未建(Phase 2 后续待办)
- **portal.db 已移到 `~/portal-data/`**(Slice 34 已实施):`PORTAL_DB_URL=sqlite:////home/xijuangu/portal-data/portal.db`,代码与数据分离,rsync 加 `--exclude='*.db'` 兜底
- **share 页面切换会话后 reference 消失已修复**(Slice 36 已实施):`fetchSessionHistory` 现存 `conversationReference` state,`share/index.tsx` 传给 `buildMessageItemReference`,切换 session_id 时在 useEffect 重置

## 8. 教训(Lessons Learned)

- koa-connect wrapper 导致 ctx 泄漏;需 native Koa middleware 重写
- Portal 重启清空 TokenStore,过期 token 会被透传;`pt_` 前缀触发重新鉴权避免此问题
- iframe URL 用 `RAGFLOW_HOST` 导致直接调 RAGFlow:8080 带无效 portal token,鉴权失败
- iframe URL 预带 `session_id` 参数预创建 session,RAGFlow 前端不使用(Slice 22 根因),导致孤儿 session + 后续 `/completions` 403
- Slice 24 改 RAGFlow 前端读 URL `session_id` 跳过 `fetchSessionId` greeting,与 portal 前端 `handleNewSession` 的 precreate 路径冲突 —— 新建会话无 greeting(Slice 28 修复)
- RAGFlow 官方 v0.26.0 镜像不含本地 fork 加的 chatbot sessions 端点,docker cp 部署后才能用(Slice 27)
- `start.sh` 用 `exec` 不自动 pkill 旧进程,必须手动 pkill 再 start(Slice 27 部署时踩坑;**Slice 35 已修复**:start.sh 内置 pgrep 自动清理)
- `pkill -f "uvicorn portal.main:app"` 会误伤 ssh 会话本身(ssh 命令行含该字符串被匹配),导致 ssh 退出码 255;用 `pgrep -f` 精确匹配 PID 后 kill 可避免(**Slice 35 已实施**,start.sh + deploy.sh 固化)
- **pkill + nohup 不能放在同一条 ssh 命令中**:pkill 杀掉 ssh 后 `nohup start.sh` 不会执行,portal 进程不启动 → 502。必须分两条 ssh 命令(§9.1)。Slice 40 部署踩坑两次(**Slice 35 deploy.sh 已固化流程**)
- **ssh 远程执行 nohup 后台命令挂起**(uvicorn 长期进程):纯 `setsid`/`nohup & disown` 在 uvicorn 上仍挂起 —— ssh 远端 shell 退出后仍等待继承 stdout fd(portal.log)的后台进程。**解法**:用 `ssh -f` 从客户端侧后台化 ssh 进程,远端 shell 立即退出,ssh 不等待(Slice 35 deploy.sh 优化)
- **API URL 与 SPA 路由 URL 重叠导致浏览器缓存污染**(Slice 43 诊断 + Slice 44 根治):`StaticFiles(html=True)` 对导航请求(`Accept: text/html`)返回 SPA index.html 且无 `Cache-Control` 头,被浏览器缓存后污染同 URL 的 API fetch(`JSON.parse(html)` 抛错 → "加载失败")。**Slice 44 方案 B 根治**:加 `NoCacheHtmlMiddleware` 对 text/html 响应加 `Cache-Control: no-store` + `Vary: Accept`,JSON 响应不受影响,前端 `cache:'no-store'` workaround 已移除。未改 API 路径(router 不加 prefix),263 处测试零改动
- **`.env` 缺失导致「密码错误」**:`start.sh` 的 `source .env` 失败但脚本无 `set -e`,`PORTAL_ADMIN_PASSWORD` 取空 → 密码哈希为空 → 任何密码都失败。部署后必须验证 `/login` 返回 200(§9.6)
- `PORTAL_DB_URL` 默认 `sqlite://`(in-memory),进程退出即清空 `chat_session_owner` 表,用户「历史会话没了」;必须显式配置文件型 SQLite 或 MySQL(Slice 34 已实施:`~/portal-data/portal.db`)
- CSS flex column 容器内的滚动子元素默认 `flex-shrink: 1`,会话增多时被压缩而非触发 `overflow-y: auto`;必须显式 `flex-shrink: 0`(Slice 31 修复)
- **CSS 同选择器 min-height 冲突**:`.detail-grid` 曾同时声明 `min-height: 0` 和 `min-height: 480px`,后者覆盖前者导致 flex 收缩失效。不要在同一选择器声明冲突属性(Slice 38 /review 修复)
- **autoSize inline 对象导致 SSE 期间输入框抖动**(Slice 41 诊断 + 修复):`message-input/next.tsx` 的 `autoSize={{ minRows: 2, maxRows: 8 }}` 是 inline 对象字面量,每次渲染创建新引用 → `textarea.tsx` 的 `adjustHeight` effect(依赖 autoSize)每个 SSE chunk 触发 → `style.height='auto'` 塌缩 → rAF 读 scrollHeight → 写回高度,形成 8-10px 高度振荡(用户看到的「抽搐」)。**解法**:提为模块级常量 `AUTO_SIZE_CONFIG` 稳定引用,effect 只在首次挂载触发。**教训**:React 中传给子组件的 inline 对象/数组字面量会破坏 `useCallback`/`useMemo` 引用稳定性,应提为模块级常量。诊断时需建 Playwright red-capable 循环采样 `getBoundingClientRect()`,用 4 模式 probe(css-fix / no-scroll / observe / combined)分离假设
- **iframe EmbedContainer 标题读 `/info` 端点 dialog.name 而非会话标题**(Slice 42 诊断 + 修复):用户看到的「law-test-01」不是 portal 会话列表标题(读 portal.db `chat_session_owner.title`),而是 RAGFlow iframe 内 `EmbedContainer` 头部显示的值(读 `/info` 端点返回的 `data.title` = RAGFlow `dialog.name`)。portal 的 `proxy_bot_json_to_ragflow` 原样回传 RAGFlow 响应,泄露内部 dialog 名。**解法**:代理返回前解析 JSON,把 `data.title` 替换为 `share_page.name`。**教训**:「标题」在不同层有多个数据源(portal.db title / RAGFlow API4Conversation.name / RAGFlow dialog.name via /info),诊断显示问题需先定位用户看到的值来自哪个数据流,再判断修复点;curl + DB 查询组合是快循环

## 9. 运维约束

### 9.1 portal 重启与部署

**Slice 35 已固化部署脚本**(commit 831fd0c,后优化 ssh -f 解决挂起):单条 `bash deploy.sh` 完成 rsync + 重启 + 健康检查 + 日志 tail,不挂起。`start.sh` 用 `pgrep -f` 精确匹配旧进程后 kill(替代 `pkill`,不误伤 ssh 会话)。

**推荐:一键部署**(在本地 `ragflow/portal-extension/` 目录):
```bash
bash deploy.sh
```
deploy.sh 内部流程:
1. rsync 后端代码(含完整 exclude 保护 `.env`/`.venv`/`*.db`/`portal.log`/`project-materials`)
2. rsync 前端 dist(本地无 dist 则提示先 `npm run build`)
3. `ssh -f` 远程执行 `nohup bash start.sh </dev/null &`(关键:用 `ssh -f` 让 ssh 本身后台化,解决远端 uvicorn 长期进程持有 stdout fd 导致 ssh 挂起的问题;纯 `setsid`/`nohup &` 在 uvicorn 长期进程上仍挂起)
4. 循环 curl 健康检查(200=完全就绪 / 401=后端就绪 dist 未部署,最多 5 次 2s 间隔)
5. 失败时 `tail -20 portal.log` 辅助排查

**start.sh 完整内容**(版本控制,`~/portal-extension/start.sh`,Slice 35 改进后):
```bash
#!/bin/bash
cd ~/portal-extension
source .venv/bin/activate
set -a
source .env        # 加载环境变量(无 set -e,.env 缺失不报错继续 → 密码错误,见 §9.6)
set +a
# Slice 35:pgrep 精确匹配旧进程,不误伤 ssh 会话(pkill -f 会匹配 ssh 命令行导致退出 255)
OLD_PIDS=$(pgrep -f "python -m uvicorn portal.main:app" || true)
if [ -n "$OLD_PIDS" ]; then
  echo "killing old portal pids: $OLD_PIDS"
  kill $OLD_PIDS 2>/dev/null || true
  sleep 2
fi
exec python -m uvicorn portal.main:app --host 0.0.0.0 --port 8000
```

**deploy.sh 远程重启核心行**(ssh -f 方案,`>/dev/null 2>&1` 防管道挂起):
```bash
ssh -f "$REMOTE_HOST" "cd $REMOTE_DIR && nohup bash start.sh > portal.log 2>&1 </dev/null &" </dev/null >/dev/null 2>&1
```
> 注意:`>/dev/null 2>&1` 重定向 ssh 自身 stdout/stderr — 后台化的 ssh 不继承调用方 stdout fd,避免 `bash deploy.sh | tail` 管道因 ssh 持有 fd 而 tail 等不到 EOF 永久挂起(Slice 41/42 部署时踩坑)。

**手动 fallback**(deploy.sh 失败时,注意仍需分两条 ssh 命令,因 start.sh 内置 pgrep 在 ssh 远程执行时 `bash start.sh` 命令行不含 `uvicorn` 字符串故不会误杀 ssh):
```bash
# 命令 1:杀旧进程(现已由 start.sh 内置,但手动 fallback 仍可用 pkill;ssh 退出 255 是正常的)
ssh 172.16.10.180 'pkill -f "uvicorn portal.main:app" || true; sleep 2'
# 命令 2:启动新进程(也需 ssh -f 避免挂起,或用 nohup & disown)
ssh -f 172.16.10.180 'cd ~/portal-extension && nohup bash start.sh > portal.log 2>&1 </dev/null &'
```

**历史教训(已由 Slice 35 + ssh -f 优化解决)**:
- `pkill -f "uvicorn portal.main:app"` 会匹配 ssh 命令行本身误伤 ssh(退出 255)→ 改用 start.sh 内置 pgrep
- pkill + nohup 不能放同一条 ssh 命令(pkill 杀 ssh 后 nohup 不执行 → 502)→ start.sh 内置 pgrep,deploy.sh 只执行 `bash start.sh`
- **ssh 远程执行 nohup 后台命令挂起**(uvicorn 长期进程持有 stdout fd):纯 `setsid`/`nohup & disown` 在 uvicorn 上仍挂起 → 改用 `ssh -f` 从客户端侧后台化 ssh 进程,远端 shell 立即退出,ssh 不等待
- **`ssh -f` 后台进程继承调用方 stdout fd 导致管道挂起**(Slice 41/42 部署时踩坑):`bash deploy.sh | tail` 中,`ssh -f` 后台化的 ssh 进程继承了 deploy.sh 的 stdout pipe fd,tail 等不到 EOF 永久挂起(部署实际成功但脚本不退出)。**解法**:ssh 命令加 `>/dev/null 2>&1` 重定向自身 stdout/stderr,后台化 ssh 不持有调用方 fd

### 9.2 RAGFlow bot_api 扩展端点(docker cp 临时替换)

RAGFlow 容器是官方 v0.26.0 镜像,不含本地 fork 在 Slice 2/5 加的 chatbot sessions 端点(`GET/PATCH/DELETE /api/v1/chatbots/<dialog_id>/sessions/<session_id>`)。通过 docker cp 替换容器内 `bot_api.py`。

**容器重建边界**:`docker restart` 不丢,`docker compose down -v` 或重新 `up` 镜像会丢,需重新执行。

```bash
# 1. 备份容器内原文件
TS=$(date +%Y%m%d-%H%M%S)
docker exec docker-ragflow-cpu-1 cp /ragflow/api/apps/restful_apis/bot_api.py /ragflow/api/apps/restful_apis/bot_api.py.bak.$TS

# 2. 替换(从本地 fork 源码)
scp ragflow/api/apps/restful_apis/bot_api.py 172.16.10.180:/tmp/bot_api.py
ssh 172.16.10.180 'docker cp /tmp/bot_api.py docker-ragflow-cpu-1:/ragflow/api/apps/restful_apis/bot_api.py'

# 3. 重启 api server(不重启容器,避免丢其他 cp 文件)
ssh 172.16.10.180 'docker exec docker-ragflow-cpu-1 pkill -f ragflow_server.py; sleep 15'

# 4. 验证端点(返回 200 非 404)
ssh 172.16.10.180 'curl -s -o /dev/null -w "%{http_code}" http://172.16.10.180:8080/api/v1/chatbots/<dialog_id>/sessions/<session_id>'

# 回滚
docker exec docker-ragflow-cpu-1 bash -c "cp /ragflow/api/apps/restful_apis/bot_api.py.bak.<ts> /ragflow/api/apps/restful_apis/bot_api.py && pkill -f ragflow_server.py"
```

### 9.3 RAGFlow web 前端 dist 替换

修改 RAGFlow `web/` 源码后,需 build + tar + scp + docker cp 替换容器内 `/ragflow/web/dist`,并 reload 容器内 nginx。容器重建同样会丢失。

Issue 83 的浅色主题由 Portal URL 参数与 RAGFlow web 初始化共同完成,部署/回滚必须同时覆盖 Portal 和 RAGFlow web dist。生产验证使用全新浏览器上下文清空 localStorage/cookie,并分别检查新会话和历史会话。2026-07-14 生产备份点:`/ragflow/web/dist.bak.issue83.20260714-115804`;精确 Playwright `1 passed`,只读主流程 `4 passed, 1 skipped`。

```bash
# 1. 本地 build
cd ragflow/web && npm run build

# 2. 打包 + 传输
tar -czf /tmp/dist.tar.gz -C dist .
scp /tmp/dist.tar.gz 172.16.10.180:/tmp/

# 3. 备份容器内原 dist + 替换 + reload
ssh 172.16.10.180 'TS=$(date +%Y%m%d-%H%M%S); docker exec docker-ragflow-cpu-1 bash -c "cp -r /ragflow/web/dist /ragflow/web/dist.bak.$TS && rm -rf /ragflow/web/dist/* && tar -xzf /tmp/dist.tar.gz -C /ragflow/web/dist && nginx -s reload && echo DEPLOYED"'
```

**回滚**:
```bash
ssh 172.16.10.180 'docker exec docker-ragflow-cpu-1 bash -c "rm -rf /ragflow/web/dist && mv /ragflow/web/dist.bak.<ts> /ragflow/web/dist && nginx -s reload"'
```

**注意**:macOS tar 会输出 `Ignoring unknown extended header keyword 'LIBARCHIVE.xattr.com.apple.provenance'` 警告,不影响解压。

### 9.4 回滚点

- 容器内 `bot_api.py.bak.<timestamp>` 是 RAGFlow bot_api 回滚点
- 容器内 `dist.bak.<timestamp>` 是 RAGFlow web dist 回滚点
- git 分支 `portal-extension` 的每个 slice commit 是 portal 代码回滚点

### 9.5 portal 数据持久化配置(Slice 34 已实施)

服务器 `.env` 必须设置 `PORTAL_DB_URL`,否则默认 `sqlite://`(in-memory)导致每次重启 `chat_session_owner` 表清空(用户「历史会话没了」)。

**当前配置**(Slice 34 后,portal.db 与代码分离):
```bash
PORTAL_DB_URL=sqlite:////home/xijuangu/portal-data/portal.db
```

portal.db 位于独立的 `~/portal-data/` 目录,`rsync --delete` 不会触碰;部署 rsync 命令兜底加 `--exclude='*.db'`。

**rsync 部署命令模板**:
```bash
# 全量同步 portal-extension(排除数据文件、配置、缓存)
rsync -avz --delete \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.pyc' \
  --exclude='node_modules' --exclude='frontend/node_modules' --exclude='frontend/dist' \
  --exclude='.venv' --exclude='*.db' --exclude='.env' --exclude='portal.log' \
  --exclude='project-materials' \
  ragflow/portal-extension/ 172.16.10.180:~/portal-extension/

# 单独同步前端 dist(前端 build 后)
rsync -avz ragflow/portal-extension/frontend/dist/ 172.16.10.180:~/portal-extension/frontend/dist/
```

验证命令:
```bash
ssh 172.16.10.180 'grep PORTAL_DB_URL ~/portal-extension/.env'
ssh 172.16.10.180 'ls -la ~/portal-data/portal.db 2>/dev/null || echo "portal.db 不存在"'
```

### 9.6 .env 存在性检查与部署后验证(Slice 40 部署踩坑后补)

**`.env` 缺失是「部署后 admin 密码错误」的首要原因**:`start.sh` 的 `source .env` 失败但脚本无 `set -e`,`PORTAL_ADMIN_PASSWORD` 取空字符串 → admin 密码哈希为空 → 任何密码都「密码错误」(见 `auth.py:34`)。Slice 40 部署后踩坑一次(2026-07-08,.env 完全缺失)。

**部署前必检**:
```bash
ssh 172.16.10.180 'test -f ~/portal-extension/.env && echo ".env exists" || echo "FATAL: .env missing"; grep -c "^PORTAL_ADMIN_PASSWORD=" ~/portal-extension/.env 2>/dev/null'
```
若 `.env` 缺失或 `PORTAL_ADMIN_PASSWORD=` 行数为 0,**不得启动 portal**,先从备份恢复或重建 .env(需 admin 密码、RAGFLOW_BETA_TOKEN、RAGFLOW_DIALOG_ID 等敏感值,从 RAGFlow MySQL `api_token`/`dialog` 表查得,见 §9.7)。

**部署后必验**(portal 重启后立即执行):
```bash
# 1. 进程存活 + 监听 8000
ssh 172.16.10.180 'pgrep -f "uvicorn portal.main:app" | head -1; ss -tlnp 2>/dev/null | grep :8000'
# 2. 登录 200(非 401)
ssh 172.16.10.180 'curl -s -X POST http://localhost:8000/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"<pwd>\"}" -w "\nHTTP %{http_code}\n"'
# 3. nginx 200(非 502)
ssh 172.16.10.180 'curl -s -o /dev/null -w "nginx /portal/ -> %{http_code}\n" http://localhost:80/portal/'
```
任一项失败即回滚(检查 .env / 进程 / nginx upstream)。

### 9.7 .env 配置项

服务器 `~/portal-extension/.env` 必须包含以下 key(2026-07-08 验证):

| 变量 | 获取途径 | 说明 |
|---|---|---|
| `PORTAL_ADMIN_USERNAME` | 固定 `admin` | 管理员用户名 |
| `PORTAL_ADMIN_PASSWORD` | 用户设定(不存仓库) | admin 明文密码(启动时 argon2 哈希) |
| `PORTAL_USER2_USERNAME` | 固定 `user2`(可选) | 测试用第二用户 |
| `PORTAL_USER2_PASSWORD` | 用户设定(可选) | user2 明文密码 |
| `PORTAL_SESSION_SECRET` | `python3 -c "import secrets; print(secrets.token_hex(32))"` 生成 | 会话 cookie 签名密钥 |
| `RAGFLOW_HOST` | `http://172.16.10.180:8080` | 容器 80→主机 8080 端口映射 |
| `RAGFLOW_BROWSER_ORIGIN` | 空字符串(同源时) | 空=同源,浏览器用相对路径经 nginx+portal 代理 |
| `RAGFLOW_BETA_TOKEN` | `docker exec docker-ragflow-cpu-1 python3 -c "import pymysql; c=pymysql.connect(host='mysql',user='root',password='<MYSQL_PASSWORD>',database='rag_flow'); cur=c.cursor(); cur.execute('SELECT beta FROM api_token LIMIT 1'); print(cur.fetchone()[0])"`(MYSQL_PASSWORD 从 `~/ragflow/docker/.env` 的 `MYSQL_PASSWORD=` 取) | RAGFlow `api_token.beta` 列值 |
| `RAGFLOW_DIALOG_ID` | 同上 DB,`SELECT id FROM dialog WHERE name='<分享页对应的知识库名>' LIMIT 1` | 硬编码分享页关联的 dialog_id |
| `PORTAL_DB_URL` | `sqlite:////home/xijuangu/portal-data/portal.db` | Slice 34,文件型 SQLite |

config.py 还支持可选变量(有默认值,不配不影响运行):`T_SHORT_TTL_SECONDS`、`RETRY_DELETE_INTERVAL_SECONDS`、`OIDC_*`、`PORTAL_DEFAULT_ORG_ID`、`PUBLIC_RATE_LIMIT_PER_MIN`、`PUBLIC_AUDIT_ENABLED`、`WIDGET_FRAME_ANCESTORS`。

## 10. 测试策略

- **后端**:pytest,按 slice/issue 组织(`tests/test_slice*.py`、`tests/test_issue*.py`),基线 435 passed + 5 skipped(2026-07-14)
- **前端**:Vitest,按页面/组件组织(`frontend/tests/*.test.tsx`),基线 89 passed(2026-07-13)
- **RAGFlow web**:Jest + esbuild transformer,基线 25 passed(2026-07-14);生产构建使用 `npm run build`
- **E2E**:Playwright + 浏览器手动验收结合。`test/playwright/portal_extension/` 覆盖 portal 实际使用主路径、6 个管理后台 tab、Slice 44 缓存回归、Issue 82 全新浏览器恢复“劳动法”含引用历史会话、Issue 83 新/历史会话浅色及引用预览、临时用户 CRUD、用户组成员和分享页表单;`PORTAL_E2E_RUN_CHAT=1` 时额外发送真实 RAGFlow 问题并等待回复完成;acceptance criteria 记录在 `docs/archive/ISSUES.md`
- 类型检查:前端 `tsc --noEmit`,后端 `ruff check`

Portal Playwright 运行命令(需真实已部署环境,本地 agent 不默认执行):

```bash
PORTAL_E2E_BASE_URL=http://172.16.10.180/portal \
PORTAL_E2E_ADMIN_USERNAME=admin \
PORTAL_E2E_ADMIN_PASSWORD='<admin-password>' \
uv run pytest -q test/playwright/portal_extension -s --junitxml=/tmp/playwright-portal.xml
```

如需真实聊天慢用例,追加 `PORTAL_E2E_RUN_CHAT=1`;可选 `PORTAL_E2E_CHAT_QUESTION`、`PORTAL_E2E_CHAT_TIMEOUT_MS`、`PORTAL_E2E_CHECK_ELEVATED_CHAT=1`。说明:CRUD 用例只创建 `pw-user-<timestamp>` / `pw-group-user-<timestamp>` 临时用户,并在同一测试中撤销授权、移除用户组成员和硬删除;若中途失败,finally 会用管理员 API 清理临时用户。用户组和分享页没有删除端点,测试不会创建永久用户组或分享页。

### 10.1 回归测试套件(每次部署 / 合并 / 验收后必跑)

每次部署到 172.16.10.180 后、合并外部 worktree / PR 前、验收新 Slice 后,必须依次跑以下三套,全部绿才算回归通过。三套互相独立,可分别执行;受影响层的套件至少要跑(如只改后端则 1+3,只改前端则 2+3)。

**触发时机**:
- 部署后(每次 `bash deploy.sh` 完成后)
- 合并外部 worktree / cherry-pick 前
- 验收新 Slice 后
- 修复 bug 后(至少跑受影响层的套件)

**1. 后端 pytest(本地,无外部依赖)**

```bash
cd ragflow/portal-extension
uv run pytest -q
```
- 基线:435 passed + 5 skipped(2026-07-14)
- 前置:无(测试用临时 SQLite,不连真实 MySQL/RAGFlow)
- 失败处理:看 `tests/test_slice*.py` 对应 slice 的断言

**2. 前端 Vitest(本地,无外部依赖)**

```bash
cd ragflow/portal-extension/frontend
npm run test
```
- 基线:89 passed(2026-07-13)
- 前置:已 `npm install`
- 失败处理:看 `frontend/tests/*.test.tsx`

**3. Playwright E2E(需真实已部署环境)**

```bash
cd ragflow
UV_PROJECT_ENVIRONMENT=.venv-playwright \
PORTAL_E2E_BASE_URL=http://172.16.10.180/portal \
PORTAL_E2E_ADMIN_USERNAME=admin \
PORTAL_E2E_ADMIN_PASSWORD='<admin-password>' \
uv run --python 3.13 pytest -q test/playwright/portal_extension -s --junitxml=/tmp/playwright-portal.xml
```
- 基线:6 passed + 1 skipped(2026-07-14 验收；跳过项为真实聊天慢用例)
- 前置:172.16.10.180 已部署最新代码 + `.venv-playwright` 已装 playwright 依赖 + admin 密码与服务器 `.env` 一致
- 默认覆盖:登录、分享页列表、iframe shell、token 不泄露、6 个 admin tab 加载、Slice 44 缓存回归、Issue 82 含引用历史恢复、临时用户 CRUD + 审计 + 用户组成员 + 分享页表单
- **不纳入默认回归**:`PORTAL_E2E_RUN_CHAT=1` 真实聊天慢用例(每次跑会产生不可控对话内容 + ~20s 耗时),仅在验收聊天相关 Slice 或发版前手动追加
- 失败处理:看 `FAILURES` 段 + `test/playwright/artifacts/` 截图;CRUD 用例失败时 finally 会用管理员 API 清理临时用户(`pw-user-*` / `pw-group-user-*`)

**回归不通过的处置**:任一套件失败即阻塞该次部署 / 合并;先定位失败用例对应的 Slice,看 `docs/archive/ISSUES.md` 该 Slice 的验收记录与 AC,判断是代码回归还是测试本身需更新。基线数字更新时机:新增 Slice 测试用例后,在对应 Slice 验收记录里更新基线并在本节同步。

## 11. 用户文档计划(grill-with-docs 2026-07-09 确定)

三份独立文档,放 `docs/manual/`,Markdown 格式,纯文字 + Mermaid 流程图(不配截图,避免 UI 改动后过期)。中文撰写,技术术语保留英文。

### 11.1 文档清单与定位

| 文档 | 文件名 | 受众 | 定位 |
|---|---|---|---|
| 产品白皮书 | `docs/manual/whitepaper.md` | 技术决策者 / 运维 / 深入理解的管理员 | 产品定位 + 技术架构,自含原理,引用 architecture.md/api.md 做技术参考 |
| 管理员操作手册 | `docs/manual/admin-manual.md` | 管理员 | 概念 + 操作,快速入门章 + 按功能模块分章 |
| 普通用户使用指南 | `docs/manual/user-guide.md` | 普通用户(通过分享页对话) | 操作步骤,极简 |

### 11.2 白皮书范围(whitepaper.md)

自含原理层(用文字 + Mermaid 讲透),不重复技术参考细节:
- **产品定位**:解决 RAGFlow iframe 三大企业落地阻碍(租户 Token 泄露 / 会话丢失 / 缺用户审计)
- **设计理念**:同源架构(门户 + 网关 + RAGFlow 原生 iframe)、令牌不离开网关、短期可撤销令牌
- **技术原理**:网关签发 T_SHORT 令牌 → iframe `?auth=` 注入 → `getAuthorization()` 优先读 URL → SSE 代理校验 session_id 归属 → 会话双删(门户 + RAGFlow)
- **底部延伸阅读**:链接到 `architecture.md`(容器/路由)、`api.md`(API 签名)、`data-model.md`(表结构)

### 11.3 管理员手册结构(admin-manual.md)

快速入门章 + 按功能模块分章(与 admin sidebar 一致):
1. 快速入门:走一遍典型工作流(建用户 → 建分享页 → 授权 → 登录验证)
2. 登录与登出
3. 用户管理:创建用户(含初始密码)、启用/禁用、重置密码(`PATCH /admin/users/:id/password`)
4. 用户组管理:创建组、添加/移除成员
5. 分享页管理:CRUD 分享页(含 RAGFlow 资源 ID 配置)
6. 授权管理:把分享页授权给用户或用户组、撤销授权
7. 会话管理:查看用户会话
8. 审计日志:按操作者/类型/时间筛选敏感操作记录

每章结构:概念(2-3 段,引用白皮书深入)→ 操作步骤(文字描述 UI 路径)→ 注意事项。

### 11.4 用户指南范围(user-guide.md)

登录 → 分享页列表 → 打开对话 → 新建会话 → 对话 → 查看历史会话 → 重命名/删除会话 → 登出。

### 11.5 写作顺序

白皮书 → 管理员手册 → 用户指南。白皮书先确立概念术语,手册概念章节引用白皮书,用户指南最后写(内容最少)。

### 11.6 维护约束

- 手册不配截图(UI 改动后截图过期,维护成本高);用文字描述 UI 路径("sidebar 用户管理 → 创建用户按钮")
- 白皮书与 architecture.md 的边界:白皮书讲"原理"(为什么、怎么工作),architecture.md 讲"技术参考"(容器清单、路由表、代码结构);两者不重复,白皮书链接到 architecture.md
- 新功能上线后需同步更新对应手册章节
