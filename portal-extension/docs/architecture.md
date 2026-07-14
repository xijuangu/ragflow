# 架构文档

## 目标

权限门户解决 RAGFlow 原生 iframe 在企业落地中的三个问题：

1. RAGFlow iframe URL 暴露租户级 beta token。
2. iframe 会话存放在 `API4Conversation`，用户无法在门户中管理自己的历史会话。
3. RAGFlow tenant 不等于企业用户/角色体系，缺少用户组、ACL 和审计。

门户的原则是保留 RAGFlow 原生问答、引用和文档预览体验，只在外层补身份、授权、会话归属和审计。

## 组件

```text
React Portal
  - 登录、分享页列表、分享页详情、管理员后台
  - 不拼接 RAGFlow token

FastAPI Portal/Gateway
  - 账号、组、分享页、授权、会话归属、审计
  - 签发 pt_ 短期令牌
  - 代理 RAGFlow bot_api / agentbot_api
  - 按已验证会话引用范围代理缩略图和文档图片
  - 托管 frontend/dist

RAGFlow
  - 原生 iframe UI
  - 原生 completions 流式问答
  - API4Conversation 作为消息、引用、文档信息事实源
```

## 请求链路

### 登录后打开分享页

1. 用户登录门户，后端写入 `portal_session` HTTP-only cookie。
2. 前端调用 `GET /share-pages` 获取当前用户有权访问的分享页。
3. 前端进入详情页后调用 `GET /share-pages/{id}/embed-url`。
4. 网关检查登录态、分享页状态、用户/组授权。
5. 网关签发 `pt_...` 短期令牌，返回带 `default_theme=light` 的 RAGFlow iframe URL。
6. RAGFlow 前端从 URL `auth` 参数读取令牌，后续 `/api/v1/...` 请求走同源网关。

### Portal 嵌入主题

- `default_theme=light` 只表达 Portal iframe 的默认值，不使用会覆盖明确选择的 `theme=light`。
- RAGFlow 入口在检测到合法 `default_theme` 时，改用独立的 `ragflow-portal-embed-ui-theme` localStorage key；没有该参数时仍使用原有 `ragflow-ui-theme` 和深色默认值。
- 因此全新 Portal iframe 首帧沿用 CSS 的白色根变量并初始化为浅色；历史会话 iframe 重载时规则相同。Portal 内若已有明确主题选择，独立 key 的值优先于默认值。
- 独立访问 RAGFlow、管理后台或其他不带 `default_theme` 的入口不会读取或写入 Portal 嵌入主题 key。

### iframe 流式问答

1. iframe 发起 `POST /api/v1/chatbots/{id}/completions` 或 `POST /api/v1/agentbots/{id}/completions`。
2. 网关读取 `Authorization: Bearer pt_...`。
3. 网关校验短期令牌、cookie 登录态、分享页授权、会话归属和 resource id。
4. 网关把 Authorization 替换成服务端保存的 `RAGFLOW_BETA_TOKEN`，转发给 RAGFlow。
5. 网关增量解析完整 SSE 事件；事件含 `reference.doc_aggs` / `reference.chunks` 时，先把文档 ID 加入当前 `pt_` 的最小引用范围，再把原始事件回传浏览器。
6. 流成功后，网关绑定新 `session_id` 或更新会话活跃时间/消息数。

### 恢复历史会话

1. 前端调用 `GET /share-pages/{id}/sessions` 获取当前用户自己的会话列表。
2. 用户选择会话后，前端调用 `GET /share-pages/{id}/sessions/{sid}`。
3. iframe 用新的 `pt_` 调同源 sessions 端点；网关校验 Portal cookie、grant、resource id 和会话归属，再使用服务端 beta token 读取消息与引用。
4. 网关只把成功 history 响应中的 `doc_aggs[].doc_id` / `chunks[].document_id` 加入该 `pt_` 的内存文档范围。
5. iframe 请求 `GET /api/v1/thumbnails?doc_ids=...` 时，所有文档 ID 都必须属于该范围；校验通过后才换 beta token 请求 RAGFlow。
6. 如果缩略图值是 `/api/v1/documents/images/{image_id}`，网关把它改写为带 `portal_ticket` 的同源 URL。票据绑定基础 `pt_`、文档 ID、图片 ID，且寿命不超过基础令牌。
7. 浏览器 `<img>` 不带 Authorization；图片端点用票据和 Portal cookie 重新检查用户、grant、令牌及资源绑定，再代理二进制内容。因此恢复引用不依赖 RAGFlow cookie 或 localStorage。

## 安全模型

- 真实 RAGFlow beta token 只存在服务端环境变量 `RAGFLOW_BETA_TOKEN` 中。
- 门户令牌统一使用 `pt_` 前缀，默认 5 分钟过期，重启后全部失效。
- 授权撤销后，网关每次请求都会重新检查 grant；即使旧 `pt_` 未过期也会被拒绝。
- 用户会话隔离依赖 `chat_session_owner`，任何带 `session_id` 的请求都必须匹配当前用户和分享页 resource。
- 引用资源使用“先验证 history 或完整 SSE 引用事件，再授权文档”的能力收窄模型；`doc_ids` 不能凭 `pt_` 自行扩展。SSE 解析不依赖网络 chunk 边界，标准和公开 `pt_` 遵守同一范围规则。图片票据是单图片、不透明、短期凭据，基础令牌撤销或 grant 移除后立即不可用。
- 无 `pt_` 前缀且无 `portal_ticket` 的缩略图/图片请求保留 Authorization 与 Cookie 透传，兼容 RAGFlow 原生访问。
- 管理员 elevated 查看正文必须显式传 `elevated=true`，并写入 `session_view_elevated` 审计。
- 普通页面默认 `X-Frame-Options: SAMEORIGIN`；widget 页面使用 CSP `frame-ancestors`。

## 数据事实源

| 数据 | 事实源 |
|---|---|
| 门户用户、用户组、分享页、授权 | Portal DB |
| 会话归属、门户显示标题、消息数缓存 | Portal DB |
| 消息正文、引用、文档预览信息 | RAGFlow `API4Conversation` |
| 短期门户令牌 | Portal 进程内存 |
| 引用文档授权范围、图片票据 | Portal 进程内存（绑定短期门户令牌） |
| 公开分享 IP 限流 | Portal 进程内存 |

## 功能边界

已完成并应作为一期主体维护：

- 自建账号、cookie 登录、用户启停。
- 用户/组/分享页/ACL 管理。
- 授权分享页列表和详情页。
- 会话列表、恢复、重命名、删除。
- 管理员会话搜索、审计、待删除重试。
- 同源 iframe 网关代理和 token 替换。

已有代码但生产使用需单独确认范围：

- OIDC SSO。
- org 字段隔离和 org_admin。
- 公开分享。
- widget 嵌入。
- agent 类型分享页。

## 与 RAGFlow 的关系

`portal-extension` 主要依赖 RAGFlow 已有的 iframe、bot API 和 sessions 能力；本仓库 fork 另维护 Portal 嵌入主题初始化和历史会话恢复所需的最小前端接缝。生产环境需要部署本 fork 的 `web/dist`。如对 RAGFlow 容器通过 `docker cp` 增加 chatbot sessions GET/PATCH/DELETE 端点，该改动同样属于运行时补丁；容器重建后两类补丁都需要重新应用。
