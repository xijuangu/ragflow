# API 文档

本文档描述 `portal-extension` 当前对外 API。所有未特别说明的接口都返回 JSON。

## 通用约定

- 登录态使用 `portal_session` HTTP-only cookie。
- 前端请求必须带同源 cookie；`frontend/src/api/client.ts` 默认 `credentials: include`。
- 普通用户接口要求已登录且用户 `enabled=true`。
- 管理接口要求 `is_admin=true` 或 `org_admin=true`；`org_admin` 只能管理本 org。
- RAGFlow iframe 内的 API 请求通过 `Authorization: Bearer pt_...` 进入网关。

## 登录与当前用户

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/login` | 用户名密码登录，写 cookie，写登录审计。 |
| `POST` | `/logout` | 清除 cookie。 |
| `GET` | `/me` | 返回当前用户基础信息。 |
| `GET` | `/sso/login` | OIDC 登录入口；未启用返回 404。 |
| `GET` | `/sso/callback` | OIDC 回调，匹配或创建本地用户。 |

`POST /login` 请求：

```json
{"username":"admin","password":"******"}
```

响应：

```json
{"username":"admin","is_admin":true}
```

失败语义：

- `401 用户未注册`
- `401 密码错误`
- `403 账号已禁用`

## 普通用户分享页

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/share-pages` | 列出当前用户被授权的分享页。 |
| `GET` | `/share-pages/{id}/embed-url` | 获取 fullscreen iframe URL 或 widget snippet。 |
| `POST` | `/share-pages/{id}/sessions` | 预创建 RAGFlow session 并绑定当前用户。 |
| `GET` | `/share-pages/{id}/sessions` | 列出当前用户在该分享页下的会话。 |
| `GET` | `/share-pages/{id}/sessions/{sid}` | 恢复当前用户自己的会话。 |
| `PATCH` | `/share-pages/{id}/sessions/{sid}` | 重命名当前用户自己的会话。 |
| `DELETE` | `/share-pages/{id}/sessions/{sid}` | 删除当前用户自己的会话。 |

`GET /share-pages/{id}/embed-url` fullscreen 响应：

```json
{
  "iframe_url": "/chats/share?shared_id=dialog-id&auth=pt_xxx&from=chat",
  "ragflow_type": "chat",
  "share_page_id": "share-default",
  "expires_in": 300
}
```

widget 响应：

```json
{
  "embed_type": "widget",
  "widget_url": "/widget/share-widget",
  "snippet": "<iframe ...></iframe>",
  "share_page_id": "share-widget",
  "expires_in": 300
}
```

## RAGFlow 网关代理

这些路径主要由 RAGFlow iframe 前端调用。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/chatbots/{dialog_id}/completions` | Chat SSE 代理。 |
| `POST` | `/api/v1/agentbots/{agent_id}/completions` | Agent SSE 代理。 |
| `GET` | `/api/v1/chatbots/{dialog_id}/info` | Chat 分享页配置代理。 |
| `GET` | `/api/v1/agentbots/{agent_id}/inputs` | Agent 输入配置代理。 |
| `GET` | `/api/v1/chatbots/{dialog_id}/sessions/{sid}` | Chat session history 代理。 |
| `GET` | `/api/v1/agentbots/{agent_id}/sessions/{sid}` | Agent session history 代理。 |

标准校验链：

1. 如果是 `pt_` 门户令牌，必须存在、未过期、未撤销。
2. 标准令牌要求当前 cookie 登录态有效。
3. 用户或用户所属组必须仍有分享页 `use` 授权。
4. 请求带 `session_id` 时必须匹配 `chat_session_owner`。
5. `session_id` 对应的 `ragflow_resource_id` 必须匹配 URL 中的 dialog/agent id。

无 `pt_` 前缀的 token 会按原生 RAGFlow token 透传路径处理，用于兼容原生分享页场景。

## 管理员用户与用户组

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/admin/users` | 创建用户。 |
| `GET` | `/admin/users` | 列出用户，支持 `org_id`。 |
| `GET` | `/admin/users/{id}` | 查看用户。 |
| `PATCH` | `/admin/users/{id}` | 启用/禁用用户。 |
| `DELETE` | `/admin/users/{id}` | 硬删除用户并级联清理会话归属。 |
| `POST` | `/admin/groups` | 创建用户组。 |
| `GET` | `/admin/groups` | 列出用户组，含成员。 |
| `POST` | `/admin/groups/{id}/members` | 添加成员。 |
| `DELETE` | `/admin/groups/{id}/members/{user_id}` | 移除成员。 |

当前没有修改用户密码的 API。改密码需按 [部署运维文档](deployment-and-operations.md#修改用户密码) 在服务器侧更新 `portal_user.password_hash`。

## 管理员分享页与授权

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/admin/share-pages` | 创建分享页。 |
| `GET` | `/admin/share-pages` | 列出分享页。 |
| `PATCH` | `/admin/share-pages/{id}` | 更新 `enabled` 或 `is_public`。 |
| `POST` | `/admin/share-pages/{id}/grants` | 创建用户/组授权。 |
| `GET` | `/admin/share-pages/{id}/grants` | 列出分享页授权。 |
| `DELETE` | `/share-pages/{id}/grants/{subject_type}/{subject_id}` | 撤销授权并吊销相关短期令牌。 |

创建分享页请求：

```json
{
  "name": "法规问答",
  "ragflow_resource_id": "dialog-or-agent-id",
  "embed_type": "fullscreen",
  "ragflow_type": "chat"
}
```

`embed_type` 可选 `fullscreen`、`widget`；`ragflow_type` 可选 `chat`、`agent`。

## 管理员会话与审计

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/admin/sessions` | 搜索所有会话元数据。 |
| `GET` | `/admin/sessions/pending-deletion` | 查看待双删重试的会话。 |
| `GET` | `/admin/sessions/{sid}` | 查看会话元数据；`elevated=true` 返回正文并写审计。 |
| `POST` | `/admin/sessions/{sid}/retry-delete` | 手动重试删除。 |
| `DELETE` | `/admin/share-pages/{id}/sessions/{sid}` | 管理员删除任意用户会话。 |
| `GET` | `/admin/audit-logs` | 查询审计日志。 |
| `GET` | `/admin/orgs` | 列出 org。 |

`GET /admin/sessions` 查询参数：

```text
user_id
share_page_id
since
until
keyword
org_id
limit
```

`GET /admin/audit-logs` 查询参数：

```text
actor_user_id
action
since
until
limit
```

## 公开分享

公开分享是已有扩展能力，是否作为生产范围需单独确认。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/public/{share_page_id}` | 获取公开分享基础信息。 |
| `GET` | `/public/{share_page_id}/embed-url` | 获取公开访问嵌入 URL。 |
| `POST` | `/public/{share_page_id}/sessions` | 公开访问预创建会话。 |
| `POST` | `/public/{share_page_id}/sessions/{sid}/chat` | 公开访问聊天。 |

公开访问受 `share_page.is_public` 和 `PUBLIC_RATE_LIMIT_PER_MIN` 控制，可按配置写 `public_chat` 审计。

## Widget 页面

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/widget/{share_page_id}` | 返回 widget iframe 容器 HTML。 |

`/widget/*` 路径不设置 `X-Frame-Options`，改用 `Content-Security-Policy: frame-ancestors ...`。
