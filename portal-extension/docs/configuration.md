# 配置文档

所有敏感配置通过环境变量传入，不写入仓库。服务器部署由 `start.sh` 加载 `~/portal-extension/.env`。

## 必需配置

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PORTAL_ADMIN_USERNAME` | `admin` | 初始管理员用户名。 |
| `PORTAL_ADMIN_PASSWORD` | 空 | 初始管理员明文密码，启动时哈希入库；生产必须配置。 |
| `PORTAL_SESSION_SECRET` | `dev-insecure-secret-change-me` | `portal_session` cookie 签名密钥；生产必须随机长字符串。 |
| `RAGFLOW_HOST` | `http://localhost:9380` | 后端内部访问 RAGFlow 的地址。生产通常直连 RAGFlow :8080，不经门户 nginx。 |
| `RAGFLOW_BETA_TOKEN` | 空 | RAGFlow `api_token.beta` 值，只允许在服务端存在。 |
| `RAGFLOW_DIALOG_ID` | 空 | 初始默认分享页绑定的 RAGFlow dialog id。 |
| `PORTAL_DB_URL` | `sqlite://` | Portal DB URL；生产必须用持久化 SQLite 或 MySQL。 |

## 常用可选配置

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PORTAL_USER2_USERNAME` | `user2` | 测试/种子普通用户。 |
| `PORTAL_USER2_PASSWORD` | 空 | 测试/种子普通用户密码。 |
| `T_SHORT_TTL_SECONDS` | `300` | `pt_` 短期令牌初始有效期（秒）。活跃 SSE 请求会触发 `TokenStore.touch()` 滑动续期，沿用签发时 TTL；实际令牌存活时间 = 最后一次活跃请求 + 此值。 |
| `RAGFLOW_BROWSER_ORIGIN` | 空 | 浏览器 iframe 使用的 RAGFlow origin；空表示同源相对路径。 |
| `RETRY_DELETE_INTERVAL_SECONDS` | `300` | 双删重试任务间隔；`<=0` 禁用定时任务。 |
| `PORTAL_DEFAULT_ORG_ID` | `default` | 默认 org id。 |
| `PUBLIC_RATE_LIMIT_PER_MIN` | `10` | 公开分享每 IP 每分钟请求上限；`<=0` 禁用。 |
| `PUBLIC_AUDIT_ENABLED` | `true` | 公开访问是否写 `public_chat` 审计。 |
| `WIDGET_FRAME_ANCESTORS` | `*` | `/widget/*` 页面的 CSP `frame-ancestors`。生产建议改为具体域名。 |

## OIDC 配置

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `OIDC_ENABLED` | `false` | 是否启用 SSO。 |
| `OIDC_ISSUER` | 空 | OIDC issuer URL。 |
| `OIDC_CLIENT_ID` | 空 | OIDC client id。 |
| `OIDC_CLIENT_SECRET` | 空 | OIDC client secret。 |
| `OIDC_REDIRECT_URI` | 空 | OIDC 回调地址。 |
| `SSO_AUTO_CREATE` | `true` | SSO 用户首次登录时是否自动创建本地用户。 |

启用 OIDC 时，上述 `OIDC_*` 四项必须完整，否则 `/sso/login` 返回配置错误。

## 前端构建配置

Vite 变量在 `npm run build` 时写入前端产物，不由 `start.sh` 运行时读取。可从 `frontend/.env.example` 复制生产配置：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `VITE_SSO_ENABLED` | `false` | 是否在登录页展示 SSO 入口。只有后端 `OIDC_ENABLED=true` 且 `/sso/login` 可用时才设为 `true`。 |
| `VITE_API_BASE` | 生产 `/portal`，开发空字符串 | 覆盖前端 API 前缀；同源标准部署通常无需设置。 |

```bash
cd frontend
cp .env.example .env.production
# 启用 OIDC 时取消下一行注释并设为 true
# VITE_SSO_ENABLED=true
npm ci
npm run build
```

## 示例 `.env`

```bash
PORTAL_ADMIN_USERNAME=admin
PORTAL_ADMIN_PASSWORD=change-me
PORTAL_USER2_USERNAME=user2
PORTAL_USER2_PASSWORD=change-me-too
PORTAL_SESSION_SECRET=replace-with-random-hex

RAGFLOW_HOST=http://172.16.10.180:8080
RAGFLOW_BROWSER_ORIGIN=
RAGFLOW_BETA_TOKEN=replace-with-ragflow-beta-token
RAGFLOW_DIALOG_ID=replace-with-dialog-id

PORTAL_DB_URL=sqlite:////home/xijuangu/portal-data/portal.db
T_SHORT_TTL_SECONDS=300
RETRY_DELETE_INTERVAL_SECONDS=300
```

## 生产注意事项

- 不要使用默认 `PORTAL_SESSION_SECRET`。
- 不要使用默认 `PORTAL_DB_URL=sqlite://`，它是内存库，进程重启后数据丢失。
- `RAGFLOW_HOST` 是后端上游地址，`RAGFLOW_BROWSER_ORIGIN` 是浏览器 iframe 地址，二者不要混用。
- 同源部署时 `RAGFLOW_BROWSER_ORIGIN` 保持空字符串，让 iframe 走 `/chats/share`、`/agent/share` 相对路径。
- `WIDGET_FRAME_ANCESTORS=*` 便于测试，但生产应收窄。
- `deploy.sh` 会排除 `.env`，不会把本地配置覆盖到服务器。
