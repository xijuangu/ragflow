# RAGFlow 权限门户前端

前端是独立的 React + Vite + TypeScript 应用，生产构建后由 `portal-extension` 的 FastAPI 服务同源托管在 `/portal/` 下。它不直接接触 RAGFlow beta token，只调用门户 API 获取 iframe/widget URL。

## 页面

```text
/login                    登录
/share-pages              普通用户分享页列表
/share-pages/:id          分享页详情、iframe/widget 加载、我的会话
/admin/users              用户管理
/admin/groups             用户组管理
/admin/share-pages        分享页管理
/admin/grants             授权管理
/admin/sessions           会话搜索与 elevated 查看
/admin/audit              审计日志
```

生产路由 `basename` 为 `/portal`，开发环境为 `/`。对应配置在 `src/main.tsx`。

## 目录

```text
frontend/
├── src/
│   ├── api/client.ts              # fetch 封装，credentials include
│   ├── auth/AuthContext.tsx       # 登录态探测与登录/登出
│   ├── components/                # 路由守卫、顶栏、管理员布局
│   ├── hooks/                     # 管理列表和乐观开关 helper
│   ├── pages/                     # 用户侧与管理员页面
│   ├── utils/formatTime.ts
│   └── styles.css                 # 当前 Graphite 视觉样式
└── tests/                         # Vitest + Testing Library
```

## 开发

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm install
npm run dev
```

Vite 开发服务器通过 `vite.config.ts` 将 `/login`、`/logout`、`/me`、`/share-pages`、`/admin`、`/api`、`/public`、`/widget` 代理到本地 FastAPI，保持 cookie 行为接近生产同源。

## 构建

```bash
npm run build
```

构建执行 `tsc --noEmit && vite build`，产物输出到 `frontend/dist/`。后端启动时如果检测到该目录，会通过 `StaticFiles(html=True)` 托管 SPA。

## 测试

```bash
npm run test
npm run typecheck
npm run lint
```

测试覆盖登录、路由守卫、分享页列表、分享页详情、会话列表/恢复、管理员用户/用户组/分享页/授权/会话/审计页面，以及 widget/agent 入口的前端行为。

## API 客户端约定

- 所有请求使用 `credentials: 'include'`。
- 生产环境默认 `API_BASE=/portal`；开发环境默认空字符串。
- 可通过 `VITE_API_BASE` 覆盖 API 前缀。
- `getEmbedUrl` 支持 fullscreen 和 widget 两种响应：
  - fullscreen 返回 `iframe_url`。
  - widget 返回 `widget_url` 和可复制 `snippet`。
- elevated 查看会话正文前由页面做二次确认，再调用 `GET /admin/sessions/:id?elevated=true`。

## 设计边界

- 当前前端保留已有交互模型：内联表单、`window.prompt`、`window.confirm`。
- 管理后台使用 topbar + 左侧栏布局；普通用户分享页和登录页不套管理员布局。
- 视觉样式以 `portal-ui-redesign` 的 D1 Graphite 方向为基础，但不引入新的统计卡、全局搜索、通知铃铛或批量导入等未落地功能。
