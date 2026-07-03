# RAGFlow 权限门户与历史会话改造 PRD

> 本 PRD 由 `grill-with-docs` 访谈(10 项决策)+ `prototype`(9 项假设 H1-H9 验证全部通过 / 8 项原型验收点全部通过)综合产出。
> 关键技术假设已由真实 RAGFlow v0.26.0(commit 92c4b76)实例验证,非纸面设计。
> 详细验证证据见 `prototype/NOTES.md`。

## Problem Statement

RAGFlow v0.26.0 的 iframe 嵌入方案存在三个阻碍企业落地的问题:

1. **租户级 Token 泄露风险:** RAGFlow 生成的 iframe URL 直接包含租户 API Token,前端将其写入浏览器 `localStorage`。该 Token 是**租户级**而非用户级或分享页级,任何拿到该 URL 的用户都能调用后端接口访问该租户的全部知识库与对话资源,无法按用户或分享页收窄范围。仅在外层页面加登录保护不够,用户仍可复制 iframe URL 或直接调后端。

2. **用户无法管理自己的历史会话:** Chat iframe 产生的会话存储在 `API4Conversation` 表,而 RAGFlow 官方普通 Chat 会话 API 操作的是另一张 `Conversation` 表,两者不互通。因此用户无法列出、查看、重命名、删除、继续自己在 iframe 中产生的历史会话,关闭页面后会话即「丢失」。

3. **缺乏用户/角色/审计能力:** RAGFlow 的 `tenant` 是资源租户而非用户角色体系,不提供门户级用户、用户组、分享页 ACL、管理员审计能力。

在保留 RAGFlow 原生问答界面、引用片段、引用标记与文档/PDF 预览的前提下,需要增加权限控制层,让每个用户能查看与继续自己的历史会话,管理员具备必要的会话管理与审计能力。

## Solution

在 RAGFlow 之外新建两层组件,形成「门户 + 网关 + RAGFlow 原生 iframe」的同源架构:

1. **权限门户(同源):** 自建账号体系,管理用户、用户组、分享页、分享页 ACL;提供「我的会话」列表与管理员后台。门户只保存身份、权限与会话归属映射,消息正文、引用、文档信息继续以 RAGFlow 的 `API4Conversation` 为唯一事实源。

2. **嵌入访问网关(同源):** 校验用户与分享页授权,签发短期、可撤销、资源受限嵌入令牌;通过 iframe URL 的 `auth` 参数注入令牌(RAGFlow 前端 `getAuthorization()` 原生优先读 URL `?auth=`,回退才读 localStorage,因此真实租户 Token 全程不离开网关);代理流式 SSE 并校验 `session_id` 归属;删除会话时双删门户与 RAGFlow 两侧记录。

3. **RAGFlow 最小扩展:** 新增 3 个 `API4Conversation` 受控端点(GET 读取会话消息与引用 / PATCH 重命名 / DELETE 删除),均复用现有 service 方法,无新逻辑。不重写聊天 UI,不替换 RAGFlow 原生引用渲染。

一期仅覆盖全屏 Chat(`embed_type=fullscreen` + `ragflow_type=chat`),同源部署,`X-Frame-Options: SAMEORIGIN`。

## User Stories

### 身份与登录

1. 作为普通用户,我希望能用门户自建账号登录,这样我不依赖外部 IdP 即可访问被授权的分享页。
2. 作为普通用户,我希望登录失败时有明确的错误提示(邮箱未注册 / 密码错误 / 账号已禁用),这样我知道如何修正。
3. 作为普通用户,我希望我的登录会话有合理过期时间,这样账号被窃取后风险有限。
4. 作为平台管理员,我希望我能启用或禁用某个用户账号,这样我能在人员变动时控制访问。
5. 作为平台管理员,我希望我禁用用户后该用户立即无法登录,但其历史会话保留(我仍可审计),这样停权不丢数据。
6. 作为平台管理员,我希望我硬删除某个用户时,其所有会话(门户归属映射 + RAGFlow `API4Conversation`)同步清除,这样不残留孤儿数据与隐私风险。

### 用户与用户组管理

7. 作为平台管理员,我希望能创建用户账号(用户名、邮箱、初始密码),这样我能为员工开通访问。
8. 作为平台管理员,我希望能创建用户组并添加成员,这样我能按组批量授权而非逐人操作。
9. 作为平台管理员,我希望能把用户从用户组移除,这样调岗时能调整授权范围。
10. 作为平台管理员,我希望能查看用户列表与用户组列表及其成员,这样我能掌握当前授权主体。
11. 作为平台管理员,我希望普通用户没有用户/组管理能力,这样不会发生越权创建或删除。

### 分享页管理

12. 作为平台管理员,我希望能创建分享页(关联某个 RAGFlow Chat dialog),这样把一个 RAGFlow 对话能力包装成可授权资源。
13. 作为平台管理员,我希望能启用或禁用某个分享页,这样我能临时下线某个对话而不删除配置。
14. 作为平台管理员,我希望能查看所有分享页列表及其关联的 RAGFlow 资源,这样我掌握对外提供的对话能力。
15. 作为平台管理员,我希望分享页的 `embed_type` 与 `ragflow_type` 一期固定为全屏 Chat,这样我不需要在尚未验证的悬浮/Agent 路径上做选择。

### 分享页 ACL

16. 作为平台管理员,我希望能把某个分享页授权给单个用户,这样特定人员可以访问。
17. 作为平台管理员,我希望能把某个分享页授权给某个用户组,这样组成员自动继承访问权。
18. 作为平台管理员,我希望能撤销某个用户或用户组对某个分享页的授权,这样权限收回。
19. 作为普通用户,我希望我只能看到被授权的分享页,这样不会看到无权访问的资源。
20. 作为普通用户,我希望授权撤销后,我已打开的 iframe 与历史链接立即失效,这样权限收回是实时的。
21. 作为普通用户,我希望授权撤销后我的历史会话默认保留(管理员仍可查),这样临时失权不会丢历史。

### 我的会话(普通用户)

22. 作为普通用户,我希望能列出我在某个分享页下的所有历史会话(标题、最后活跃时间),这样我能找回之前的对话。
23. 作为普通用户,我希望能点击某个历史会话重新打开,这样关闭页面后能继续之前的对话。
24. 作为普通用户,我希望重新打开的会话能完整恢复消息正文、引用片段、引用标记和 PDF 预览,这样体验与首次对话一致。
25. 作为普通用户,我希望能在已绑定的旧 session_id 上继续提问,并且流式响应正常,这样对话是连贯的。
26. 作为普通用户,我希望能重命名我的会话,这样标题有意义便于查找。
27. 作为普通用户,我希望能删除我的会话,这样能清理过期内容。
28. 作为普通用户,我希望删除会话是硬删除且门户与 RAGFlow 两侧同步清除,这样不会有残留幽灵会话。
29. 作为普通用户,我希望我不能访问另一用户创建的 session_id,这样我的会话私密。
30. 作为普通用户,我希望新建会话后系统能自动捕获并绑定 session_id 到我的账号,这样不需我手动操作。

### 嵌入访问(iframe)

31. 作为普通用户,我希望打开分享页时浏览器收到的 iframe URL 不含 RAGFlow 真实租户 Token,这样 Token 不会泄露到客户端。
32. 作为普通用户,我希望网关签发的嵌入令牌是短期且可撤销的,这样即使令牌被复制,有效期与撤销机制能限制风险。
33. 作为普通用户,我希望 iframe 内的对话、引用片段、引用标记、PDF 预览全部保留 RAGFlow 原生体验,这样不被替换为简化 UI。

### 管理员会话管理与审计

34. 作为平台管理员,我希望能按用户、分享页、时间范围、关键词搜索所有人的会话,这样能排查问题与合规审计。
35. 作为平台管理员,我希望能查看任意会话的元数据(标题、用户、时间、消息数)而不暴露正文,这样日常管理不侵犯隐私。
36. 作为平台管理员,我希望能查看任意会话的消息正文,但需要二次确认(如「以管理员身份查看 — 此操作将记录」),这样排查能力与隐私留痕并存。
37. 作为平台管理员,我希望能删除任意用户的会话,这样能响应数据清理或合规要求。
38. 作为平台管理员,我希望所有敏感操作(登录成功/失败、授权变更、删除会话、查看他人正文、启用/禁用用户)都写入审计日志,这样可追溯。
39. 作为平台管理员,我希望审计日志永久保留,这样长期可追溯。
40. 作为平台管理员,我希望普通用户的行为(列自己的会话、继续对话)不被全量记录,这样审计日志不噪音且存储可控。

### 运维与安全

41. 作为平台管理员,我希望分享页只在门户同源下加载(`X-Frame-Options: SAMEORIGIN`),这样不会被嵌入任意外部站点规避登录态。
42. 作为平台管理员,我希望网关每次请求都校验用户登录态、分享页授权、session 归属,这样任一环节失效都立即拒绝。
43. 作为平台管理员,我希望 RAGFlow 真实租户 Token 只存在于网关服务端配置,这样轮换 Token 不影响已分发的分享页。

## Implementation Decisions

### 架构决策(D1-D10 摘要,详见 `prototype/ragflow-portal-decisions.md`)

- **D1 身份来源:** 门户自建账号,独立于 RAGFlow;RAGFlow 侧不感知具体用户。
- **D2 租户隔离:** 单租户,数据模型不预留 `org_id`。
- **D3 访问门槛:** 仅登录用户,无公开分享;`chat_session_owner.portal_user_id` 为 `NOT NULL`。
- **D4 权限主体:** 用户 + 用户组,无部门树、无授权有效期、无审批流。
- **D5 管理员层级:** 两层(普通用户 + 平台管理员),`portal_user.is_admin` 布尔;保留 `share_page_grant.permission=manage` 字段值但一期不解析。
- **D6 用户会话能力:** 查看 + 继续对话 + 重命名 + 删除;无导出;硬删除,不设回收站。
- **D7 管理员访问与审计:** 默认只看元数据,查正文需二次确认 + 写审计;审计仅覆盖敏感操作。
- **D8 数据保留:** 禁用保留会话,硬删除用户时级联双删会话;审计永久保留;撤销授权立即拒绝继续但保留历史。
- **D9 一期范围:** 仅全屏 Chat,`embed_type`/`ragflow_type` 字段保留但固定值,不开放选择器。
- **D10 运行位置:** 仅门户内同源,`X-Frame-Options: SAMEORIGIN`,不维护外部域名白名单。

### 模块划分

1. **权限门户(新建):** 账号、用户组、分享页、ACL、我的会话、管理员后台、审计日志的 REST API 与登录会话管理。
2. **嵌入访问网关(新建):** 令牌签发与吊销、iframe URL 构造、SSE 代理、session 归属校验、双删协调。
3. **RAGFlow 最小后端扩展(在 RAGFlow 源码上扩展):** 3 个 `API4Conversation` 受控端点。
4. **RAGFlow 最小前端扩展:** 一期通过 URL 参数注入 `auth` 与 `session_id`,**无需修改 RAGFlow 前端源码**(已由原型 H1 验证:`getAuthorization()` 原生读 URL `?auth=`)。若需预创建 session 的 UI 提示,在门户侧实现,不侵入 RAGFlow 前端。

### 令牌注入机制(原型 H1 验证结论)

- RAGFlow `web/src/utils/authorization-util.ts` 的 `getAuthorization()` 优先读 URL `?auth=`,回退才读 `localStorage`。
- iframe URL 的 `auth` 参数是 RAGFlow 设计内的原生注入点,网关只需把租户 Token 替换为网关签发的短期令牌(`T_short`)。
- 真实租户 Token 全程不离开网关服务端。
- postMessage 注入原生不支持(需改源码),但 URL 参数已足够,一期不实现 postMessage 路径。

### Token 选用(原型 H2-H9 验证结论)

- iframe URL 的 `auth` 参数对应 RAGFlow `api_token` 表的 `beta` 列(非 `token` 列)。
- `token` 列(`ragflow-` 前缀)走 AUTH_API 路径,不适用于 `bot_api`。
- 网关持有 `beta` Token 调用 `bot_api`,对外签发 `T_short`。

### 首次对话双步行为(原型关键发现)

RAGFlow `async_iframe_completion` 在无 `session_id` 时**只创建 session 返回 prologue,不处理 question**。采用方案 B:

- 用户打开分享页时,门户**预创建 session**(调 RAGFlow 创建空 session)并绑定到 `chat_session_owner`,把 `session_id` 通过 iframe URL 注入。
- 首问直接带 `session_id`,RAGFlow 正常处理 question 与流式响应。
- 这样避免前端处理双步逻辑,session 归属在预创建时即绑定。

### RAGFlow 最小后端扩展端点(原型 H2/H7/H8 结论)

均复用现有 `API4ConversationService` 方法(`get_by_id` / `update_by_id` / `delete_by_id`),无新逻辑,仅加路由层与网关鉴权:

- `GET /api/v1/chatbots/<dialog_id>/sessions/<session_id>` —— 读取会话消息 + 引用片段(必须,H2)
- `PATCH /api/v1/chatbots/<dialog_id>/sessions/<session_id>` —— 重命名(可选,H7)
- `DELETE /api/v1/chatbots/<dialog_id>/sessions/<session_id>` —— 删除(必须,H8)

端点强制按 `dialog_id + session_id + 调用方身份(网关)` 校验,网关侧再按 `chat_session_owner.portal_user_id` 做用户级隔离(RAGFlow 仅租户级隔离,原型 H5 已确认)。

### 数据模型(原型沉淀,门户侧)

```text
portal_user(id, username, password_hash, email, is_admin, enabled, created_at)
portal_group(id, name, created_at)
portal_group_member(group_id, user_id, added_at)
share_page(id, name, ragflow_type='chat', ragflow_resource_id, embed_type='fullscreen', enabled)
share_page_grant(share_page_id, subject_type, subject_id, permission='use')
chat_session_owner(session_id, share_page_id, portal_user_id NOT NULL, ragflow_resource_id, title, created_at, last_active_at)
audit_log(actor_user_id, action, target_type, target_id, at, meta_json)
```

审计 `action` 枚举(最小化):`login_success | login_failure | grant_create | grant_revoke | session_delete | session_view_elevated | user_enable | user_disable`。

### 网关请求校验链(每次请求)

1. 门户登录态有效(同源 cookie)。
2. `share_page_grant` 存在(用户或其所属组对该分享页有 `use` 权限)。
3. 请求的 `session_id` 在 `chat_session_owner` 中归属当前用户(管理员 elevated 模式除外,且写审计)。
4. `session_id` 对应的 `dialog_id` 与 `share_page.ragflow_resource_id` 一致。
5. 全部通过 → 用 `beta` Token 调 RAGFlow `bot_api` 代理 SSE;任一失败 → 403。

### 撤销机制(原型 H6 结论)

- 撤销授权 = 删除 `share_page_grant` 行 + 吊销已签发的 `T_short`(网关内存令牌表标记失效)。
- RAGFlow 侧 `beta` Token 是租户级的,无法按分享页撤销,因此撤销由网关在每次请求校验时实现。
- 已由原型 H1 场景 5-6 验证:撤销后同令牌请求返回 401。

### 双删事务策略(原型 H8 结论)

- 删除会话时先删 RAGFlow `API4Conversation`(调扩展 DELETE 端点),再删门户 `chat_session_owner`。
- 若 RAGFlow 侧删除失败:门户侧不删,标记 `chat_session_owner.deleted_at` 待重试(后台清理任务),避免门户已删但 RAGFlow 残留的幽灵会话。
- 管理员可查看待重试记录并手动触发清理。

## Testing Decisions

### 主 seam:端到端 HTTP 流程测试

- **唯一主 seam**,符合「最少 seam」原则;门户 CRUD 与网关逻辑在其中自然覆盖。
- **驱动方式:** HTTP 客户端模拟用户全程行为,从门户登录到 RAGFlow SSE 对话再到撤销失效。
- **覆盖流程(对应 8 个原型验收点):**
  1. 用户登录并获得某个分享页权限。
  2. 网关不给浏览器暴露 RAGFlow 真正的 API Token(断言 iframe URL 含 `T_short` 不含 `beta` Token)。
  3. 新建会话后捕获并绑定 `session_id`(断言 `chat_session_owner` 有记录)。
  4. 关闭页面后从「我的会话」重新打开(用 session_id 重载)。
  5. 完整恢复消息、引用片段、引用标记和 PDF 预览(断言引用 chunks + doc_aggs + 文档预览可定位)。
  6. 在旧 `session_id` 上继续提问,流式响应正常(断言 SSE 流 + 新 message_id + session_id 不变)。
  7. 用户不能访问另一用户的 `session_id`(用户 B 调 A 的 session → 403)。
  8. 撤销分享权限后已有 iframe 与历史链接立即失效(撤销后同令牌请求 → 403)。

### 好测试的标准

- 只测外部行为(HTTP 响应状态、响应体、可观察的副作用),不测内部实现细节(不 assert 私有方法调用、不 mock 内部模块)。
- 断言三类副作用:HTTP 响应、门户 DB 状态(`chat_session_owner` 归属与 `audit_log` 记录)、RAGFlow 侧状态(通过网关 GET session 端点验证消息/引用存在)。
- 不依赖 RAGFlow 内部表结构(通过网关或 RAGFlow 扩展端点验证,不直连 MySQL)。

### 模块覆盖

- 门户 API(登录、用户/组/分享页/ACL/我的会话/管理员后台/审计)—— 在端到端流程中调用并断言。
- 网关(令牌签发/吊销、SSE 代理、session 归属校验、双删协调)—— 在端到端流程中验证各分支。
- RAGFlow 扩展端点(GET/PATCH/DELETE session)—— 经网关调用验证。

### 先例

- 原型阶段已用 curl + 真实 RAGFlow 实例验证 H1-H9,这 9 个验证脚本(见 `prototype/NOTES.md`)是端到端测试的雏形,可固化为自动化测试。
- 原型的 8 个验收点直接映射为端到端测试用例。

### 不测的部分

- RAGFlow 前端 UI 渲染(引用弹层、PDF 抽屉)—— 依赖浏览器,一期不引入 E2E 浏览器测试;通过 API 层断言引用数据完整即可。
- 门户管理 UI(一期无 UI 测试框架,只测 API)。
- 单元测试 seam 不单独设立(端到端覆盖足够,失败时再下沉定位)。

## Out of Scope

- **悬浮组件**(`floating-chat-widget.tsx`)的令牌注入与引用渲染 —— 原型未验证,延后。
- **Agent iframe** 的会话恢复与引用渲染 —— 原型未验证,延后(底层 `API4Conversation` 相同,但 Agent 可能含多步/工具调用)。
- **多租户隔离** —— 一期单租户,不预留 `org_id`。
- **公开分享**(匿名访问) —— 一期仅登录用户。
- **部门树、授权有效期、审批流** —— 一期不做。
- **分享页管理员角色** —— 一期仅平台管理员 + 普通用户两层;`share_page_grant.permission=manage` 字段值保留但不解析。
- **导出会话** —— 一期不做。
- **全量行为审计** —— 仅覆盖敏感操作。
- **第三方站点嵌入**(跨域 cookie、frame-ancestors 白名单、SSE 跨域) —— 一期仅门户内同源。
- **RAGFlow 前端源码修改** —— 一期通过 URL 参数注入,无需改前端;若未来需 postMessage 注入再评估。
- **审计日志自动清理** —— 一期永久保留。
- **门户管理 UI 的浏览器自动化测试** —— 一期仅测 API。

## Further Notes

### 验证基础

- 所有阻塞级技术假设(H1-H4)已由真实 RAGFlow v0.26.0(commit 92c4b76)实例验证通过,H5-H9 结论性验证符合设计预期。
- 8 项原型验收点全部通过,详见 `prototype/NOTES.md`。
- 10 项设计决策(D1-D10)已由 `grill-with-docs` 访谈确认,详见 `prototype/ragflow-portal-decisions.md`。

### 关键风险已闭环

- **最高风险 H1(令牌注入):** RAGFlow 前端原生支持 URL `?auth=`,无需改源码,架构成立。
- **首次对话双步行为:** 已识别并设计预创建 session 方案。
- **RAGFlow 仅租户级隔离:** 已确认由网关 `chat_session_owner` 实现用户级隔离,符合交接设计。

### 后续工作流

```text
PRD(本文档) -> /to-issues -> 按 issue /implement
```

### 环境信息(供实施参考,不含敏感凭据)

- RAGFlow v0.26.0 部署于内部服务器,80 端口对外,容器化运行。
- 门户与网关同源部署,一期单租户,网关持单一 RAGFlow `beta` Token。
- MySQL、Redis、MinIO、ES 由 RAGFlow 容器栈提供,门户可复用或独立部署(实施时决定)。
