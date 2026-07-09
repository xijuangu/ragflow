# RAGFlow 权限门户

这是一个部署在 RAGFlow 旁路的权限门户与嵌入访问网关。它不替换 RAGFlow 原生问答界面，而是在外层补齐企业使用需要的登录、用户/用户组、分享页 ACL、历史会话、管理员审计和同源代理能力。

RAGFlow 上游源码不是本目录的维护对象。本目录只维护 `portal-extension` 自身的后端、前端、测试、部署脚本和项目文档。

## 当前状态

一期权限门户已完成，主要能力包括：

- 自建账号登录、登出、会话 cookie、账号启用/禁用。
- 用户、用户组、分享页、授权的管理员维护。
- 普通用户只看到被授权分享页，并能查看、恢复、重命名、删除自己的历史会话。
- 网关签发 `pt_` 前缀短期门户令牌，通过 RAGFlow iframe URL 的 `auth` 参数注入，真实 `RAGFLOW_BETA_TOKEN` 只留在服务端。
- `/api/v1/chatbots/*` 和 `/api/v1/agentbots/*` 的同源代理，覆盖 completions、info/inputs、sessions history。
- 管理员会话搜索、元数据查看、正文 elevated 查看审计、待删除会话重试。
- SQLAlchemy 持久化，支持 SQLite/MySQL；短期令牌和公开分享限流保留内存态。
- OIDC SSO、org 字段隔离、公开分享、widget/agent 路径已有代码与测试覆盖，但部分能力是否纳入生产使用要按当前验收范围单独确认。
- React + Vite 前端，提供登录页、分享页列表、分享页详情、6 个管理员页面。
- 本地后端 pytest、前端 Vitest、生产 Playwright 回归路径已建立。

## 架构

```text
浏览器
  -> /portal/ React 权限门户
  -> /api/v1/... 同源网关代理
       - 校验 portal_session
       - 校验分享页授权和会话归属
       - 校验/替换 pt_ 短期令牌
       - 用服务端 RAGFLOW_BETA_TOKEN 调 RAGFlow
  -> RAGFlow 原生 iframe
```

核心边界：

- 门户只保存身份、授权、会话归属、审计和展示标题。
- 消息正文、引用片段、文档预览仍以 RAGFlow `API4Conversation` 为事实源。
- 浏览器拿到的是短期 `pt_` 门户令牌，不拿 RAGFlow beta token。
- 非 widget 页面默认 `X-Frame-Options: SAMEORIGIN`；widget 页面通过 CSP `frame-ancestors` 控制跨域嵌入。

更详细的说明见 [架构文档](docs/architecture.md)。

## 目录

```text
portal-extension/
├── portal/                 # FastAPI 后端与网关
├── frontend/               # React + Vite 前端
├── tests/                  # 后端 pytest
├── docs/                   # 当前文档 + 历史 PRD/ISSUES/验证记录
├── project-materials/      # 原始 PRD、原型、UI 设计稿、RAG 验证材料
├── deploy.sh               # 同步并重启生产 portal
├── start.sh                # 服务器侧启动脚本
├── pyproject.toml          # 后端依赖和 pytest/ruff 配置
└── CONTEXT.md              # 长上下文/历史运维记录
```

## 快速开始

后端：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
uv sync --python 3.13 --extra dev

export PORTAL_ADMIN_PASSWORD=testpass123
export PORTAL_SESSION_SECRET=dev-secret-change-me
export RAGFLOW_HOST=http://localhost:9380
export RAGFLOW_BROWSER_ORIGIN=
export RAGFLOW_BETA_TOKEN=fake-beta-token
export RAGFLOW_DIALOG_ID=test-dialog-id
export PORTAL_DB_URL=sqlite:///./portal-dev.db

uv run uvicorn portal.main:app --host 0.0.0.0 --port 8000 --reload
```

前端开发：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm install
npm run dev
```

生产构建由 FastAPI 托管 `frontend/dist`：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm run build
```

## 常用命令

```bash
# 后端测试
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
uv run pytest -q
uv run ruff check portal tests

# 前端测试
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm run test
npm run typecheck
npm run lint

# 生产部署
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
bash deploy.sh
```

详见 [开发与测试](docs/development-and-testing.md) 和 [部署运维](docs/deployment-and-operations.md)。

## 文档地图

- [架构文档](docs/architecture.md)：系统边界、请求链路、安全模型、功能边界。
- [配置文档](docs/configuration.md)：环境变量、默认值、生产建议。
- [API 文档](docs/api.md)：门户、网关、管理员、公开分享接口。
- [数据模型](docs/data-model.md)：ORM 表、字段语义、持久化策略。
- [开发与测试](docs/development-and-testing.md)：本地运行、测试套件、质量门禁。
- [部署运维](docs/deployment-and-operations.md)：生产拓扑、部署脚本、健康检查、回滚。
- [前端说明](frontend/README.md)：前端路由、API 客户端、构建和测试。
- [项目材料](project-materials/README.md)：早期 PRD、原型、UI 设计稿和 RAG 验证文件。
- [历史归档](docs/archive/README.md)：PRD、ISSUES、NOTES、handoff、早期决策和 UI PRD 等追溯材料。

## 重要约束

- 不要把真实 `RAGFLOW_BETA_TOKEN` 写入代码、文档示例或前端构建产物。
- `PORTAL_DB_URL` 生产必须指向持久化数据库；默认 `sqlite://` 是内存库，重启会丢运行数据。
- iframe URL 必须使用 `RAGFLOW_BROWSER_ORIGIN` 生成浏览器可访问地址；内部上游调用使用 `RAGFLOW_HOST`。
- `pt_` 前缀是门户短期令牌边界，网关用它区分门户令牌和 RAGFlow 原生 token。
- 生产部署时运行时数据和 `.env` 必须与代码目录分离，`deploy.sh` 已排除 `.env`、`.venv`、`*.db` 和日志。
- RAGFlow 官方容器内的 chatbot sessions 扩展端点如果通过 `docker cp` 临时替换，容器重建后会丢失，需要重新部署。
