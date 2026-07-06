# RAGFlow 权限门户 — Slice 1 最小可登录分享页骨架

门户 + 嵌入访问网关,打通「用户登录 → 网关签发短期嵌入令牌 → iframe URL 注入令牌 →
RAGFlow 原生 bot_api 对话」端到端链路。**RAGFlow 侧无任何修改**;真实 beta Token
全程不离开网关服务端。

> 对应 `docs/ISSUES.md` 的 Issue 1 / Slice 1。
>
> 说明:portal-extension 用 FastAPI 作为独立网关服务,与主项目 Quart 分离
> (FastAPI 适合 SSE 异步代理,与主项目互不依赖)。

## 架构

```
用户 ──登录──> 门户(同源 cookie 会话)
用户 ──请求分享页──> 网关签发 T_short(5分钟,内存,可撤销)
                  └─> 返回 iframe URL: {RAGFLOW_HOST}/chat/share?shared_id={dialog_id}&auth={T_short}&from=chat
iframe ──SSE──> 网关代理:校验 T_short → 用 beta Token 调 RAGFlow bot_api → 流式回传
```

- iframe URL 的 `auth` 参数是 RAGFlow 前端 `getAuthorization()` 原生注入点(优先读 URL `?auth=`,
  回退才读 localStorage),真实 beta Token 全程不进浏览器。
- 网关 SSE 代理路径与 RAGFlow 前端原生调用路径一致(`/api/v1/chatbots/<id>/completions`),
  同源部署下 iframe 内前端发起的 SSE 天然走网关,无需反向代理配置。
- T_short 绑定用户 + 分享页,过期/撤销/dialog 不匹配均返回 401。

## 目录结构

```
portal-extension/
├── portal/
│   ├── main.py        # FastAPI app 入口(create_app + X-Frame-Options 中间件)
│   ├── config.py      # 配置(全部从环境变量读)
│   ├── auth.py        # 会话校验依赖(get_current_user)
│   ├── password.py    # bcrypt 密码哈希(独立模块避免循环导入)
│   ├── models.py      # 硬编码数据(portal_user / share_page / grant,字段用 Literal 约束)
│   ├── gateway.py     # T_short 签发/校验/撤销 + iframe URL 构造 + SSE 代理
│   └── routes.py      # /login、/share-pages/{id}/embed-url、/api/v1/chatbots/{id}/completions
├── tests/
│   ├── conftest.py    # pytest fixtures(测试客户端 + integration 跳过逻辑)
│   └── test_slice1_e2e.py  # 端到端测试(14 个:12 单元 + 2 integration)
├── docs/              # PRD / ISSUES / NOTES
├── pyproject.toml
└── README.md
```

## 环境变量

全部敏感值只走环境变量,不写入代码或文件。`.env` 已被 `.gitignore` 忽略。

| 变量 | 说明 | 示例 |
|---|---|---|
| `PORTAL_ADMIN_USERNAME` | 硬编码 admin 用户名 | `admin` |
| `PORTAL_ADMIN_PASSWORD` | admin 明文密码(启动时哈希) | 自定义 |
| `PORTAL_SESSION_SECRET` | 会话 cookie 签名密钥 | 随机长字符串 |
| `RAGFLOW_HOST` | RAGFlow web 地址 | `http://172.16.10.180` |
| `RAGFLOW_BETA_TOKEN` | RAGFlow `api_token.beta` 列值(网关持有) | 32 位字符串 |
| `RAGFLOW_DIALOG_ID` | 硬编码分享页关联的 dialog_id | `b4f88...` |
| `T_SHORT_TTL_SECONDS` | T_short 有效期(秒) | `300` |
| `PORTAL_DB_URL` | DB 连接 URL(SQLite/MySQL;默认 `sqlite://` in-memory) | `mysql+pymysql://user:pass@host:3306/portal` |
| `RETRY_DELETE_INTERVAL_SECONDS` | Slice 12 双删重试定时任务间隔(秒;默认 `300`;`<=0` 禁用,管理员仍可手动触发) | `300` |

## 一条命令运行

依赖管理用 [uv](https://docs.astral.sh/uv/)(与主项目一致,见 `AGENTS.md` / `CLAUDE.md`)。
若系统无 uv,先安装:`curl -LsSf https://astral.sh/uv/install.sh | sh`。

```bash
# 安装依赖(创建 .venv 并锁定)
uv sync --python 3.13 --extra dev

# 配置环境变量(示例,真实值请自行设置)
export PORTAL_ADMIN_PASSWORD=your-password
export PORTAL_SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export RAGFLOW_HOST=http://your-ragflow-host
export RAGFLOW_BETA_TOKEN=your-beta-token
export RAGFLOW_DIALOG_ID=your-dialog-id

# 启动
uv run uvicorn portal.main:app --port 8000 --reload
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/login` | 登录,建立同源会话 cookie |
| GET | `/share-pages/{id}/embed-url` | 返回 iframe URL(含 `auth=T_short`,需登录) |
| POST | `/api/v1/chatbots/{dialog_id}/completions` | SSE 代理(与 RAGFlow 前端原生路径一致;校验 T_short,用 beta Token 调 RAGFlow) |

所有响应附带 `X-Frame-Options: SAMEORIGIN`(PRD D10 同源嵌入,阻止外部站点 iframe)。

## 测试

```bash
# 单元测试(无需 RAGFlow,12 个,默认占位值)
uv run pytest tests/test_slice1_e2e.py -v

# 集成测试(需真实 RAGFlow,导出 RAGFLOW_BETA_TOKEN 与 RAGFLOW_HOST 后运行)
uv run pytest tests/test_slice1_e2e.py -v -m integration

# 全套(含 integration,需真实 RAGFlow 环境变量)
uv run pytest tests/test_slice1_e2e.py -v

# 静态检查与格式化
uv run ruff check portal/ tests/
uv run ruff format portal/ tests/
```

覆盖 7 个验收点:
1. admin 登录获同源会话 ✅
2. embed-url 含 `auth=T_short`,不含 beta Token ✅
3. iframe 对话流式 + 引用片段可见 ✅ [integration]
4. 无效/过期/撤销 T_short → 401 ✅
5. 未登录请求分享页 → 403 ✅
6. beta Token 调 RAGFlow SSE 流式回传 ✅ [integration]
7. beta Token 全程不泄露 ✅
