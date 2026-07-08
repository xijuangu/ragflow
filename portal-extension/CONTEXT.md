# Portal Extension — 项目上下文

> 整个项目的持久上下文:目标、架构、代码结构、约束、约定、已知限制、教训、运维。
> 接手前必读本文 + `docs/ISSUES.md` + `docs/PRD.md`。
> `docs/HANDOFF-phase3.md` 只记录操作细节与当前环境状态,不重复本文内容。

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
浏览器 → nginx :80
          ├─ /portal/            → rewrite ^/portal/(.*) → portal:8000(前端 SPA + API)
          ├─ /portal/assets/     → rewrite → portal:8000/assets/
          ├─ /api/v1/(chatbots|agentbots)/  → portal:8000(统一代理:/info、/inputs、/completions、/sessions)
          └─ /                   → RAGFlow:8080(原生)
```

- **nginx 容器**:`portal-nginx`,配置在 `~/portal-nginx/conf.d/default.conf`
- **RAGFlow**:容器 `docker-ragflow-cpu-1`,官方 v0.26.0 镜像;`~/ragflow/docker/.env` 设 `SVR_WEB_HTTP_PORT=172.16.10.180:8080`(腾出 :80 给 nginx)
- **portal**:uvicorn `--port 8000 --host 0.0.0.0`,启动脚本 `~/portal-extension/start.sh`

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
│   │   ├── api/client.ts     # API_BASE='/portal/api',所有 API 调用
│   │   ├── auth/AuthContext.tsx
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx
│   │   │   ├── SharePagesPage.tsx
│   │   │   ├── SharePageDetailPage.tsx   # 分享页详情(iframe + 会话列表)
│   │   │   └── admin/         # 管理后台(Users/Groups/SharePages/Grants/Sessions/AuditLogs)
│   │   ├── components/        # AdminLayout/AdminRoute/AppHeader/ProtectedRoute
│   │   ├── hooks/             # useAdminList/useOptimisticToggle
│   │   └── utils/
│   ├── tests/                # Vitest(73 tests)
│   ├── vite.config.ts        # base='/portal/'
│   └── vitest.config.ts
├── tests/                    # 后端 pytest(387 passed + 5 skipped)
│   ├── conftest.py
│   └── test_slice*.py        # 按 slice 组织的 E2E + 单测
└── docs/
    ├── PRD.md                # 产品需求文档
    ├── ISSUES.md             # Issue 跟踪(Issue 1-36 + TD1-TD20)
    ├── NOTES.md              # 原型验证记录(H1-H9 假设验证)
    ├── HANDOFF-phase3.md     # Phase 3 操作细节(部署命令、环境状态)
    ├── HANDOFF.md            # 早期 handoff
    ├── ragflow-portal-decisions.md  # 关键技术决策
    └── ragflow-prototype-handoff.md
```

## 4. 硬约束(不可违反)

- **Portal 不得修改 RAGFlow 侧代码** —— portal 是代理层,不重写 RAGFlow 业务逻辑。本地 fork 对 RAGFlow 源码的改动(如 `bot_api.py` 扩展端点)通过 docker cp 部署到容器,不纳入 portal-extension 仓库
- **内部 RAGFlow 调用用 `RAGFLOW_HOST=:8080`**(直连,不经 nginx)
- **浏览器 RAGFlow 访问用 `RAGFLOW_BROWSER_ORIGIN=`**(相对路径,经 nginx + portal 代理)
- **Portal token 必须用 `pt_` 前缀**,与 RAGFlow beta token 区分(集中用 `PORTAL_TOKEN_PREFIX` 常量)
- **路由文件 `routes.py` 在 middleware 重构期间不得修改**
- **RAGFlow 容器是官方 v0.26.0 镜像**,不含本地 fork 改动。`api/apps/restful_apis/bot_api.py` 的 chatbot sessions 端点(GET/PATCH/DELETE)通过 docker cp 部署,容器重建会丢失
- **Portal 重启必须先 pkill 旧进程** —— `start.sh` 用 `exec` 不自动 pkill,否则端口被占用,新进程不启动,旧代码继续跑
- **数据文件必须与代码分离** —— `portal.db` 等运行时数据文件不能放在项目目录内,否则 `rsync --delete` 会删掉(Slice 34 修复前当前位于 `~/portal-extension/portal.db`,有风险)

## 5. 工程约定

- 所有 middleware 用 async/await,不用 callback 风格
- Portal token 前缀知识集中用 `PORTAL_TOKEN_PREFIX` 常量
- SSE 响应必须解析出 `session_id`,同时支持 flat `{session_id}` 与 nested `{data:{session_id}}` 结构
- Session 消息数通过 `SessionStore.increment_message_count(+1)` 在 SSE stream 成功后更新
- Portal 重启必须用 `start.sh` 脚本(加载 `.env`)
- 消息数语义:`message_count = len(RAGFlow history.messages)`(Slice 26 改动,非 Q&A 轮次)
- Session 归属:网关在 SSE 成功后从响应绑定 `session_id` 到当前用户(Slice 22),不依赖 iframe URL 预带 `session_id`
- iframe URL 不预带 `session_id`(Slice 22 根因:RAGFlow 前端不读 URL session_id,预创建产生孤儿 session + 后续 403)
- 部署 rsync 必须排除 `--exclude='*.db'` 与 `--exclude='.env'`(数据文件与配置不得被 `--delete` 清掉)
- CSS flex column 容器内的滚动子元素需显式 `flex-shrink: 0`,否则会话增多时被压缩而非触发滚动条(Slice 31 修复)

## 6. 关键技术决策

### 6.1 令牌注入机制

RAGFlow 前端 `getAuthorization()` 优先读 URL `?auth=` 参数,回退读 `localStorage`。网关签发短期 portal token(`pt_` 前缀),通过 iframe URL `auth` 参数注入,租户级 beta Token 全程不离开网关。

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
- `start.sh` 用 `exec` 不自动 pkill 旧进程,必须手动 pkill 再 start(Slice 27 部署时踩坑)
- `pkill -f "uvicorn portal.main:app"` 会误伤 ssh 会话本身(ssh 命令行含该字符串被匹配),导致 ssh 退出码 255;用 `pgrep -f` 精确匹配 PID 后 kill 可避免(Slice 35 待实施)
- `PORTAL_DB_URL` 默认 `sqlite://`(in-memory),进程退出即清空 `chat_session_owner` 表,用户「历史会话没了」;必须显式配置文件型 SQLite 或 MySQL(Slice 34 待实施)
- CSS flex column 容器内的滚动子元素默认 `flex-shrink: 1`,会话增多时被压缩而非触发 `overflow-y: auto`;必须显式 `flex-shrink: 0`(Slice 31 修复)

## 9. 运维约束

### 9.1 portal 重启

```bash
# 当前方式(临时):pkill 误伤 ssh 会话(退出码 255,但实际杀进程成功),需分两条命令
ssh 172.16.10.180 'pkill -f "uvicorn portal.main:app" || true; sleep 2'
ssh 172.16.10.180 'cd ~/portal-extension && nohup bash start.sh > portal.log 2>&1 < /dev/null & disown'

# Slice 35 待实施:改进为 pgrep 精确匹配 + start.sh 自动清理,单条命令重启
# ssh 172.16.10.180 'bash ~/portal-extension/start.sh'  # 自动 pkill 旧进程 + 启动新进程
```

### 9.2 RAGFlow bot_api 扩展端点(docker cp 临时替换)

容器重建(`docker compose down -v` 或重新 `up` 镜像)会丢失 cp 的文件,需重新执行或改 volume 挂载。详见 `docs/HANDOFF-phase3.md` §5.1。

### 9.3 RAGFlow web 前端 dist 替换

修改 RAGFlow `web/` 源码后,需 build + tar + rsync + docker cp 替换容器内 `/ragflow/web/dist`,并 `docker exec nginx -s reload`。容器重建同样会丢失。详见 `docs/HANDOFF-phase3.md` §5.2。

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

portal.db 位于独立的 `~/portal-data/` 目录,`rsync --delete` 不会触碰;部署 rsync 命令兜底加 `--exclude='*.db'`(见 HANDOFF-phase3.md)。

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

### 9.7 .env 敏感值获取途径

| 变量 | 获取途径 |
|---|---|
| `PORTAL_ADMIN_PASSWORD` | 用户设定(不存仓库) |
| `PORTAL_SESSION_SECRET` | `python3 -c "import secrets; print(secrets.token_hex(32))"` 生成 |
| `RAGFLOW_HOST` | `http://172.16.10.180:8080`(容器 80→主机 8080 端口映射) |
| `RAGFLOW_BETA_TOKEN` | `docker exec docker-ragflow-cpu-1 python3 -c "import pymysql; c=pymysql.connect(host='mysql',user='root',password='<MYSQL_PASSWORD>',database='rag_flow'); cur=c.cursor(); cur.execute('SELECT beta FROM api_token LIMIT 1'); print(cur.fetchone()[0])"`(MYSQL_PASSWORD 从 `~/ragflow/docker/.env` 的 `MYSQL_PASSWORD=` 取) |
| `RAGFLOW_DIALOG_ID` | 同上,`SELECT id FROM dialog WHERE name='<分享页对应的知识库名>' LIMIT 1` |
| `PORTAL_DB_URL` | `sqlite:////home/xijuangu/portal-data/portal.db`(Slice 34) |

## 10. 测试策略

- **后端**:pytest,按 slice 组织(`tests/test_slice*.py`),基线 415 passed + 5 skipped(Slice 40 后)
- **前端**:Vitest,按页面/组件组织(`frontend/tests/*.test.tsx`),基线 76 passed(Slice 28 后)
- **RAGFlow web**:Jest 跑不起来(`umi/test` 缺失),靠 `npm run build` 兜底
- **E2E**:浏览器手动验收,acceptance criteria 记录在 `docs/ISSUES.md` 各 slice
- 类型检查:前端 `tsc --noEmit`,后端 `ruff check`
