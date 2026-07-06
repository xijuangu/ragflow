# RAGFlow 权限门户前端(Slice 9)

门户前端最小骨架,打通「登录页 → 登录成功 → 跳转分享页 → iframe 加载 → 对话可见」端到端链路。

## 栈选型:Vite + React + TypeScript

**选 Vite + React 而非 RAGFlow 现有 UmiJS 栈的理由:**

- **独立轻量:** Vite 启动快(<1s)、配置少,不耦合 RAGFlow 前端构建链(web/ 用 UmiJS + 大量业务依赖)。
- **零侵入:** 不修改 RAGFlow `web/` 任何代码,前端代码完全独立放在 `portal-extension/frontend/`。
- **TypeScript strict:** 与后端 ruff 严格度对齐,tsc `--noEmit` 干净。
- **构建产物可静态托管:** `npm run build` 产出 `dist/`,由 FastAPI `StaticFiles` 同源托管(X-Frame-Options: SAMEORIGIN 生效)。

## 目录结构

```
frontend/
├── index.html              # Vite 入口 HTML
├── package.json
├── tsconfig.json           # strict TypeScript
├── tsconfig.node.json      # vite/vitest 配置文件的 TS 配置
├── vite.config.ts          # Vite + dev server proxy(同源到 FastAPI)
├── vitest.config.ts        # Vitest + jsdom + Testing Library
├── .eslintrc.cjs           # ESLint(TypeScript + react-hooks)
├── .gitignore
├── src/
│   ├── main.tsx            # 应用入口(BrowserRouter + AuthProvider)
│   ├── App.tsx             # 路由定义(/login、/share-pages、/share-pages/:id)
│   ├── styles.css          # 最小化样式(纯 CSS,无设计稿)
│   ├── vite-env.d.ts
│   ├── api/
│   │   └── client.ts       # fetch 封装(login/logout/me/share-pages/embed-url)
│   ├── auth/
│   │   └── AuthContext.tsx # 认证上下文(通过 GET /me 探测登录态)
│   ├── components/
│   │   ├── AppHeader.tsx   # 顶栏(用户名 + 登出按钮)
│   │   └── ProtectedRoute.tsx  # 路由守卫(未登录跳 /login)
│   └── pages/
│       ├── LoginPage.tsx           # 登录页(用户名 + 密码 + 错误提示)
│       ├── SharePagesPage.tsx      # 分享页列表页
│       └── SharePageDetailPage.tsx # 分享页详情页(iframe 加载)
└── tests/
    ├── setup.ts                    # jest-dom + fetch mock helper
    ├── LoginPage.test.tsx          # 登录交互测试(4 用例)
    ├── SharePagesPage.test.tsx     # 列表加载测试(4 用例)
    ├── SharePageDetailPage.test.tsx # iframe 渲染测试(3 用例)
    └── RouteGuard.test.tsx         # 路由守卫 + 登出测试(4 用例)
```

## 开发

```bash
cd portal-extension/frontend
npm install
npm run dev        # Vite dev server: http://localhost:5173(代理 API 到 :8000)
```

开发时 Vite dev server (5173) 通过 `vite.config.ts` 的 proxy 把 `/login`、`/logout`、`/me`、
`/share-pages`、`/admin`、`/api` 路径代理到 FastAPI (8000),保证同源(cookie 自动携带)。

## 构建

```bash
npm run build      # tsc -b && vite build → dist/
```

构建产物 `dist/` 由后端 `portal/main.py` 末尾的 `StaticFiles(directory="frontend/dist", html=True)`
同源托管(仅当 `dist/` 存在时挂载)。生产部署时先 `npm run build` 再启动 FastAPI 即可。

## 测试

```bash
npm run test       # Vitest run(15 用例)
npm run typecheck  # tsc --noEmit
npm run lint       # ESLint
```

## 后端 API 依赖(Slice 1-7 已就绪 + Slice 9 新增 2 端点)

| 端点 | 方法 | 用途 | 来源 |
|---|---|---|---|
| `/login` | POST | 用户名/密码登录 → 同源 cookie | Slice 1 |
| `/logout` | POST | 清除会话 cookie(新增) | Slice 9 |
| `/me` | GET | 探测当前登录态(新增) | Slice 9 |
| `/share-pages` | GET | 列出被授权的分享页 | Slice 4 |
| `/share-pages/:id/embed-url` | GET | 获取 iframe URL(含 T_short) | Slice 1 |

> `/logout` 与 `/me` 为 Slice 9 新增端点(纯新增,不修改已有 API 契约)。
> `/logout` 必需:HTTP-only cookie 无法由前端 JS 清除,必须服务端清除。
> `/me` 用于前端路由守卫探测登录态(避免用 GET /share-pages 做探测的语义混淆)。

## iframe 安全机制

- iframe URL 通过 `GET /share-pages/:id/embed-url` 获取,含 `auth=T_short`(短期嵌入令牌,5 分钟过期)。
- iframe URL **不含**真实 beta Token(前端只透传后端返回的 URL,不拼接 token)。
- iframe 同源加载,门户 cookie 自动携带。
- `X-Frame-Options: SAMEORIGIN` 由后端中间件设置,前端不破坏。
