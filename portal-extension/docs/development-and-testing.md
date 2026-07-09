# 开发与测试

## 本地后端

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
uv sync --python 3.13 --extra dev

export PORTAL_ADMIN_PASSWORD=testpass123
export PORTAL_USER2_PASSWORD=testpass123
export PORTAL_SESSION_SECRET=dev-secret-change-me
export RAGFLOW_HOST=http://ragflow-mock.invalid
export RAGFLOW_BETA_TOKEN=fake-beta-token-for-local-dev
export RAGFLOW_DIALOG_ID=test-dialog-id
export PORTAL_DB_URL=sqlite:///./portal-dev.db

uv run uvicorn portal.main:app --host 0.0.0.0 --port 8000 --reload
```

测试默认会在 `tests/conftest.py` 中设置占位环境变量，后端单元测试不需要真实 RAGFlow。

## 本地前端

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm install
npm run dev
```

开发服务器默认运行在 Vite 端口，并把门户 API 代理到本地 FastAPI。

## 后端测试

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
uv run pytest -q
uv run ruff check portal tests
uv run ruff format portal tests
```

当前后端测试按 slice 组织，覆盖登录、网关令牌、会话恢复、授权撤销、用户组、双删、DB、重试任务、多租户、SSO、公开分享、widget/agent、cookie、缓存中间件等。

如需真实 RAGFlow 集成测试，先导出真实环境变量，再运行带 `integration` 标记的用例：

```bash
export RAGFLOW_HOST=http://your-ragflow-host
export RAGFLOW_BETA_TOKEN=your-beta-token
export RAGFLOW_DIALOG_ID=your-dialog-id
uv run pytest -q -m integration
```

## 前端测试

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm run test
npm run typecheck
npm run lint
```

前端测试使用 Vitest + jsdom + Testing Library。测试文件在 `frontend/tests/`。

## 生产 Playwright 回归

Playwright 用例位于 RAGFlow 子仓库的：

```text
/Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/test/playwright/portal_extension
```

默认回归命令：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow
UV_PROJECT_ENVIRONMENT=.venv-playwright \
PORTAL_E2E_BASE_URL=http://172.16.10.180/portal \
PORTAL_E2E_ADMIN_USERNAME=admin \
PORTAL_E2E_ADMIN_PASSWORD='<admin-password>' \
uv run --python 3.13 pytest -q test/playwright/portal_extension -s --junitxml=/tmp/playwright-portal.xml
```

需要真实聊天时追加：

```bash
PORTAL_E2E_RUN_CHAT=1
```

可选：

```bash
PORTAL_E2E_CHAT_QUESTION='你的问题'
PORTAL_E2E_CHAT_TIMEOUT_MS=60000
PORTAL_E2E_CHECK_ELEVATED_CHAT=1
```

## 合并/部署前建议门禁

至少运行：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
uv run pytest -q
uv run ruff check portal tests

cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm run test
npm run typecheck
npm run lint
npm run build
```

部署后再运行 Playwright 默认回归。任一失败都应阻塞发布或合并。

## 测试注意事项

- 后端测试用 SQLite in-memory，避免污染生产数据。
- 集成测试会连接真实 RAGFlow，只在明确需要时运行。
- `TokenStore` 是内存态，测试中重建 app 会清空令牌。
- 前端 API mock 应保持后端响应 shape，不要只按页面临时字段造假。
- 公开分享、widget、agent、OIDC 是扩展能力；改动相关代码时必须跑对应 slice 测试。
