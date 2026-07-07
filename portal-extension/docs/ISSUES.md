# RAGFlow 权限门户改造 — Issue 列表

> 由 `to-issues` 从 PRD 拆分,16 个 vertical slices(tracer bullets),每个端到端可验证。
> 本地无 issue tracker,以本地文件记录;迁移至正式 tracker 时每节对应一个 issue,标 `ready-for-agent`。
> Phase 1(Slice 1-7)已完成并部署;Phase 2(Slice 8-16)为待办,按依赖顺序实施。

## 依赖图

```
Phase 1(已完成,Issue 1-7):
Slice 1 (骨架/Tracer Bullet 1)
  ├─> Slice 2 (session 捕获/恢复)
  │     ├─> Slice 3 (继续/隔离/撤销)
  │     └─> Slice 5 (重命名/删除/双删) <─ Slice 3 完成
  └─> Slice 4 (CRUD) ──> Slice 6 (管理员/审计) <─ Slice 5 完成
                                              Slice 4 完成
Slice 7 (测试固化) <─ Slice 1-6 全部

Phase 2(待办,Issue 8-16):
Slice 8 (DB 持久化)
  ├─> Slice 12 (运维增强:重试+message_count)
  ├─> Slice 13 (多租户扩展)
  └─> Slice 15 (公开分享)
Slice 9 (前端登录+iframe 骨架)
  ├─> Slice 10 (前端我的会话)
  ├─> Slice 11 (前端管理后台)
  └─> Slice 16 (悬浮/Agent)
Slice 14 (OAuth/SSO) — 独立,无阻塞
```

---

## Issue 1 — Slice 1: 最小可登录的分享页访问(Tracer Bullet 1)

### Parent

无(起点)。关联 PRD:`/PRD.md`。

### What to build

构建门户与网关的最小骨架,打通「用户登录 → 网关签发短期嵌入令牌 → iframe URL 注入令牌 → RAGFlow 原生 bot_api 对话」端到端链路。本 slice 用硬编码数据快速验证核心架构,**RAGFlow 侧无任何修改**。

具体行为:
- 门户硬编码单个 admin 用户(用户名/密码哈希),提供登录端点,登录成功后建立同源会话。
- 硬编码一个分享页(关联某个 RAGFlow Chat dialog_id)与一条 grant(admin 用户对该分享页有 use 权限)。
- 网关持有一个 RAGFlow `beta` Token(对应 `api_token.beta` 列,非 `token` 列)。
- 用户登录后请求分享页时,网关签发短期 `T_short`(随机字符串,内存存储,5 分钟过期),构造 iframe URL,把 `T_short` 放在 URL 的 `auth` 参数(RAGFlow 前端 `getAuthorization()` 原生优先读 `?auth=`,回退才读 localStorage)。
- iframe 内的 SSE 请求经网关代理:网关用 `T_short` 校验有效性,用 `beta` Token 调 RAGFlow `/api/v1/chatbots/<dialog_id>/completions`,流式响应回传 iframe。
- iframe URL 与浏览器 localStorage 中**不含**真实 `beta` Token。

数据模型最小子集(仅本 slice 必需字段):
```
portal_user(id, username, password_hash, is_admin=true, enabled=true)
share_page(id, name, ragflow_type='chat', ragflow_resource_id=<dialog_id>, embed_type='fullscreen', enabled=true)
share_page_grant(share_page_id, subject_type='user', subject_id, permission='use')
```
令牌表(内存或临时表):`T_short(token, portal_user_id, share_page_id, expires_at, revoked=false)`。

### Acceptance criteria

- [ ] 用户能用硬编码 admin 账号登录门户,获得同源会话。
- [ ] 登录后请求分享页,网关返回的 iframe URL 含 `auth=T_short` 参数,**不含**真实 `beta` Token。
- [ ] iframe 加载后能正常发起对话,RAGFlow 原生引用片段与标记可见。
- [ ] 无效/过期的 `T_short` 调网关 → 401。
- [ ] 未登录用户请求分享页 → 403。
- [ ] 网关用 `beta` Token 调 RAGFlow `bot_api` 成功,SSE 流式响应正常回传。
- [ ] 真实 `beta` Token 全程不出现在任何浏览器可访问的位置(URL、localStorage、响应体)。

### Blocked by

无 — 可立即开始。

---

## Issue 2 — Slice 2: session_id 捕获与归属绑定 + 历史恢复

### Parent

关联 PRD:`/PRD.md`。承接 Slice 1 的骨架。

### What to build

让用户能管理与恢复自己的历史会话。核心是「预创建 session + 归属绑定 + 历史读取」端到端链路。

具体行为:
- 门户建 `chat_session_owner(session_id, share_page_id, portal_user_id NOT NULL, ragflow_resource_id, title, created_at, last_active_at)` 表。
- 用户打开分享页时,门户**预创建 session**:调 RAGFlow 创建空 `API4Conversation`,拿到 `session_id`,立即写入 `chat_session_owner` 绑定到当前用户,把 `session_id` 注入 iframe URL(与 `auth` 一并通过 URL 参数)。
  - 预创建方案解决 RAGFlow `async_iframe_completion` 无 session_id 时只返回 prologue 不处理 question 的双步行为(原型关键发现)。
- 用户首次提问时 iframe 已带 `session_id`,RAGFlow 正常处理 question 与流式响应;网关代理 SSE 时捕获 `session_id` 并更新 `chat_session_owner.last_active_at`。
- RAGFlow 加端点 `GET /api/v1/chatbots/<dialog_id>/sessions/<session_id>`(复用现有 `API4ConversationService.get_by_id`,无新逻辑),返回会话消息数组 + reference(chunks[] + doc_aggs[])。
- 门户「我的会话」列表端点:按 `portal_user_id` 查 `chat_session_owner`,返回 session 列表(标题、最后活跃时间)。
- 重新打开:门户用 `session_id` 调网关,网关调 RAGFlow GET 端点取回消息 + 引用,返回给前端恢复渲染。

RAGFlow 扩展端点强制按 `dialog_id + session_id + 调用方(网关)` 校验;网关侧再按 `chat_session_owner.portal_user_id` 做用户级隔离。

### Acceptance criteria

- [ ] 用户打开分享页时,门户预创建 session 并在 `chat_session_owner` 绑定到当前用户(可在 DB 验证记录存在)。
- [ ] iframe URL 含 `session_id` 参数,首次提问直接处理 question(无双步 prologue)。
- [ ] 对话后 `chat_session_owner.last_active_at` 被更新。
- [ ] 用户能在「我的会话」看到该 session(标题、最后活跃时间)。
- [ ] 点击历史 session 重新打开,能完整恢复消息正文、引用片段(chunks)、引用标记、文档定位(doc_aggs 的 document_id 可定位 PDF 预览)。
- [ ] 重新打开后引用标记可点击、引用片段弹层正常、PDF 预览可加载(通过 API 层断言引用数据完整;浏览器渲染不测)。
- [ ] RAGFlow GET 端点复用 `get_by_id`,无新业务逻辑(仅路由 + 网关鉴权)。

### Blocked by

- Issue 1(Slice 1 骨架)

---

## Issue 3 — Slice 3: 继续对话流式 + 用户隔离 + 撤销立即失效

### Parent

关联 PRD:`/PRD.md`。承接 Slice 2 的 session 绑定。

### What to build

验证安全核心:旧 session 继续流式、用户间隔离、撤销授权立即失效。

具体行为:
- 网关支持在已绑定的旧 `session_id` 上继续提问:代理 SSE 到 RAGFlow `bot_api`,请求体带 `session_id`,流式响应正常,新消息追加到同一会话(新 message_id,session_id 不变)。
- 网关每次请求校验链(任一失败 → 403):
  1. 门户登录态有效(同源 cookie)。
  2. `share_page_grant` 存在(用户或其所属组对该分享页有 use 权限)。
  3. 请求的 `session_id` 在 `chat_session_owner` 中归属当前用户。
  4. `session_id` 对应的 `dialog_id` 与 `share_page.ragflow_resource_id` 一致。
- 门户加第二个用户(硬编码或 CRUD)与撤销授权 API(删除 `share_page_grant` 行)。
- 网关撤销机制:删 grant + 吊销已签发的 `T_short`(内存令牌表标记 `revoked=true`);后续同 `T_short` 请求 → 403。
- RAGFlow 侧 `beta` Token 是租户级的无法按分享页撤销,撤销完全由网关实现。

### Acceptance criteria

- [ ] 用户 A 在旧 `session_id` 上继续提问,SSE 流式正常,新 message_id 生成,session_id 保持不变。
- [ ] 用户 B 用 A 的 `session_id` 调网关 → 403(归属校验失败)。
- [ ] 用户 B 持自己的 `T_short` 调 RAGFlow 直接用 A 的 session_id → 403(网关代理路径拒绝)。
- [ ] 管理员撤销用户 A 对某分享页的授权后:
  - 已签发给 A 的 `T_short` 立即失效(同令牌请求 → 403)。
  - A 已打开的 iframe 继续提问 → 403(网关校验 grant 不存在)。
  - A 刷新 iframe 加载 → 403(网关拒绝签发新 `T_short`)。
- [ ] 撤销授权后 A 的历史会话在 `chat_session_owner` 中保留(管理员仍可查)。
- [ ] 网关校验链四步全部生效(可分别构造失败场景验证)。

### Blocked by

- Issue 2(Slice 2 session 绑定)

---

## Issue 4 — Slice 4: 用户/用户组/分享页/ACL CRUD

### Parent

关联 PRD:`/PRD.md`。替换 Slice 1 的硬编码数据。

### What to build

把硬编码数据替换为完整 CRUD,实现用户、用户组、分享页、授权的管理。

具体行为:
- 门户 CRUD(均要求 `is_admin=true`):
  - 用户:创建(用户名、邮箱、初始密码)、查询、启用/禁用、(硬删除延后到 Slice 5)。
  - 用户组:创建、查询、添加成员、移除成员。
  - 分享页:创建(关联 RAGFlow dialog_id)、查询、启用/禁用;`embed_type`/`ragflow_type` 一期固定值不开放选择器。
  - 授权:把分享页授权给用户或用户组(subject_type=user/group)、撤销授权。
- ACL 解析:网关校验 grant 时支持 `subject_type=user` 与 `subject_type=group`(用户 → 所属组 → 组的 grant,一次 SQL join)。
- 普通用户:`is_admin=false`,只能查询自己被授权的分享页与自己的会话;无任何管理能力。
- 禁用用户(`enabled=false`):无法登录,但会话保留(管理员可查);网关拒绝其请求。
- 登录失败明确错误(邮箱未注册 / 密码错误 / 账号已禁用)。

数据模型补全(相对 Slice 1):
```
portal_user 增加字段:email, is_admin, enabled, created_at(补齐)
portal_group(id, name, created_at)
portal_group_member(group_id, user_id, added_at)
share_page_grant.subject_type ∈ {user, group}(字段已预留,本 slice 启用 group)
```

### Acceptance criteria

- [ ] 管理员能创建用户、用户组、分享页,并能把分享页授权给用户或用户组。
- [ ] 管理员能把用户加入/移出用户组。
- [ ] 普通用户登录后只看到被授权的分享页(直接授权或所属组授权)。
- [ ] 用户组成员自动继承组对分享页的授权。
- [ ] 普通用户调用任何管理 API → 403。
- [ ] 禁用用户无法登录(明确错误提示),但其历史会话保留。
- [ ] 登录失败有明确错误(邮箱未注册 / 密码错误 / 账号已禁用)。
- [ ] 网关 ACL 解析支持 user 与 group 两种 subject_type(可构造用户仅通过组授权的场景验证)。

### Blocked by

- Issue 1(Slice 1 骨架)

---

## Issue 5 — Slice 5: 会话重命名/删除 + 双删 + 用户硬删除级联

### Parent

关联 PRD:`/PRD.md`。承接 Slice 2 的 session 表与 Slice 3 的隔离。

### What to build

补全会话生命周期管理(重命名、删除)与用户硬删除的级联清理。

具体行为:
- RAGFlow 加端点(复用现有 service,无新逻辑):
  - `PATCH /api/v1/chatbots/<dialog_id>/sessions/<session_id>` —— 重命名(更新 `API4Conversation.name` 字段)。
  - `DELETE /api/v1/chatbots/<dialog_id>/sessions/<session_id>` —— 删除(调 `API4ConversationService.delete_by_id`)。
- 门户会话管理 API:
  - 重命名:用户重命名自己的会话(网关校验归属后调 RAGFlow PATCH)。
  - 删除:用户删除自己的会话(网关双删协调)。
- 双删事务策略:
  - 先调 RAGFlow DELETE 删 `API4Conversation`,成功后再删门户 `chat_session_owner`。
  - 若 RAGFlow 侧删除失败:门户侧不删,标记 `chat_session_owner.deleted_at` 待重试;后台清理任务定期重试。
  - 避免门户已删但 RAGFlow 残留的幽灵会话。
- 用户硬删除级联:管理员硬删除用户时,先级联双删该用户的所有 `chat_session_owner`(每条调 RAGFlow DELETE),再删 `portal_user`。
- 删除为硬删除,不设回收站。

数据模型补充:`chat_session_owner.deleted_at`(可空,待重试标记)。

### Acceptance criteria

- [ ] 用户能重命名自己的会话,RAGFlow 侧 `API4Conversation.name` 与门户 `chat_session_owner.title` 同步更新。
- [ ] 用户能删除自己的会话,门户 `chat_session_owner` 与 RAGFlow `API4Conversation` 同步删除(双删一致)。
- [ ] 删除后用户在「我的会话」看不到该 session,网关调 RAGFlow GET 端点也返回 404。
- [ ] RAGFlow DELETE 失败时,门户侧不删,`chat_session_owner.deleted_at` 被标记(可在 DB 验证)。
- [ ] 管理员硬删除用户后,该用户的所有 `chat_session_owner` 与对应 RAGFlow `API4Conversation` 全部清除(无孤儿)。
- [ ] 用户不能重命名/删除他人的会话(网关归属校验 → 403)。
- [ ] RAGFlow PATCH/DELETE 端点复用 `update_by_id`/`delete_by_id`,无新业务逻辑。

### Blocked by

- Issue 2(Slice 2 session 表)
- Issue 3(Slice 3 隔离校验)

---

## Issue 6 — Slice 6: 管理员后台 + 审计日志

### Parent

关联 PRD:`/PRD.md`。承接 Slice 4 的 CRUD 与 Slice 5 的会话管理。

### What to build

管理员会话搜索、分级查看正文(二次确认 + 审计)、审计日志记录。

具体行为:
- 管理员会话搜索 API:按用户、分享页、时间范围、关键词查询所有人的会话(返回元数据:标题、用户、时间、消息数)。
- 管理员查看会话正文(分级):
  - 默认只返回元数据,不含消息正文。
  - 查正文需显式参数(如 `?elevated=true`),网关写 `audit_log(action=session_view_elevated)` 后返回正文。
  - UI 层二次确认是「显式参数」的触发方式(一期 API 层落地,UI 二次确认可后续)。
- `audit_log(actor_user_id, action, target_type, target_id, at, meta_json)` 表,记录 8 类敏感操作:
  - `login_success | login_failure | grant_create | grant_revoke | session_delete | session_view_elevated | user_enable | user_disable`
- 审计日志永久保留,不自动清理。
- 普通用户的日常操作(列自己的会话、继续对话)不记审计。

### Acceptance criteria

- [ ] 管理员能按用户、分享页、时间范围、关键词搜索所有人的会话(返回元数据)。
- [ ] 管理员默认查会话只返回元数据(标题、用户、时间、消息数),不含消息正文。
- [ ] 管理员用 `elevated` 参数查看正文时,`audit_log` 写入一条 `session_view_elevated` 记录(含 actor、target session_id、时间)。
- [ ] 不带 `elevated` 参数时返回元数据且不写审计。
- [ ] 8 类敏感操作全部触发审计记录(登录成功/失败、授权创建/撤销、会话删除、查正文、用户启用/禁用)。
- [ ] 普通用户的操作(列会话、继续对话)不写审计日志。
- [ ] 审计日志表无自动清理机制(永久保留,可验证无 TTL/清理任务)。
- [ ] 普通用户调用管理员搜索/审计 API → 403。

### Blocked by

- Issue 4(Slice 4 CRUD)
- Issue 5(Slice 5 会话管理)

---

## Issue 7 — Slice 7: 端到端测试固化

### Parent

关联 PRD:`/PRD.md` 的 Testing Decisions。承接 Slice 1-6 全部。

### What to build

把原型 8 个验收点 + PRD 43 条 user stories 的关键路径固化为自动化端到端 HTTP 测试(唯一主 seam),覆盖双删事务失败、撤销后失效、用户隔离等分支。

具体行为:
- 端到端测试套件:HTTP 客户端模拟用户全程行为,断言 HTTP 响应 + 门户 DB 状态 + RAGFlow 侧状态(经网关 GET 端点验证,不直连 MySQL)。
- 测试用例映射 8 个原型验收点:
  1. 用户登录并获得分享页权限。
  2. iframe URL 不含真实 `beta` Token(断言 URL 含 `T_short` 不含 `beta`)。
  3. 新建会话后 `chat_session_owner` 有归属记录。
  4. 关闭后从「我的会话」重新打开(用 session_id 重载)。
  5. 完整恢复消息 + 引用 chunks + doc_aggs + 文档定位。
  6. 旧 session_id 继续流式(新 message_id + session_id 不变)。
  7. 用户 B 调 A 的 session_id → 403。
  8. 撤销授权后同令牌 → 403。
- 额外分支测试:
  - 双删事务失败(RAGFlow DELETE 失败 → `chat_session_owner.deleted_at` 标记)。
  - 禁用用户无法登录但会话保留。
  - 硬删除用户级联清会话(无孤儿)。
  - 管理员 elevated 查正文写审计。
  - 8 类敏感操作全部入审计。
- 不测:RAGFlow 前端 UI 渲染(浏览器 E2E)、门户管理 UI(一期无 UI 测试框架)。

### Acceptance criteria

- [ ] 8 个原型验收点全部有对应自动化测试用例且通过。
- [ ] 双删事务失败分支有测试用例(模拟 RAGFlow DELETE 失败,验证 `deleted_at` 标记)。
- [ ] 用户隔离测试:用户 B 调 A 的 session → 403。
- [ ] 撤销失效测试:撤销后同 `T_short` → 403。
- [ ] 硬删除用户级联测试:删除后无孤儿会话(门户 + RAGFlow 两侧皆清)。
- [ ] 审计日志测试:8 类敏感操作全部入审计。
- [ ] 测试套件可一条命令运行(如 `pytest e2e/` 或 `node e2e/`)。
- [ ] 测试不直连 RAGFlow MySQL(通过网关或 RAGFlow 扩展端点验证)。
- [ ] 测试套件在 CI 可跑(若一期无 CI,至少本地一条命令全绿)。

### Blocked by

- Issue 1(Slice 1)
- Issue 2(Slice 2)
- Issue 3(Slice 3)
- Issue 4(Slice 4)
- Issue 5(Slice 5)
- Issue 6(Slice 6)

---

## Issue 8 — Slice 8: DB 持久化迁移(内存存储 → MySQL)

### Parent

承接 Phase 1(Issue 1-7 已完成并部署)。关联 PRD 数据模型;HANDOFF.md 后续待办"必须② DB 化"。

### What to build

把当前内存存储(SeedData / SessionStore / AuditStore / TokenStore)迁移到 MySQL 持久化,解决进程重启数据丢失问题。7 张表 schema 已在 PRD 数据模型就绪:

```
portal_user(id, username, password_hash, email, is_admin, enabled, created_at)
portal_group(id, name, created_at)
portal_group_member(group_id, user_id, added_at)
share_page(id, name, ragflow_type, ragflow_resource_id, embed_type, enabled, created_at)
share_page_grant(share_page_id, subject_type, subject_id, permission)
chat_session_owner(session_id, share_page_id, portal_user_id, ragflow_resource_id,
                   title, created_at, last_active_at, deleted_at, message_count)
audit_log(id, actor_user_id, action, target_type, target_id, at, meta_json)
```

端到端行为:
- 建表迁移脚本(可复用 RAGFlow 现有 MySQL 实例,新建 `portal_*` 库或独立库)。
- 把 4 个内存存储类替换为 DB 访问层(保持现有 API 接口不变,routes/gateway 不改)。
- 启动时 seed 数据(admin/user2/默认分享页/grant)从 DB 读取,不存在则初始化写入。
- `T_short` 令牌表也持久化(或保留内存但接受重启失效,二选一由实现决定)。
- 现有 178 单元测试 + 5 integration 测试全部通过(测试用独立 DB 或事务回滚隔离)。

### Acceptance criteria

- [ ] 7 张表 schema 落地,迁移脚本可重复执行(idempotent)。
- [ ] 进程重启后用户/会话/授权/审计数据不丢失(验证:登录 → 重启 → 仍登录,会话仍在)。
- [ ] 现有 178 单元测试全部通过(测试隔离,不污染生产数据)。
- [ ] 现有 5 integration 测试在真实 MySQL + 真实 RAGFlow 下全部通过。
- [ ] `T_short` 行为明确(持久化或重启失效,文档说明)。
- [ ] ruff 干净,无新依赖引入(MySQL 驱动除外,如 sqlalchemy + pymysql 或 asyncmy)。

### Blocked by

无 — 可立即开始(Issue 1-7 已完成)。

---

## Issue 9 — Slice 9: 前端登录页 + 分享页 iframe 加载(前端 Tracer Bullet)

### Parent

关联 PRD user stories 1-6、31-33。HANDOFF.md 后续待办"建议③ 前端实现(骨架)"。

### What to build

构建门户前端最小骨架,打通「登录页 → 登录成功 → 跳转分享页 → iframe 加载 → 对话可见」端到端链路。后端 API 已就绪(Slice 1-7),本 slice 只做前端。

端到端行为:
- 登录页(`/login`):用户名/密码表单,调 POST `/login`,成功后跳转分享页列表。
- 分享页列表页:调 GET `/share-pages` 列出用户被授权的分享页,点击进入分享页详情。
- 分享页详情页:调 GET `/share-pages/<id>/embed-url` 拿 iframe URL,渲染 `<iframe>` 加载,iframe 内 RAGFlow 原生对话可用。
- iframe 同源加载,门户 cookie 自动携带,X-Frame-Options: SAMEORIGIN 生效。
- 登出端点 + 登出后回登录页。
- 一期可最小化样式(无设计稿,可用现有 RAGFlow 前端栈 React/UmiJS 或独立轻量栈如 Vite+React)。

### Acceptance criteria

- [ ] 用户能用浏览器打开登录页,输入 admin 凭据登录成功,跳转分享页列表。
- [ ] 列表页显示用户被授权的分享页(至少 sp_default)。
- [ ] 点击分享页进入详情页,iframe 加载 RAGFlow 对话界面,能输入问题并收到流式回答。
- [ ] iframe URL 不含真实 beta Token(浏览器 DevTools 可验证)。
- [ ] 未登录用户直接访问分享页详情页 → 跳转登录页。
- [ ] 登出后回登录页,再访问分享页 → 跳转登录页。
- [ ] 浏览器 DevTools 看到 X-Frame-Options: SAMEORIGIN 响应头。

### Blocked by

无 — 后端 API 已就绪,前端可独立开发。

---

## Issue 10 — Slice 10: 前端我的会话列表 + 重新打开

### Parent

关联 PRD user stories 22-25、29-30。承接 Slice 9 前端骨架。HANDOFF.md 后续待办"建议③ 前端实现(会话)"。

### What to build

让用户在前端管理自己的历史会话:列表、重新打开、继续对话、重命名、删除。

端到端行为:
- 分享页详情页加「我的会话」侧栏或 tab:调 GET `/share-pages/<id>/sessions` 列出当前用户在该分享页下的会话(标题、最后活跃时间、消息数)。
- 点击会话 → 调 GET `/share-pages/<id>/sessions/<sid>` 取回历史 → iframe 带 session_id 加载 → RAGFlow 恢复历史消息与引用。
- 会话操作:重命名(调 PATCH)、删除(调 DELETE),删除后列表实时更新。
- 新建会话按钮:调 POST `/share-pages/<id>/sessions` 预创建 → iframe 加载新 session_id。
- 用户只能看到/操作自己的会话(后端已隔离,前端不需额外校验)。

### Acceptance criteria

- [ ] 用户在分享页详情页能看到「我的会话」列表(标题、时间、消息数)。
- [ ] 点击历史会话能重新打开,iframe 内恢复历史消息正文 + 引用片段 + 引用标记。
- [ ] 重新打开后能继续提问,流式回答正常,消息追加到同一会话。
- [ ] 用户能重命名会话,列表标题实时更新。
- [ ] 用户能删除会话,列表实时移除,再次刷新不出现。
- [ ] 新建会话按钮能预创建并加载空会话。
- [ ] 用户看不到他人会话(后端隔离生效,前端列表只有自己的)。

### Blocked by

- Issue 9(Slice 9 前端骨架)

---

## Issue 11 — Slice 11: 前端管理后台

### Parent

关联 PRD user stories 4-11、12-21、34-43(管理员 CRUD + 会话搜索 + 审计)。承接 Slice 9 前端骨架。HANDOFF.md 后续待办"建议③ 前端实现(后台)"。

### What to build

管理员后台前端,覆盖用户/组/分享页/授权 CRUD + 会话搜索 + 审计日志查看。

端到端行为:
- 管理后台入口(仅 is_admin 用户可见):用户管理、用户组管理、分享页管理、授权管理、会话搜索、审计日志六个 tab。
- 用户管理:列表、创建(用户名/邮箱/初始密码)、启用/禁用、硬删除(确认弹窗提示级联清会话)。
- 用户组管理:列表、创建、添加/移除成员。
- 分享页管理:列表、创建(关联 RAGFlow dialog_id)、启用/禁用。
- 授权管理:选择分享页 → 列出 grant → 授权给用户/组、撤销授权。
- 会话搜索:按用户/分享页/时间/关键词筛选 → 列表(元数据);点击「查看正文」二次确认 → 调 elevated 端点 → 弹窗显示消息正文。
- 审计日志:按时间/action/actor 筛选 → 列表(8 类敏感操作)。
- 普通用户访问管理后台 → 403 提示或跳转。

### Acceptance criteria

- [ ] 管理员能看到管理后台入口,普通用户看不到且直接访问 URL → 403。
- [ ] 管理员能创建/禁用/启用/硬删除用户,硬删除时有确认提示。
- [ ] 管理员能创建用户组、添加/移除成员。
- [ ] 管理员能创建分享页(填 dialog_id)、启用/禁用。
- [ ] 管理员能把分享页授权给用户或组,能撤销授权。
- [ ] 管理员能按多维度搜索会话,默认只看元数据,查正文需二次确认且写审计。
- [ ] 管理员能查看审计日志,按 action/时间筛选。
- [ ] 8 类敏感操作在 UI 上都有触发入口且能在审计日志看到记录。

### Blocked by

- Issue 9(Slice 9 前端骨架)

---

## Issue 12 — Slice 12: 双删重试定时任务 + message_count SSE 实时更新

> **状态:✅ 已完成(slice12-ops-implementation 分支)**
> - 重试任务:`portal/tasks.py` 的 `retry_delete_pending_sessions` + `_retry_delete_loop`,
>   `main.py` 的 startup/shutdown hook(`start_retry_delete_task` / `stop_retry_delete_task`)。
>   选型 `asyncio.create_task` + `asyncio.sleep`(无新依赖,生命周期清晰)。
> - SSE message_count:`gateway.py` 的 `_sync_message_count_after_sse`,流成功后调 GET history
>   取最新 messages 数,与 `last_active_at` 同时机更新(与 `resume_session` 同逻辑)。
> - 配置:`RETRY_DELETE_INTERVAL_SECONDS`(默认 300s,<=0 禁用,管理员仍可手动触发 retry-delete)。
> - 测试:新增 16 个(11 retry task + 5 SSE message_count),全套 223 passed, 5 skipped。

### Parent

HANDOFF.md 后续待办"建议④ 后台重试任务"与"建议⑤ SSE 代理 message_count 更新"。

### What to build

补两个运维增强:(1) 双删失败的后台重试任务;(2) SSE 代理时实时更新 message_count。

端到端行为:
- **重试任务**:Slice 5 标记 `deleted_at` 的会话由后台定时任务重试调 RAGFlow DELETE,成功后硬删门户侧记录。提供两种触发方式:定时任务(如每 5 分钟) + 管理员手动触发端点(Slice 6 已加 retry-delete 端点,本 slice 补定时调度)。
- **message_count 更新**:当前 message_count 仅在恢复会话(GET history)时更新,SSE 代理后不更新导致滞后。本 slice 解析 SSE 流,在流成功完成后按新消息数更新 message_count(与 last_active_at 同一时机更新)。
- 定时任务实现可选:APScheduler / 独立 worker / cron 调 API,由实现决定,需文档说明。

### Acceptance criteria

- [ ] 双删失败的会话(`deleted_at` 非空)被定时任务重试,RAGFlow 成功后门户侧硬删除(无残留)。
- [ ] 管理员手动触发 retry-delete 端点仍可用(Slice 6 已实现,本 slice 不破坏)。
- [ ] 定时任务可配置间隔,默认 5 分钟,文档说明配置方式。
- [ ] SSE 代理成功后 message_count 按新消息数更新(不再仅靠 GET history 更新)。
- [ ] message_count 更新与 last_active_at 更新在同一时机(流成功完成后)。
- [ ] 失败流(上游不可达)不更新 message_count(与 last_active_at 一致)。
- [ ] 现有测试全部通过,新增重试任务与 message_count 更新的测试用例。

### Blocked by

- Issue 8(Slice 8 DB 持久化 — 重试任务需查询 `deleted_at` 字段)

---

## Issue 13 — Slice 13: 多租户扩展(org_id)

### Parent

PRD D2 决定一期单租户不预留 org_id。HANDOFF.md 后续待办"可选⑥ 多租户扩展"。

### What to build

在现有单租户架构上加 org_id 维度,实现多租户隔离。

端到端行为:
- `portal_user` / `portal_group` / `share_page` / `chat_session_owner` / `audit_log` 加 `org_id` 字段。
- `portal_user` 加 `org_id` + 角色(`org_admin` 介于普通用户与平台管理员之间)。
- 网关校验链加 org_id 隔离:用户只能访问同 org 的分享页与会话。
- 平台管理员跨 org 管理所有数据;org_admin 只管本 org。
- 迁移脚本:现有数据归入默认 org(org_id='default')。
- 配置 RAGFlow 多 dialog_id 时按 org 区分(每个 org 关联不同 dialog)。

### Acceptance criteria

- [ ] 所有 7 张表加 org_id 字段,迁移脚本把现有数据归入默认 org。
- [ ] 用户只能看到同 org 的分享页与会话(跨 org 访问 → 403)。
- [ ] org_admin 能管理本 org 用户/组/分享页/授权,不能跨 org。
- [ ] 平台管理员能跨 org 管理,能看到 org 维度列表。
- [ ] 审计日志含 org_id 维度,可按 org 筛选。
- [ ] 现有测试适配 org_id 后全部通过。

### Blocked by

- Issue 8(Slice 8 DB 持久化 — 加字段需 DB 化完成)

---

## Issue 14 — Slice 14: OAuth/SSO 登录

### Parent

PRD D1 决定一期自建账号。HANDOFF.md 后续待办"可选⑦ OAuth/SSO"。

### What to build

在自建账号体系上叠加 OAuth/SSO 登录(OIDC / LDAP / 飞书 SSO 三选一或多个)。

端到端行为:
- 门户登录页加「SSO 登录」按钮(可选多个 provider)。
- 用户点 SSO → 跳转 IdP → 回调 → 门户用 IdP 返回的唯一标识匹配本地用户(不存在则自动创建,需配置策略)。
- SSO 用户与自建账号用户共用 `portal_user` 表,加 `sso_provider` + `sso_external_id` 字段。
- 管理员可配置 SSO provider(OIDC issuer / LDAP server / 飞书 app_id)。
- SSO 登录成功也写 `login_success` 审计日志。

### Acceptance criteria

- [ ] 至少实现一种 SSO(OIDC 或 LDAP 或飞书)端到端可登录。
- [ ] SSO 用户首次登录自动创建本地用户记录(可配置是否允许自动创建)。
- [ ] SSO 用户与自建账号用户权限模型一致(授权/会话/审计无差异)。
- [ ] SSO 登录成功写 `login_success` 审计日志(meta 含 provider)。
- [ ] 管理员能配置 SSO provider 参数。
- [ ] SSO 失败(IdP 不可达 / 用户不存在且不允许自动创建)有明确错误提示。

### Blocked by

无 — 可与 Slice 8 并行(但 SSO 用户字段需 DB 持久化,建议 Slice 8 之后)。

---

## Issue 15 — Slice 15: 公开分享(is_public + 限流)

### Parent

PRD D3 决定一期仅登录用户。HANDOFF.md 后续待办"可选⑧ 公开分享"。

### What to build

允许分享页标记为公开,免登录访问,加限流防滥用。

端到端行为:
- `share_page` 加 `is_public` 字段(默认 false)。
- 公开分享页有独立 URL(如 `/public/<share_page_id>`),无需登录直接加载 iframe。
- 公开分享页的 T_short 签发不校验 grant,但仍走网关代理(限流 + 审计)。
- 限流:按 IP 限流(如每 IP 每分钟 10 次对话),超限 → 429。
- 公开分享页的会话归属「匿名用户」(session_id 不绑定 portal_user_id,或绑定到特殊 anonymous 用户)。
- 管理员可切换分享页 is_public,关闭后公开 URL 立即失效。

### Acceptance criteria

- [ ] 管理员能把分享页标记为 is_public=true,公开 URL 可免登录访问。
- [ ] 公开分享页能正常对话(iframe 加载 + SSE 代理工作)。
- [ ] 公开分享页按 IP 限流,超限返回 429。
- [ ] 管理员关闭 is_public 后,公开 URL 立即返回 403。
- [ ] 公开会话不绑定到具体 portal_user(匿名或特殊用户)。
- [ ] 公开分享页的会话不进入普通用户的「我的会话」列表。
- [ ] 公开访问也写审计(可选,由实现决定)。

### Blocked by

- Issue 8(Slice 8 DB 持久化 — `is_public` 字段需 DB 化)

---

## Issue 16 — Slice 16: 悬浮组件 / Agent 支持

### Parent

PRD D9 决定一期仅全屏 Chat,字段已预留(`embed_type` / `ragflow_type`)。HANDOFF.md 后续待办"可选⑨ 悬浮组件/Agent"。

### What to build

扩展 `embed_type` 支持 widget(悬浮组件) + `ragflow_type` 支持 agent(RAGFlow Agent iframe)。

端到端行为:
- `embed_type=widget`:门户返回悬浮组件 JS snippet(而非全屏 iframe),嵌入到任意页面右下角,点击展开对话窗。
- `ragflow_type=agent`:网关构造 RAGFlow Agent iframe URL(`/agent/share?...`),SSE 代理走 RAGFlow agentbot 端点(已有 `/agentbots/<id>/completions`)。
- 网关校验链对 agent 类型同样生效(grant + T_short + 归属)。
- 管理员创建分享页时可选 embed_type 与 ragflow_type(一期固定值改为可选)。
- 悬浮组件与 Agent 的会话也进 `chat_session_owner`,用户可管理。

### Acceptance criteria

- [ ] 管理员能创建 `embed_type=widget` 的分享页,前端生成可嵌入的 JS snippet。
- [ ] 悬浮组件在任意页面右下角加载,点击展开对话窗,能正常对话。
- [ ] 管理员能创建 `ragflow_type=agent` 的分享页,iframe 加载 RAGFlow Agent 界面。
- [ ] Agent 分享页的 SSE 代理走 agentbot 端点,流式响应正常。
- [ ] 网关校验链对 widget 与 agent 类型都生效。
- [ ] widget 与 agent 的会话进入「我的会话」列表,可重命名/删除/重新打开。
- [ ] X-Frame-Options 策略对 widget 场景适配(跨域嵌入需调整 CSP/frame-ancestors)。

### Blocked by

- Issue 9(Slice 9 前端骨架 — widget 需前端组件)

---

## Phase 3 — 同源部署修复(2026-07-06 真实环境验证发现)

> Phase 1(Issue 1-7)与 Phase 2(Issue 8-16)已实现。真实环境(172.16.10.180)部署采用「portal 前端 `/portal/` 子路径 + RAGFlow 占 :80 + nginx 反代」同源架构后,发现 3 个互相耦合的运行时 bug:
>
> - **B1**:portal iframe 内 RAGFlow 分享页仍跳 RAGFlow 登录页(根因:分享页挂载调 `GET /api/v1/chatbots/{id}/info` 要求 AUTH_BETA,网关只代理了 `/completions`,T_short 直达 RAGFlow 被拒 → 401 → 前端 `redirectToLogin()`)。
> - **B2**:登录 RAGFlow 后刷新 `/portal/`,portal 登出(根因:同源下两端都用默认 cookie 名 `session`,RAGFlow 登录覆盖 portal session cookie,签名互不兼容)。
> - **B3**:直接访问 RAGFlow 原生分享页 `http://172.16.10.180/chats/share?shared_id=xxx` 发消息 403「未登录」(根因:nginx 把所有 `/api/v1/.../completions` 路由到 portal,portal 要求 portal 会话,原生分享页用户无 portal 会话)。
>
> 关键认知:PRD L120-128 原始令牌注入设计只覆盖了 `/completions` 代理,**漏掉了分享页挂载时的 `/info` 端点**——这是 B1 的设计层根因;B3 是子路径部署引入的回归。Slice 17 修 B2(独立),Slice 18 统一修 B1+B3(网关成为分享页 API 唯一入口,按 T_short 决定换 Token 或透传)。

## Issue 17 — Slice 17: Portal 会话 cookie 隔离(修复 B2 同源 cookie 踩踏)

### Parent

无(Phase 3 起点)。关联 PRD:`/PRD.md` L21(同源架构)。

### What to build

给 portal `SessionMiddleware` 传独立 cookie 名(如 `portal_session`),与 RAGFlow 的 `session` cookie 不再互相覆盖。

当前 [portal/main.py:89](file:///Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/portal/main.py#L89) `app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)` 未传 `session_cookie` 参数,Starlette 默认 `session_cookie="session"`;RAGFlow(`api/apps/__init__.py`)同样未配 `SESSION_COOKIE_NAME`,Quart/Flask 默认也是 `session`。两端同源(`172.16.10.180:80`)下共享 cookie jar,RAGFlow 登录写入的 `session` 覆盖 portal 的 `session`,portal 用自身 `secret_key` 解签名失败 → 会话丢失 → `get_current_user` 抛 403 → 前端显示登出。

行为:portal 的会话 cookie 改名为 `portal_session`,RAGFlow 继续用 `session`,两者互不干扰。

### Acceptance criteria

- [ ] `portal/main.py` `SessionMiddleware` 显式传 `session_cookie="portal_session"`(或配置项)
- [ ] 在 `http://172.16.10.180/` 登录 RAGFlow 后,刷新 `/portal/` 仍保持 portal 登录态
- [ ] 在 `/portal/` 登录 portal 后,刷新 `/` RAGFlow 登录态不受影响
- [ ] 单测覆盖:portal 会话 cookie 名为 `portal_session`(非默认 `session`)
- [ ] 既有 350 个 pytest 全绿(无回归)

### Blocked by

None - can start immediately

---

## Issue 18 — Slice 18: 网关统一代理分享页 API + nginx 路由分流(修复 B1 + B3)

### Parent

无(Phase 3)。关联 PRD:`/PRD.md` L120-128(令牌注入机制)、L131-133(beta Token 选用)。

### What to build

网关成为 RAGFlow 分享页 API 的**唯一入口**:nginx 把所有 `/api/v1/chatbots/*` 与 `/api/v1/agentbots/*` 路由到 portal;portal 对每个请求按 Authorization 是否含有效 `T_short` 决定行为——有则校验(grant + 归属)+ 换真实 beta Token 转发 RAGFlow,无则**原样透传** RAGFlow(让 RAGFlow 用自身 auth/session 处理)。这统一修复 B1(`/info` 端点补上)与 B3(原生分享页无 T_short 时透传,不再 403)。

端到端行为:
- **portal iframe 路径**:分享页挂载调 `GET /api/v1/chatbots/{id}/info`,Authorization 带 `Bearer <T_short>` → 网关校验 T_short + grant + 归属 → 换 beta Token 调 RAGFlow `/info` → 200 返回 iframe。`POST .../completions` 同理(已有 SSE 代理,纳入统一分发)。
- **RAGFlow 原生分享页路径**:用户直接访问 `/chats/share?shared_id=xxx`(不经 portal),分享页挂载调 `GET /info`,Authorization 无 T_short(或带 RAGFlow 自身 beta Token)→ 网关**透传** RAGFlow → RAGFlow 用自身 auth 处理 → 200。`POST .../completions` 同理透传。
- 网关对 `agent` 类型同样处理(`/api/v1/agentbots/{id}/inputs` + `/completions`;RAGFlow agentbot_api 无 `/info` 端点,故不代理)。

实现要点(决策性部分,非逐行规格):
- 网关新增 `GET /api/v1/chatbots/{id}/info` 与 `GET /api/v1/agentbots/{id}/inputs` 代理端点,校验链沿用现有 SSE 代理(cookie + grant + T_short validate → 换 beta Token → 转发 RAGFlow,JSON 响应原样回传)。
- 网关对所有 `/api/v1/chatbots/*` 与 `/api/v1/agentbots/*` 请求增加透传分支:**无 T_short,或 T_short 不在 TokenStore(非 portal 签发,可能是 RAGFlow 自身 beta Token)→ 原样转发 RAGFlow**(保留原始 Authorization / Cookie)。注意区分:T_short 在 TokenStore 但已 revoked/expired → 401(非透传,令牌曾有效但失效)。
- nginx 配置:新增通用规则 `location ~ ^/api/v1/(chatbots|agentbots)/` `proxy_pass http://<portal>:8000;`,**保留**原 `~ ^/api/v1/(chatbots|agentbots)/[^/]+/completions$` 专用规则(SSE 需 `proxy_buffering off`);portal 成为这些路径的唯一入口,由 portal 决定换 Token 或透传。
- SSE 端点(`.../completions`)保留 `proxy_buffering off` 等流式配置;非 SSE 端点(`/info`、`/inputs`)走普通代理。

> 注:PRD L120-128 原设计假定「SSE 请求被代理就够了」,忽略了分享页挂载时的 `/info` 等辅助端点。本 slice 补齐设计盲点,并把网关升级为分享页 API 统一入口(按 T_short 决定换 Token 或透传),同时消除子路径部署引入的 B3 回归。

### Acceptance criteria

- [ ] portal 嵌入的 iframe 加载 RAGFlow 分享页,显示对话 UI(不再跳 RAGFlow 登录页)
- [ ] iframe 内 `GET /api/v1/chatbots/{id}/info` 返回 200(经网关换 beta Token)
- [ ] iframe 内发消息,`POST .../completions` SSE 流式回复正常
- [ ] **直接**访问 RAGFlow 原生分享页 `http://172.16.10.180/chats/share?shared_id=xxx`(不经 portal,无 portal 会话),发消息返回 200 不再 403
- [ ] 原生分享页的 `GET /info`(无 T_short)透传 RAGFlow,返回 200
- [ ] `ragflow_type=agent` 的分享页同样工作(`/agentbots/{id}/inputs` + `/completions`;RAGFlow agentbot 无 `/info`)
- [ ] 单测覆盖:网关对「有 T_short」与「无 T_short」两条分支的分发逻辑
- [ ] 既有 pytest 全绿(无回归;基线 350,Slice 17/18 新增 16 → 366 passed + 5 skipped)

### Blocked by

- Issue 17(Slice 17 cookie 隔离 — 避免 E2E 验证时 B2 干扰会话状态)

---

## Issue 19 — Slice 19: 区分过期 portal T_short 与原生 beta Token(修复浏览器 109 + x.find 崩溃)

### Parent

Issue 18(Slice 18 网关透传分支上线后,浏览器 E2E 暴露的回归)。

### What to build

Slice 18 的透传分支无法区分两种「token 不在 TokenStore」的场景:
1. **原生 beta Token**(RAGFlow 自身签发,原生分享页用户带此 token,无 portal 会话)— 应**透传** RAGFlow;
2. **过期的 portal T_short**(portal 重启后 TokenStore 清空,iframe URL 里残留的旧 T_short)— 应**拒绝**(401),触发 iframe 重新向 portal 申请 T_short。

当前两者都被透传给 RAGFlow,但过期 T_short 不是合法 beta Token,RAGFlow AUTH_BETA 拒绝 → 浏览器看到 `109 No authorization`,且 `/info` 或 `/completions` 返回错误体后下游组件 `MarkdownContent` 调 `reference.doc_aggs.find(...)` 拿到 undefined → `TypeError: x.find is not a function` → 分享页显示「Something went wrong」。

**修复方向**:给 portal 签发的 T_short 加可识别前缀 `pt_`(`secrets.token_urlsafe` 结果前加 `pt_`)。网关校验链改为:
- Authorization token 以 `pt_` 开头但不在 TokenStore → **401**(过期/吊销的 portal T_short,触发重新登录);
- Authorization token 以 `pt_` 开头且在 TokenStore 但 revoked/expired → **401**(Slice 18 既有逻辑,保持);
- Authorization token 不以 `pt_` 开头(原生 beta Token 或其它)→ **透传** RAGFlow(Slice 18 既有逻辑,保持);
- 无 Authorization header → **透传** RAGFlow(Slice 18 既有逻辑,保持)。

副作用:正在使用的 iframe URL(含旧格式 T_short,无 `pt_` 前缀)会失效 — 用户重新登录 portal 获取新 embed-url 即可(T_short 本就是短命令牌,5 分钟过期,可接受)。

同时确认 `proxy_bot_json_to_ragflow` 与 `_build_sse_passthrough_response` 的响应体**逐字节透传** RAGFlow(`Response(content=resp.content)` + 原 content-type),无 FastAPI 中间件改写 — 确保 `/info` 返回的 `doc_aggs` 数组形状与直连 RAGFlow 一致,`x.find` 不再崩溃。

### Acceptance criteria

- [ ] `TokenStore.issue` 签发的 T_short 以 `pt_` 前缀开头;既有测试断言更新(token 格式变化)
- [ ] 网关 SSE 与 JSON 代理的透传分支:`pt_` 前缀但不在 TokenStore → 401(非透传);无 `pt_` 前缀 → 透传(保持)
- [ ] portal 重启后,带新格式 T_short(`pt_` 前缀)的 iframe 触发干净 401/重新申请 T_short,而非 109 + x.find 崩溃(旧格式无前缀 T_short 会透传,5 分钟内自然过期,可接受)
- [ ] 原生分享页(带 RAGFlow beta Token,无 `pt_` 前缀)仍透传成功,不 403
- [ ] 浏览器 Network 面板 `/api/v1/chatbots/{id}/info` 与 `.../completions` 均返回 200(用 fresh portal T_short 时)
- [ ] 浏览器控制台无 `TypeError: x.find is not a function`,share 页渲染对话 UI 不再显示「Something went wrong」
- [ ] portal 代理 `/info` 响应 JSON 与直连 RAGFlow `/info` 响应结构逐字节一致(curl diff 验证 `data.doc_aggs` 为数组)
- [ ] 单测覆盖:`pt_` 前缀 token 不在 TokenStore → 401;无前缀 token → 透传;`pt_` 前缀 + 有效 → 换 beta Token
- [ ] 既有 pytest 全绿(无回归;基线 366 passed + 5 skipped)

### Blocked by

- Issue 18(Slice 18 透传分支已上线,本 slice 在其基础上细化区分逻辑)

---

## Issue 20 — Slice 20: Phase 3 完整 E2E 浏览器验收(B1 + B2 + B3 三 bug 确认)

### Parent

Phase 3(B1 + B2 + B3 三 bug 修复)。

### What to build

Slice 17(cookie 隔离)+ Slice 18(网关统一代理 + 透传)+ Slice 19(T_short 前缀区分)代码与部署均已就绪后,做最终浏览器 E2E 验收,确认 3 个原始 bug 消失,并勾选 Issue 17 + 18 + 19 的浏览器相关验收项。

本 slice 无代码改动(纯验收 + 文档更新);若验收中发现新问题,记录为新 issue 而非在本 slice 内修复。

### Acceptance criteria

- [ ] **B1 消失**:portal 嵌入的 iframe 加载 RAGFlow 分享页,显示对话 UI,不再跳 RAGFlow 登录页
- [ ] iframe 内 `GET /api/v1/chatbots/{id}/info` 返回 200(经网关换 beta Token)
- [ ] iframe 内发消息,`POST .../completions` SSE 流式回复正常,引用片段可见
- [ ] **B3 消失**:直接访问 `http://172.16.10.180/chats/share?shared_id=xxx` 原生分享页(不经 portal,无 portal 会话),发消息返回 200 不再 403
- [ ] 原生分享页的 `GET /info`(无 T_short 或带 RAGFlow beta Token)透传 RAGFlow,返回 200
- [ ] **B2 消失**:登录 RAGFlow 后刷新 `/portal/` 仍保持 portal 登录态;反之亦然
- [ ] `ragflow_type=agent` 的分享页同样工作(`/agentbots/{id}/inputs` + `/completions`)
- [ ] Issue 17 + 18 + 19 的浏览器相关验收项全部勾选;ISSUES.md Phase 3 验收清单标记完成

### Blocked by

- Issue 19(Slice 19 T_short 前缀区分 — 109 + x.find 修复后才能通过 E2E)

---

## Issue 21 — Slice 21: portal 前端改用预创建 session 端点(修复 /completions 403)

### Parent

Issue 18(Slice 18 网关代理上线后,浏览器 E2E 暴露的 session 归属 403 回归)。

### What to build

portal 前端目前调 `GET /share-pages/{id}/embed-url`(不预创建 session),iframe URL 无 `session_id` 参数。iframe 加载后,首次 `POST /api/v1/chatbots/{id}/completions`(无 session_id)成功 — 网关跳过 session 归属校验,RAGFlow 创建 session 并在 SSE 首帧返回 session_id。但**网关没把这个新 session 绑定到 `chat_session_owner`**(绑定只在 portal 的 `precreate_session` 路由做)。

后续 `POST /completions` 请求带 RAGFlow 返回的 session_id → 网关校验链步骤 3 `_assert_session_ownership` → `session_store.get(session_id)` 返回 None(session 从未绑定)→ **403「会话不存在或无权访问」**。

**修复方向**:portal 前端改为调 `POST /share-pages/{id}/sessions`(预创建 session 端点),而非 `GET /share-pages/{id}/embed-url`。预创建端点:
1. 调 RAGFlow 创建空 session,从 SSE 首帧解析 session_id;
2. 绑定 session_id 到 `chat_session_owner`(portal_user_id = 当前用户,ragflow_resource_id = dialog_id);
3. 签发 T_short 并构造**带 session_id 参数**的 iframe URL。

iframe 加载后,RAGFlow 前端从 URL 读 session_id,首问直接带 session_id → 网关归属校验通过(session 已绑定)→ 200。

### Acceptance criteria

- [ ] portal 前端「进入分享页」操作调 `POST /share-pages/{id}/sessions`(预创建),而非 `GET /share-pages/{id}/embed-url`
- [ ] 返回的 iframe URL 含 `session_id` 参数(与 `auth`/`shared_id`/`from` 并列)
- [ ] iframe 内首次发消息,`POST /completions` 请求体含 session_id,网关归属校验通过,返回 200
- [ ] 多轮对话:连续发 2+ 条消息,每次 `/completions` 均返回 200,不 403
- [ ] F12 Network:`/api/v1/chatbots/{id}/completions` 第二次及后续请求返回 200(非 403)
- [ ] portal 日志无 403「会话不存在或无权访问」
- [ ] 既有 pytest 全绿(无回归;基线 375 passed + 5 skipped)
- [ ] 前端单测覆盖:embed-url 按钮调预创建端点(若前端有 Vitest 测试)

### Blocked by

- Issue 19(Slice 19 — iframe URL 路径 + pt_ 前缀 + browser_origin 已修复,本 slice 在其基础上修 session 归属)

---

## Issue 22 — Slice 22: 网关 SSE 代理时绑定 RAGFlow 实际创建的 session(修正 Issue 21 方向)

### Parent

Issue 21(实施后 E2E 仍 403 — 方向错误,根因诊断见下)。

### 根因诊断(Issue 21 实施后 E2E 仍 403)

Issue 21 的假设:**「iframe 加载后,RAGFlow 前端从 URL 读 session_id,首问直接带 session_id」** — 这个假设是**错的**。

RAGFlow 前端 `web/src/pages/next-chats/hooks/use-send-shared-message.ts:77`:
```tsx
session_id: get(derivedMessages, '0.session_id'),
```
session_id 来自 `derivedMessages[0].session_id`(SSE 响应里 RAGFlow 返回的 session_id),**不从 URL `?session_id=` 读**。

页面加载时 `useEffect(fetchSessionId)`(`use-send-shared-message.ts:120-122`)发 `question: ''` 请求(无 session_id)→ RAGFlow 创建新 session_B → SSE 返回 session_B → 前端存 `derivedMessages[0].session_id = session_B`。

**完整失败链路**:
1. portal `precreate_session` 创建 session_A,绑定 session_A,返回 iframe URL 含 `session_id=session_A`
2. iframe 加载,RAGFlow 前端**忽略 URL 的 session_id**
3. `fetchSessionId` 发 `POST /completions` body `{question:'', session_id: undefined}` → 网关 `request_session_id=""` → 跳过归属校验 → 透传 → 200,RAGFlow 返回 session_B
4. 用户发消息 → `POST /completions` body `{question:'...', session_id: session_B}` → 网关 `_assert_session_ownership(session_B)` → `session_store.get(session_B)` 返回 None(只绑定了 session_A)→ **403**

Slice 21 往 iframe URL 塞 `session_id` 参数无效 — RAGFlow 前端不读它。

### What to build

**修复方向**:网关在 SSE 代理时,若请求体无 session_id(首次 fetchSessionId 请求),从 SSE 响应流解析 RAGFlow 返回的 session_id,流成功完成后绑定到当前用户。这样 iframe 实际使用的 session_B 会被绑定,后续请求(带 session_B)归属校验通过。

1. **网关 `_build_sse_streaming_response`**:流式转发的同时解析 SSE `data:` 行的 session_id;流成功完成后,若 `request_session_id == ""`(请求体无 session_id)且解析到响应 session_id,调 `session_store.bind(parsed_session_id, share_page_id, portal_user_id, dialog_id, org_id)` 绑定到当前用户。
2. **前端回退 Slice 21**:fullscreen 类型改回只调 `GET /embed-url`(不 precreate)。precreate 创建的 session_A 不会被 iframe 使用(RAGFlow 前端不读 URL session_id),留着只产生孤儿记录。widget 类型仍用 embed-url(返回 snippet,逻辑不变)。

### Acceptance criteria

- [ ] 网关 `_build_sse_streaming_response` 解析 SSE 响应的 session_id(首帧 `data.session_id` 或 `data.data.session_id`)
- [ ] 请求体无 session_id 且响应有 session_id 时,流成功完成后绑定到当前用户(chat_session_owner)
- [ ] 请求体有 session_id 且已绑定时,不重复绑定(走既有归属校验 + update_last_active 路径)
- [ ] 前端 fullscreen 类型改回调 `GET /embed-url`(回退 Slice 21 的 precreate)
- [ ] iframe 内首次发消息(fetchSessionId 的 question='' 请求触发绑定),第二次 /completions(带 session_id)返回 200
- [ ] 多轮对话:连续发 2+ 条消息,每次 /completions 均 200
- [ ] portal 日志无 403「会话不存在或无权访问」
- [ ] 既有 pytest 全绿(无回归;基线 375 passed + 5 skipped)
- [ ] 前端 Vitest 全绿(回退 Slice 21 后测试同步调整)

### Blocked by

- Issue 21(Slice 21 实施后暴露根因 — RAGFlow 前端不读 URL session_id)

---

## 后续待办(Issue 16 AC2 遗留)

> Issue 16 AC2「悬浮组件在任意页面右下角加载,点击展开对话窗,能正常对话」— Slice 16 实现了 `/widget/<id>` 骨架 HTML + 可嵌入 snippet + CSP frame-ancestors 放行,但 **悬浮组件实际 UI 渲染(右下角悬浮按钮 + 点击展开对话窗 + iframe 加载 + SSE 对话)尚未实现**。`/widget/<id>` 当前仅返回含 `<div id="widget-root">` 的占位 HTML,需前端构建产物挂载 React 组件。

**待实现内容:**
- 前端新建 WidgetEntry 组件(独立入口,不依赖 AuthContext)
- 右下角悬浮按钮(可折叠/展开)
- 展开后渲染 iframe(调 `/public/<id>/embed-url` 或标准 embed-url,带 session_id)
- widget 模式下会话 CRUD(复用 Slice 10 逻辑,但 widget 独立入口无登录态 — 若需登录则走标准 embed-url,若公开则走 /public)
- Vite 多入口构建配置(widget 独立 bundle)
- `/widget/<id>` HTML 模板加载 widget bundle JS

**Blocked by:** 无(可随时开始,但建议技术债清理后进行)

---

## 技术债(待重构,非 slice)

> 来源:Slice 8 + Slice 9 合并后的 `/review` Standards 报告(2026-07-06)。记入文档备查,暂不拆 slice,后续迭代时择机处理。
>
> 2026-07-06 更新:TD1/2/4/6/7/8/9/10/11/13/14/15 已清理(3 路并行重构,350 passed + 70 passed 全绿)。TD3 Won't fix,TD5 延后,TD12 保留(有测试调用)。
>
> 2026-07-06 二次更新:TD 清理后的 `/review`(fixed point `973fc2e`)发现 5 项 smell(4 判断项 + 1 范围蔓延),其中 3 项(TD14 dead param / TD8 守卫丢失 / TD11 重复回归)当场修复,余下 5 项记为 TD16-TD20 备查。

| # | 类型 | 位置 | 描述 | 处置 |
|---|---|---|---|---|
| TD1 | Duplicated Code | `portal/models.py` SeedData/SessionStore/AuditStore | `with self._sm() as session: ... session.commit()` 形状重复 30+ 次。可抽 `_transact(fn)` 上下文管理器。 | ✅ 已清理(抽 `_StoreBase._transact`,16 处重构) |
| TD2 | Duplicated Code | `portal/routes.py:443-447` 与 `:1002-1006` | 消息计数同步逻辑(`if isinstance(history, dict): ... update_message_count(...)`)两处逐字重复。可抽 `_sync_message_count(owner, history, session_id, store)` helper。 | ✅ 已清理(合并到 TD8 的 `sync_message_count_from_history`) |
| TD3 | Feature Envy | `portal/models.py` `build_seed_data` | 直接构造 `PortalUserModel`/`SharePageModel` 绕过 `SeedData` CRUD。**评估为有意为之**:固定 ID(`u_admin`/`sp_default`)保证 idempotent seed,`create_user` 用随机 ID 无法保证。 | Won't fix(有理由) |
| TD4 | Mysterious Name | `portal/models.py` `self._sm`(38 次) | `_sm` 对 `session_maker` 过简,`_session_maker` 更诚实。 | ✅ 已清理(全局改名 `_session_maker`,39 处) |
| TD5 | Primitive Obsession(pre-existing) | `portal/db.py` `SharePageGrantModel` | `subject_type`/`permission` 仍为 `String(16)`,虽同文件已定义 `SubjectType`/`Permission` Literal。pre-existing,非 Slice 8 引入。 | 延后(影响 schema 迁移) |
| TD6 | Duplicated Code | `frontend/src/pages/admin/*` + `SharePageDetailPage.tsx` | `formatTime` 函数在 4+ 前端文件重复定义。可抽 `frontend/src/utils/formatTime.ts`。 | ✅ 已清理(抽 `utils/formatTime.ts`,参数化 `date`/`datetime`/`seconds`) |
| TD7 | Duplicated Code | `frontend/src/pages/admin/*.tsx` | 6 个 admin 页同构 `useEffect` + cancelled IIFE + ApiError catch + `busyId` 乐观更新/回滚模式。可抽 `useAdminList` / `useOptimisticToggle` hook。 | ✅ 已清理(抽 `hooks/useAdminList.ts` + `hooks/useOptimisticToggle.ts`) |
| TD8 | Duplicated Code | `portal/gateway.py` `_sync_message_count_after_sse` 与 `portal/routes.py` `resume_session` | message_count 同步逻辑(`fetch_session_history_via_ragflow` + `update_message_count(len(messages))`)两处重复。可聚到 `SessionStore.sync_message_count_from_history`。 | ✅ 已清理(聚到 `SessionStore.sync_message_count_from_history`,三处调用统一) |
| TD9 | Data Clumps | `portal/models.py` PortalUser + `portal/db.py` PortalUserModel + `portal/oidc.py` + `portal/routes.py` | `sso_provider` + `sso_external_id` 6 处捆绑出现。可捆成 `SSOIdentity(provider, external_id)` 小类型。 | ✅ 已清理(捆成 `SSOIdentity` dataclass,PortalUser 用 `sso: SSOIdentity \| None`) |
| TD10 | Mysterious Name | `portal/routes.py` `_assert_oidc_enabled` | 名字只说「enabled」,实际还校验 4 项配置完整性(缺则 500)。改名 `_assert_oidc_ready` 或拆 `_assert_oidc_enabled` + `_assert_oidc_configured`。 | ✅ 已清理(改名 `_assert_oidc_ready`) |
| TD11 | Middle Man | `portal/routes.py` `_oidc_config(settings)` | 仅 4 字段直传到 `OIDCConfig(...)`,路由层只用一次、无独立测试。可内联或让 `oidc.py` 直接收 `Settings`。 | ✅ 已清理(内联到调用处) |
| TD12 | Speculative Generality | `portal/oidc.py` `_discovery_cache` + `clear_discovery_cache()` | 进程级 dict + 钩子,但 spec 无多 IdP 场景,`SSO_PROVIDER = "oidc"` 写死。缓存键用 issuer 是过度抽象。保留无害,删亦佳。 | 保留(`clear_discovery_cache` 有 8 处测试调用,删了破坏测试) |
| TD13 | Divergent Change(deprecated API) | `portal/main.py` `@app.on_event("startup"/"shutdown")` | FastAPI 旧式 API,有 DeprecationWarning。应迁移到 lifespan context manager。 | ✅ 已清理(迁移到 `lifespan` context manager,DeprecationWarning 从 1093 降到 1) |
| TD14 | Duplicated Code(安全敏感) | `portal/gateway.py` `proxy_sse_public_to_ragflow` 与 `_proxy_sse_public_core` | 公开 SSE 校验链(T_short validate / is_public / enabled / dialog_id / ownership)在两处逐行重复。任一改一侧必漏另一侧。应让 `proxy_sse_public_to_ragflow` 调用 `_proxy_sse_public_core` 而非复制。 | ✅ 已清理(`proxy_sse_public_to_ragflow` 调用 `_proxy_sse_public_core`,校验链统一) |
| TD15 | Duplicated Code / Repeated Switches | `portal/gateway.py` 4 个 `*_agent_session_via_ragflow` + `portal/routes.py` 4 处 `if ragflow_type == "agent"` | agent session 函数与 chat 版本几乎逐字相同(仅 URL 段 agentbots vs chatbots 不同)。应抽 `_ragflow_session_api(settings, resource_id, ragflow_type)` 统一分发。 | ✅ 已清理(chat 函数加 `ragflow_type` 参数 + `_ragflow_bot_segment` 辅助,agent 函数变 thin wrapper;routes.py 5 个 dispatch helper 统一分发) |
| TD16 | Feature Envy + Divergent Change | `portal/models.py` `SessionStore.sync_message_count_from_history` | TD8 合并点把 `fetch_*_session_history_via_ragflow`(HTTP 调用)放进数据层 `SessionStore`,需 `# 延迟导入避免循环依赖` 注释掩盖反向依赖;且 SessionStore 原全同步,现混入 async 方法。更合适归宿:gateway/routes 侧 helper 调 `store.update_message_count`。 | 待重构(记自 TD 清理后 `/review` Standards 报告) |
| TD17 | Repeated Switches + Middle Man | `portal/routes.py` 5 个 dispatch helper + `portal/gateway.py` `_ragflow_bot_segment` + 4 个 `*_agent_session_*` thin wrapper | TD15 半抽取:switch 没消除,只是从调用点搬到两层 helper;4 个 agent 函数退化为 1 行 thin wrapper(注释「供测试 monkeypatch」)。可进一步统一为单分发 + 策略对象,或接受当前形态(agent wrapper 保留供 mock)。 | 待重构(当前测试依赖 agent wrapper 的 monkeypatch,重构需同步改测试) |
| TD18 | Mysterious Name | `frontend/src/hooks/useOptimisticToggle.ts` | hook 名只反映「乐观」模式,但 `UsersAdminPage.handleDelete` 当悲观删除用(注释「悲观删除:成功后才 filter」),JSDoc 也承认双模。可改名 `useToggleState` 或拆 `useOptimisticToggle` + `usePessimisticToggle`。 | 待重构(命名调整涉及 6 个 admin 页调用方) |
| TD19 | Speculative Generality | `frontend/src/hooks/useAdminList.ts` | hook 返回 `data`/`setData` 状态,但 6 个 admin 页中 4 个(Groups/Sessions/AuditLogs/Grants)只用 `onSuccess` 分发,从不读 `data`/`setData`。可拆为 `useAdminList`(带状态)+ `useAdminFetch`(仅 fetch+onSuccess),或保持现状(无害的通用 hook)。 | 待重构(低优先,现状无害) |
| TD20 | Shotgun Surgery(超规格) | `portal/main.py` `create_app` | TD13 仅要求迁移到 lifespan context manager,但 engine/init_db 被提前到 `FastAPI()` 构造之前。功能等价,但属超规格重构。可回退 engine 创建时序到 lifespan 内,或保留(已通过测试,无回归)。 | 待评估(保留现状风险低,回退有回归风险) |

## 迁移至正式 issue tracker 时的说明

- 每个 Issue 节对应一个 issue,标题用「Slice N: <描述>」。
- `ready-for-agent` 标签打在每个 issue 上。
- Phase 1(Issue 1-7)已完成,迁移时标 `done` 或归档。
- Phase 2 发布顺序按依赖:Issue 8 → (9 并行) → (10/11 并行,依赖 9) → 12(依赖 8);可选项 13(依赖 8)/14(独立)/15(依赖 8)/16(依赖 9)按需发布。
- `Blocked by` 字段填上游 issue 编号。
- Issue body 直接复用本文档对应章节。
