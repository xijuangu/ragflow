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

## Issue 23 — Slice 23: 会话列表实时刷新 + 新建会话防抖 + 消息数准确同步

### Parent

Issue 22(Slice 22 修复了 /completions 403,E2E 验证发消息正常,但暴露会话列表/历史恢复/消息计数 3 个新问题)。

### 根因(E2E 验证发现)

1. **发消息后左侧无会话记录**:`SharePageDetailPage.tsx` 的"我的会话"列表只在挂载时调一次 `listSessions`,发消息后不刷新。网关 Slice 22 已 bind session 到 DB,但前端没重新拉列表 → 看不到新会话。
2. **新建会话一次性弹出多个**:`handleNewSession` 用 `sessionBusy` state 做守卫,但 `setSessionBusy(true)` 是异步的。React 18 批处理 → 快速点击时 `sessionBusy` 还未更新 → 守卫失效 → 多次调 `precreateSession` → 创建多个 session。
3. **切换会话再切回来显示 0 条**:网关 Slice 22 bind session 时 `message_count=0`,`_sync_message_count_after_sse` 调 RAGFlow GET history 同步,但对 RAGFlow 新建的空 session 返回空历史 → count 仍 0。

### What to build

**端到端行为**:用户在 iframe 发消息 → 网关 bind session → 前端检测到新 session → 刷新"我的会话"列表 → 用户看到新会话出现且消息数正确。点击"新建会话"按钮 → 只创建一个(防抖)。

1. **前端会话列表实时刷新**:`SharePageDetailPage.tsx` 在 iframe 加载后定时轮询(如每 5s)或通过 postMessage 检测 iframe 内新消息 → 重新调 `listSessions` 刷新左侧列表。
2. **新建会话防抖**:`handleNewSession` 用 `useRef` 做同步守卫(ref 赋值是同步的,非 state 异步),防止快速点击绕过 `sessionBusy` 守卫。
3. **消息数准确同步**:网关 `_sync_message_count_after_sse` 对 RAGFlow 新建空 session(GET history 返回空)的场景,改为 SSE 流完成后 `message_count += 1`(每轮对话 +1),不依赖 GET history。

### Acceptance criteria

- [ ] 用户在 iframe 首次发消息后,左侧"我的会话"列表在 5s 内出现新会话(标题"新会话",消息数 1)
- [ ] 点击"新建会话"按钮,无论点击多快,只创建一个 session(不弹多个)
- [ ] 连续发 2+ 条消息后,左侧列表对应会话显示正确的消息数(非 0)
- [ ] 既有 pytest 全绿(无回归;基线 380 passed + 5 skipped)
- [ ] 前端 Vitest 全绿
- [ ] E2E:iframe 内发消息 → 左侧列表实时刷新 → 切换到其他会话再切回 → 消息数正确

### Blocked by

- Issue 22(Slice 22 已修复 403,发消息正常 — 本 slice 在其基础上修列表/防抖/计数)

---

## Issue 24 — Slice 24: iframe 恢复历史会话(RAGFlow 前端读 URL session_id)

### Parent

Issue 22(Slice 22 根因诊断确认:RAGFlow 前端 `use-send-shared-message.ts:77` 的 session_id 来自 SSE 响应,不从 URL `?session_id=` 读 → 刷新/切换会话后 iframe 创建新 session → 历史丢失)。

### 根因(E2E 验证发现)

RAGFlow 前端 `web/src/pages/next-chats/hooks/use-send-shared-message.ts`:
- 第 24-42 行 `useGetSharedChatSearchParams` 只从 URL 读 `shared_id`/`from`/`locale`/`theme`/`visible_avatar`,**不读 `session_id`**。
- 第 111-122 行 `fetchSessionId` 在页面加载时发 `question=''` 请求(无 session_id)→ RAGFlow 创建新 session → 前端存 `derivedMessages[0].session_id`。
- 第 77 行 `sendMessage` 的 `session_id` 来自 `derivedMessages[0].session_id`(SSE 响应),不从 URL 读。

**失败链路**:刷新页面 / 点击历史会话 → iframe 重载 → `fetchSessionId` 创建新 session → 旧 session 的历史不恢复 → 用户看不到之前的对话。

**2026-07-07 E2E 补充反馈(Issue 25 部署后)**:
- 进入分享页后发送一条消息,左侧会刷新出一个新会话(绑定与刷新已生效)。
- 点击这个会话后,iframe 内只显示一个 greeting,没有刚才发出的用户消息与 RAGFlow 回复。
- 左侧消息数显示为 1。用户期望若按 UI 消息条数统计,首轮应包含 greeting + 用户消息 + 助手回复共 3 条。
- 管理后台「会话搜索 → 查看正文 → 以管理员身份查看」调用 `/portal/admin/sessions/<session_id>?elevated=true` 返回 502,弹窗显示 `RAGFlow 取回会话失败: HTTP 404`。
- 结论:Issue 25 已解决 greeting 垃圾会话与绑定刷新问题,但历史会话恢复仍未完成;点击会话和管理员 elevated 查看正文都没有取回绑定 session 的真实 RAGFlow history。

### What to build

**端到端行为**:用户点击左侧历史会话 → iframe URL 带 session_id → RAGFlow 前端读 URL session_id → 跳过 `fetchSessionId`(不创建新 session)→ 用该 session_id 恢复历史 → 用户看到历史消息。

1. **修改 RAGFlow 前端** `use-send-shared-message.ts`:
   - `useGetSharedChatSearchParams` 加 `sessionId` 字段(从 URL 读 `session_id`)。
   - `fetchSessionId` 在有 URL session_id 时跳过(不创建新 session),或用该 session_id 调 GET history 恢复 `derivedMessages`。
2. **portal 前端**:`handleReopen` 恢复 iframe URL 带 session_id(已有 `appendSessionId` 逻辑,确认有效)。
3. **注意**:这是修改 RAGFlow 源码(`web/src/`),需评估 RAGFlow 升级时的维护成本。修改应尽量小且可选(URL 无 session_id 时保持原行为)。

### Acceptance criteria

- [ ] RAGFlow 前端 `useGetSharedChatSearchParams` 读 URL `session_id` 参数
- [ ] iframe URL 含 session_id 时,`fetchSessionId` 跳过(不创建新 session)
- [ ] 用户发一条消息并获得回复后,左侧出现该会话
- [ ] 用户点击左侧会话 → iframe 重载 → 显示该会话的真实历史消息:至少包含 greeting、用户问题、RAGFlow 回复
- [ ] 点击左侧会话不会创建新的 greeting-only session
- [ ] 切换会话再切回 → 仍显示对应历史消息与引用片段
- [ ] 管理后台会话搜索中点击「查看正文」并以管理员身份查看 → 返回该会话 messages + reference,不再报 502/`RAGFlow 取回会话失败: HTTP 404`
- [ ] 刷新页面 → iframe 重载 → 如恢复最近活跃会话,显示真实历史而不是 greeting-only
- [ ] iframe URL 不含 session_id 时,保持原行为(创建新 session,兼容公开分享)
- [ ] 既有 portal pytest 全绿(无回归)
- [ ] 前端 Vitest 全绿
- [ ] E2E:发消息 → 刷新页面 → 历史消息仍在 → 切换会话再切回 → 历史消息仍在

### Blocked by

- Issue 25(Slice 25 修复刷新产生垃圾会话 + 发消息后不显示 — 本 slice 修复点击后恢复历史,依赖 Slice 25 先把会话列表准确性修好)

---

## Issue 25 — Slice 25: 会话列表准确性 — 过滤 greeting session + 真实消息后及时刷新

### Parent

Issue 23(Slice 23 加了 5s 轮询 + useRef 防抖 + increment_message_count,E2E 验证发现 RAGFlow 前端每次 iframe 加载自动发 greeting 创建新 session,网关 Slice 22 把 greeting session 也 bind 到用户 → 刷新产生垃圾会话;且 5s 轮询太慢,发消息后左侧不及时显示)。

### 根因(E2E 验证发现)

1. **刷新产生垃圾会话**:RAGFlow 前端 `web/src/pages/next-chats/hooks/use-send-shared-message.ts:120-122` 在 iframe 挂载时自动调 `fetchSessionId()`,发 `{ question: '' }` 的 greeting 请求(无 session_id)→ RAGFlow 创建新 session 返回 session_id + prologue(开场白)。网关 Slice 22 的 `_build_sse_streaming_response` 在 request body 无 session_id 时,从 SSE 响应解析 session_id 并 bind 到用户。每次刷新 iframe 重载 → 又发 greeting → 又 bind → 左侧出现消息数为 1 的垃圾会话(只有打招呼)。

2. **发消息后左侧不及时显示**:Slice 23 的 5s 轮询间隔对用户感知太慢。用户进入页面后初始 `listSessions` 返回空,greeting session 在 5s 后才被轮询检测到。用户在 5s 内发消息时左侧仍为空,误以为"没显示"。

### What to build

**端到端行为**:用户进入分享页 → iframe 加载发 greeting(创建 session 但不 bind 到用户)→ 左侧不显示 greeting session(无垃圾会话)→ 用户发首条消息 → 网关 bind session 到用户 → 前端及时刷新列表 → 左侧出现会话。刷新页面 → 不产生新会话。

1. **网关过滤 greeting session**:`_build_sse_streaming_response` 的 bind 逻辑加 `question` 字段判断 — 请求体 `question == ''`(greeting)时不 bind session(即使 SSE 返回 session_id)。`question != ''`(真实消息)时:
   - 若 request body 有 session_id 且 session 未 bind → bind 到当前用户(greeting 创建的 session 现在被用户正式使用)
   - 若 session 已 bind → `update_last_active` + `increment_message_count`(保持 Slice 23 行为)
2. **校验链放行未 bind 的 session**:SSE 代理前的 session 归属校验,对"session 存在于 RAGFlow 但未 bind 到 portal"的情况放行(前提:dialog_id 与 share_page 一致),允许用户用 greeting 创建的 session 发首条消息。bind 发生在 SSE 流成功后(非校验阶段)。
3. **前端及时刷新**:SSE 流完成后主动触发一次 `listSessions` 刷新(不等 5s 轮询)。可用 postMessage(iframe → 父页面)或缩短轮询间隔到 2s。零侵入优先(postMessage 需改 RAGFlow 源码,缩短间隔更简单)。

### Acceptance criteria

- [ ] 刷新分享页 → 左侧不出现新会话(无 greeting 垃圾会话)
- [ ] 连续刷新 3 次 → 左侧会话数不增加(均为既有会话)
- [ ] 用户首次发消息后 → 左侧 2s 内出现新会话(消息数 1)
- [ ] 用户发 2+ 条消息 → 左侧会话消息数正确递增
- [ ] greeting session 不在 `chat_session_owner` 表中留下记录(未 bind)
- [ ] 既有 pytest 全绿(无回归;基线 384 passed + 5 skipped)
- [ ] 前端 Vitest 全绿
- [ ] E2E:进入 → 刷新 3 次 → 左侧无新会话 → 发消息 → 2s 内显示会话 → 消息数正确

### Blocked by

- Issue 23(Slice 23 已完成轮询 + 防抖 + increment — 本 slice 在其基础上修 greeting 过滤 + 及时刷新)

---

## Issue 26 — Slice 26: 会话列表消息数与 RAGFlow 历史消息条数一致

### Parent

Issue 24(恢复历史会话后,左侧消息数需要与用户实际看到的 RAGFlow 历史消息条数一致)。

### 根因(E2E 验证发现)

Issue 23/25 当前用 SSE 成功后的 `message_count += 1` 表示"完成一轮真实提问"。这能避免新会话显示 0,但语义与 UI 上的消息条数不一致。

用户当前期望左侧"消息数"反映 RAGFlow 历史消息条数:首轮对话应显示 3 条(greeting + 用户问题 + 助手回复),而不是 1 轮。

### What to build

**端到端行为**:用户发送消息并获得回复后,左侧会话列表中的消息数与恢复会话时 RAGFlow 返回的历史 messages 数一致。消息数不再表示"问答轮次",而表示用户点击会话后实际能看到的消息条数。

1. **明确消息数语义**:左侧列表与管理员元数据里的 `message_count` 均表示 RAGFlow history messages 条数。
2. **真实消息后同步绝对值**:SSE 流成功后,在不破坏发消息体验的前提下同步该 session 的 RAGFlow history,用 `len(messages)` 更新 `message_count`。
3. **恢复会话时校准**:用户点击历史会话或管理员查看正文时,继续用 RAGFlow history 绝对值校准 `message_count`。
4. **失败容错**:若 history 同步失败,不影响 SSE 正常返回;但不得把 `message_count` 更新成错误的 `+1` 值。

### Acceptance criteria

- [ ] 首次真实提问并获得回复后,左侧消息数显示为 3(greeting + 用户消息 + RAGFlow 回复)
- [ ] 每新增一轮问答后,消息数按 RAGFlow history messages 条数同步,不是简单 `+1`
- [ ] 点击历史会话恢复后,列表中的 `message_count` 与恢复接口返回的 messages 数一致
- [ ] greeting-only session 不进入会话列表,也不影响任何已有会话的消息数
- [ ] GET history 失败时不破坏发消息流程,但列表不展示错误的新增消息数
- [ ] 既有 portal pytest 全绿(无回归)
- [ ] 前端 Vitest 全绿
- [ ] E2E:发消息 → 左侧消息数为 3 → 点击会话 → iframe 内消息条数与左侧一致

### Blocked by

- Issue 24(Slice 24 先修复点击会话后能恢复真实历史;本 slice 再校准消息数语义)

---

## Issue 27 — Slice 27: 部署 RAGFlow bot_api 扩展端点到运行容器(修复 GET/PATCH/DELETE sessions 404)

### Parent

Issue 24(Slice 24 部署 commit `1a65f38` 后浏览器 E2E 暴露:点击会话与管理员 elevated 查正文均 404)。

### 根因(E2E 验证发现)

部署的 RAGFlow 容器 `docker-ragflow-cpu-1`(官方 v0.26.0 镜像)`api/apps/restful_apis/bot_api.py` 只有 4 个端点(`/completions`、`/info`、`/agentbots/*/completions`、`/agentbots/*/inputs`)。本地 fork 在 Slice 2(commit `e4d2f3a`)与 Slice 5(commit `6fecd01`)加的 3 个 chatbot 端点 + 对应 agentbot 端点 + `_assert_dialog_access` helper **从未部署到容器**。

Portal 网关 `fetch_session_history_via_ragflow`(`portal-extension/portal/gateway.py:514`)调 RAGFlow `GET /api/v1/chatbots/<dialog_id>/sessions/<session_id>` → RAGFlow 返回 404 → portal 原样透传给浏览器 → 浏览器显示「请求错误 404: undefined」,iframe 内只有界面无消息。管理员 elevated 视图(`portal-extension/portal/routes.py`)同理 502「RAGFlow 取回会话失败: HTTP 404」。

服务器验证:`ssh 172.16.10.180 'docker exec docker-ragflow-cpu-1 grep -n "_assert_dialog_access\|chatbots.*sessions" /ragflow/api/apps/restful_apis/bot_api.py'` 返回空。

### What to build

把本地 fork 在 Slice 2/5 加的 chatbot 端点(GET/PATCH/DELETE `/chatbots/<dialog_id>/sessions/<session_id>`)+ 对应 agentbot 端点(Slice 16/TD15 重构时加的 thin wrapper)+ `_assert_dialog_access` helper,部署到 172.16.10.180 的 `docker-ragflow-cpu-1` 容器。

**部署方式(用户确认):docker cp 临时替换**。把改动的 `.py` 文件 docker cp 进容器,重启 RAGFlow api server 进程(容器内 `pkill -f ragflow_server.py`,entrypoint 会自动拉起;或 `docker restart docker-ragflow-cpu-1`)。

**运维约束**:docker cp 替换在容器重建(`docker compose down/up`)时会丢失。需在 `docs/HANDOFF-phase3.md` 或运维文档记录此约束,后续若重建容器需重新 cp 或改为 volume 挂载 / rebuild 镜像。

**改动范围**:对照本地 `api/apps/restful_apis/bot_api.py` 与容器内版本,diff 出需 cp 的文件(至少 `bot_api.py`;若 agent 端点在 `agent_api.py` 也一并 cp;若 `_assert_dialog_access` 依赖其他 helper 也需 cp)。cp 前先备份容器内原文件(`dist.bak.<timestamp>` 模式)。

### Acceptance criteria

- [x] 容器内 `curl GET /api/v1/chatbots/{dialog_id}/sessions/{session_id}` 返回 200(非 404)— 部署后 curl 验证通过(无 T_short 返回鉴权错误 JSON,端点存在;portal 透传后 200)
- [ ] 容器内 `curl PATCH .../sessions/{sid}` 重命名返回 200,`API4Conversation.name` 更新 — curl 层面端点存在(返回鉴权 JSON 非 404),完整 PATCH 验证留待 Slice 29
- [ ] 容器内 `curl DELETE .../sessions/{sid}` 返回 200,记录删除 — 同上,端点存在,完整 DELETE 验证留待 Slice 29
- [x] nginx 路径 `GET /api/v1/chatbots/{id}/sessions/{sid}` 返回 200(portal 透传 RAGFlow) — Slice 27 部署后 curl 验证通过
- [x] 容器内原 `bot_api.py` 已备份(`bot_api.py.bak.20260707-145501`),可在需要时回滚
- [x] 运维文档记录「容器重建需重新 cp」约束(更新 `docs/HANDOFF-phase3.md` §5.1 部署命令)
- [x] 既有 portal pytest 全绿(无回归;基线 387 passed + 5 skipped)— Slice 27 部署后 pytest 387 passed + 5 skipped,无回归
- [ ] 浏览器点击左侧会话,iframe 显示历史消息 — 留待 Slice 29 E2E 验收
- [ ] 管理后台「会话搜索 → 查看正文 → 以管理员身份查看」返回 200 — 留待 Slice 29 E2E 验收
- [ ] ~`ragflow_type=agent` 的分享页同样工作~ — **已知限制**:portal 网关 `_ragflow_bot_segment` 对 agent 返回 "agentbots",但 RAGFlow 官方只有 `/agents/<id>/sessions/<sid>`(非 `/agentbots/`)。chat 类型已修复,agent 类型需改 portal 网关 URL 构造,留作后续 issue(非 Slice 27 范围)

### Blocked by

None - can start immediately(Issue 24 已部署,本 slice 修部署遗漏)

---

## Issue 28 — Slice 28: 修复"新建会话"按钮创建空 session 无 greeting

### Parent

Issue 24(Slice 24 改 RAGFlow 前端读 URL `session_id` 跳过 `fetchSessionId` greeting,与 portal 前端 `handleNewSession` 的 precreate 路径冲突)。

### 根因(E2E 验证发现)

Slice 24 改了 RAGFlow 前端 [use-send-shared-message.ts](file:///Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/web/src/pages/next-chats/hooks/use-send-shared-message.ts):URL 有 `session_id` → 跳过 `fetchSessionId`(greeting)→ 调 GET history 恢复 `derivedMessages`。

但 portal 前端 [SharePageDetailPage.tsx:155](file:///Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend/src/pages/SharePageDetailPage.tsx#L155) 的 `handleNewSession` 仍调 `api.precreateSession(id)`,返回的 `iframe_url` 带 `session_id` 参数 → RAGFlow 前端读 URL session_id → 跳过 greeting → 调 GET history 取 precreate 的空 session → 显示空(且因 Issue 27 缺端点还会 404)。

用户期望(已确认):「新建会话」按钮应显示 greeting 开场白,符合用户对「新建会话」的直觉。

### What to build

portal 前端 `handleNewSession` 改为不带 session_id 的路径:调 `api.getEmbedUrl(id)`(而非 `precreateSession`),返回的 iframe URL 不含 `session_id` → RAGFlow 前端走 `fetchSessionId` 创建新 session + greeting → 网关 Slice 22 逻辑在 SSE 成功后绑定到当前用户 → Slice 23 轮询刷新左侧列表。

消除 precreate 路径产生的空 session 孤儿(precreate 创建的 session_A 不会被 iframe 使用,因 RAGFlow 前端不读 URL session_id —— Slice 22 根因诊断已确认)。fullscreen 类型不再用 precreate;widget 类型若仍需 precreate(独立入口逻辑)单独评估。

### Acceptance criteria

- [ ] 点击「新建会话」→ iframe 显示 greeting 开场白(非空) — 留待 Slice 29 E2E 验收
- [ ] 发送首条消息后,左侧 2s 内出现新会话,消息数正确(非 0) — 留待 Slice 29 E2E 验收
- [ ] 不产生消息数为 0 的孤儿 precreate session(验证:新建会话后立即刷新,左侧不出现新会话,只有发消息后才出现) — 留待 Slice 29 E2E 验收
- [ ] 连续点击「新建会话」多次,只创建一个 session(保留 Slice 23 的 useRef 防抖) — 留待 Slice 29 E2E 验收
- [x] fullscreen 类型不再调 `precreateSession`(改调 `getEmbedUrl`) — commit c7b1dae,handleNewSession 分流:fullscreen 走 getEmbedUrl,widget 保持 precreate
- [x] 既有前端 Vitest 全绿 — 76 passed(73 基线 + 3 新增 Slice 28 测试)
- [x] 既有 portal pytest 全绿(无回归) — 387 passed + 5 skipped(Slice 30 合并后 400 passed)

### Blocked by

- Issue 27(Slice 27 先部署 GET history 端点,否则新建会话的 greeting session 点击会 404)

---

## Issue 29 — Slice 29: Issue 24/26 E2E 验收闭环

### Parent

Issue 24 + 26(代码已实现并部署,但浏览器 E2E 验收因 Issue 27 部署遗漏 + Issue 28 新建会话副作用而未闭环)。

### What to build

Issue 27(部署端点)+ Issue 28(新建会话修复)代码与部署均就绪后,做最终浏览器 E2E 验收,确认 Issue 24 + 26 的 acceptance criteria 全部满足,勾选 ISSUES.md。本 slice 无代码改动(纯验收 + 文档)。

若验收中发现新问题,记录为新 issue 而非在本 slice 内修复。

### Acceptance criteria

- [ ] Issue 24 的 11 项 AC 全部勾选(关键:点击会话恢复历史、刷新页面历史仍在、admin elevated 不 502)
- [ ] Issue 26 的 8 项 AC 全部勾选(关键:首次提问后消息数 = 3、新增轮次按 history 长度同步)
- [ ] Issue 20(Phase 3 B1/B2/B3 E2E 验收)顺带完成或记录剩余项
- [ ] Slice 27 + 28 的浏览器相关验收项全部勾选
- [ ] 验收过程无新 issue 产生(或有则记录)

### Blocked by

- Issue 27(Slice 27 先修复 404)
- Issue 28(Slice 28 先修复新建会话 greeting)
- Issue 31(Slice 31 先修复会话列表滚动条,验收时确认 UI 完整可用)
- Issue 32(Slice 32 先移除 iframe Reset 按钮,避免历史不一致)
- Issue 33(Slice 33 先修复 SSE 期间切换丢失消息,避免验收时数据丢失)

---

## Issue 30 — Slice 30: 修复 agent 类型 sessions 端点 URL 构造(404)

### Parent

Issue 27(Slice 27 部署 chat 类型 sessions 端点时发现 agent 类型仍有 404)。

### 根因

portal 网关 `_ragflow_bot_segment`(`portal-extension/portal/gateway.py:457`)对 agent 统一返回 "agentbots":

```python
def _ragflow_bot_segment(ragflow_type: str) -> str:
    return "agentbots" if ragflow_type == "agent" else "chatbots"
```

该 segment 用于 completions 与 sessions 两类端点。但 RAGFlow 官方端点路径不一致:

| 端点类型 | chat | agent | RAGFlow 路径来源 |
|---|---|---|---|
| completions | `/api/v1/chatbots/<id>/completions` | `/api/v1/agentbots/<id>/completions` | `bot_api.py`(agentbot 部分在 bot_api.py) |
| sessions(GET/PATCH/DELETE) | `/api/v1/chatbots/<id>/sessions/<sid>` | `/api/v1/agents/<id>/sessions/<sid>` | chat 在 `bot_api.py`(本地 fork 加),agent 在 `agent_api.py`(官方) |

agent 的 sessions 端点 RAGFlow 用 `/agents/`(非 `/agentbots/`),导致 portal 网关 `fetch_session_history_via_ragflow` / `rename_session_via_ragflow` / `delete_session_via_ragflow` 对 agent 调 `/agentbots/<id>/sessions/<sid>` 返回 404。

### What to build

拆分 segment 函数:`_ragflow_bot_segment` 仍用于 completions(保持 "agentbots" 不变),新增 `_ragflow_sessions_segment` 用于 sessions 端点:agent → "agents",chat → "chatbots"。

改动范围:
- `portal/gateway.py`:加 `_ragflow_sessions_segment` 函数,在 `fetch_session_history_via_ragflow` / `rename_session_via_ragflow` / `delete_session_via_ragflow` 中用它替换 `_ragflow_bot_segment`
- 既有测试验证 agent sessions URL 改为 `/agents/`(非 `/agentbots/`)
- chat 类型 sessions URL 不变(仍 `/chatbots/`)

### Acceptance criteria

- [x] `fetch_session_history_via_ragflow(ragflow_type='agent')` 上游 URL 含 `/agents/`(非 `/agentbots/`) — commit b92be81
- [x] `rename_session_via_ragflow(ragflow_type='agent')` 上游 URL 含 `/agents/` — commit b92be81
- [x] `delete_session_via_ragflow(ragflow_type='agent')` 上游 URL 含 `/agents/` — commit b92be81
- [x] chat 类型 sessions URL 不变(仍 `/chatbots/`) — 13 个测试覆盖,chat sessions URL 仍含 `/chatbots/`
- [x] completions 端点 URL 不变(仍用 `_ragflow_bot_segment`,agent → "agentbots") — `precreate_session_via_ragflow` 与 `proxy_sse_to_ragflow` 未改,测试断言 agent completions 仍含 `/agentbots/`
- [x] 既有 portal pytest 全绿(无回归;基线 387 passed + 5 skipped) — 合并后 400 passed + 5 skipped(13 新增 Slice 30 测试)
- [x] 新增/修改的单测覆盖 agent sessions URL 构造 — `tests/test_slice30_agent_sessions_url.py` 13 个测试

**额外修复**:子代理发现第 4 个 sessions URL 函数 `proxy_session_history_to_ragflow`(L1048,服务于路由 `GET /api/v1/agentbots/{id}/sessions/{sid}`)同样错误使用 `_ragflow_bot_segment`,已一并修复(否则 agent GET history 代理端点仍 404)。

### Blocked by

None - can start immediately(独立改动,与 Slice 28 无文件冲突)

---

## Issue 31 — Slice 31: 会话列表添加滚动条(会话增多时不再挤压)

### Parent

无(E2E 验收 Slice 28 时发现的使用性问题)。

### 根因(E2E 验证发现)

`SharePageDetailPage` 会话列表 CSS 缺少 flex 收缩约束,会话增多时列表持续扩展而非滚动,导致每个会话项被挤压。

- `.sessions-sidebar`(`styles.css:285`):`display: flex; flex-direction: column; overflow: hidden` —— 缺 `min-height: 0`
- `.session-list`(`styles.css:315`):`overflow-y: auto; flex: 1; display: flex; flex-direction: column` —— `overflow-y: auto` 已设,但不生效

**根因**:flex column 容器的子元素默认 `min-height: auto`,会随内容增长而非触发滚动。需给滚动子元素(或容器)加 `min-height: 0`,才能让 `overflow-y: auto` 生效。这是 flex 布局的经典陷阱。

### What to build

修复 `frontend/src/styles.css` 的 `.sessions-sidebar` 和/或 `.session-list`,加 `min-height: 0` 让 flex item 能正确收缩并触发垂直滚动。改动范围:1-2 个 CSS 属性,无逻辑改动。

可选增强:测试覆盖会话列表项超过视口高度时的滚动行为(jsdom 测试 `scrollTop > 0` 或 `overflow-y: auto` 计算样式)。

### Acceptance criteria

- [ ] 会话列表项超过视口高度时,出现垂直滚动条
- [ ] 会话增多时,每个会话项高度不变,不被挤压
- [ ] 会话少时(少于视口),无滚动条(不浪费空间)
- [ ] sidebar 头部(「我的会话」+「新建会话」按钮)始终可见,不随列表滚动
- [ ] iframe 区域不受影响(不被列表挤压)
- [ ] 既有前端 Vitest 全绿

### Blocked by

None - can start immediately(纯 CSS 修复,与后端无关)

---

## Issue 32 — Slice 32: 移除 iframe 右上角重置按钮(前端重置导致历史不一致)

### Parent

无(E2E 验收 Slice 28 时发现)。

### 根因(E2E 验证发现)

RAGFlow 分享页 `web/src/pages/next-chats/share/index.tsx:62` 把 `removeAllMessagesExceptFirst`(仅前端清空 `derivedMessages`,不调后端)传给 `EmbedContainer` 的 `handleReset` prop,渲染右上角「Reset」按钮。

用户点击「Reset」:
1. 前端 `derivedMessages` 被清空(只剩 greeting)→ 界面显示重置成功
2. 但后端 `API4Conversation` 的 `message` 数组不变 → session_id 不变
3. 继续对话 → RAGFlow 在原 session 追加新消息(因 session_id 未变)
4. 刷新页面 → Slice 24 的 `fetchSessionId` / GET history 从 RAGFlow 取回完整历史(含「重置前」+「重置后」所有消息)→ 旧消息恢复,新消息接在后面

**根因**:前端重置与后端状态不一致。RAGFlow 的 `removeAllMessagesExceptFirst` 设计用于原生 Chat(本地状态,刷新不恢复),不适用于 iframe 分享页(刷新从后端恢复历史)。

### What to build

不传 `handleReset` 给 `EmbedContainer`(`EmbedContainer` 的 `handleReset?` 是可选 prop,不传则按钮无 onClick 或不渲染)。改动范围:RAGFlow `web/src/pages/next-chats/share/index.tsx` 删除/注释 `handleReset={removeAllMessagesExceptFirst}` 一行。

**替代方案(不采用)**:让 Reset 按钮真正调后端删除当前 session + 创建新 session。但这需 portal 网关配合(签发新 session_id + iframe URL 重载),改动大且 portal 已有「新建会话」按钮覆盖此场景。直接移除更简单,且避免用户误操作。

**门户已有「新建会话」按钮**(`SharePageDetailPage` 左侧 sidebar 顶部),功能完整(Slice 28 修复后显示 greeting + 创建新 session),iframe 内的 Reset 按钮冗余且有副作用,应移除。

### Acceptance criteria

- [ ] iframe 右上角不再显示「Reset」按钮(或按钮无 onClick,不触发前端重置)
- [ ] 用户无法触发前端重置,不会出现「重置前+重置后」历史不一致
- [ ] 门户左侧「新建会话」按钮仍正常工作(Slice 28 覆盖此场景)
- [ ] RAGFlow `web` `npm run build` 通过
- [ ] 容器内 `/ragflow/web/dist` 替换 + nginx reload 后,浏览器验证 Reset 按钮消失

### Blocked by

None - can start immediately(独立 RAGFlow web 改动)

---

## Issue 33 — Slice 33: SSE 流式响应期间禁用会话切换(避免消息丢失)

### Parent

无(E2E 验收 Slice 31/32 时发现)。

### 根因(E2E 验证发现)

iframe 内 RAGFlow 前端用 `useSendMessageWithSse()`(返回 `{ send, answer, done }`)处理流式响应。当用户在 portal 侧点击「新建会话」或「切换会话」时:

1. portal 改变 `iframeUrl` → iframe 重载
2. RAGFlow 前端组件卸载 → SSE 请求被中止(`send` 的 fetch 还在 pending)
3. `derivedMessages` state 被清空 → iframe 重载后 GET history 取不到未完成的消息(RAGFlow 后端只在 SSE 完成后才写入 session history)
4. 结果:**本次消息丢失,新会话可能未创建,原会话也缺这条消息**

**根因**:portal 侧切换按钮不知道 iframe 内 SSE 正在进行,直接重载 iframe 中断了流式响应。这是跨 iframe 边界的状态同步缺失。

### What to build

跨 iframe 边界的状态同步(垂直 slice,端到端):

1. **RAGFlow 前端 `use-send-shared-message.ts`**:在 `send(completionUrl, ...)` 调用前向 `window.parent` postMessage `{ type: 'ragflow:completions:start' }`;在 SSE `done` 或 error 时 postMessage `{ type: 'ragflow:completions:end' }`。注意:
   - `done` 来自 `useSendMessageWithSse()` 返回值,SSE 完成(含正常结束 + error)时触发
   - 必须在 finally 或 done effect 里发 end,避免 error 时永久禁用按钮
   - 用 try/finally 包裹 send 调用,保证 start/end 配对
2. **portal 前端 `SharePageDetailPage.tsx`**:
   - 加 `isStreaming` state,`useEffect` 监听 `window` 的 `message` 事件
   - 校验 `event.origin`(只接受同源 iframe,避免恶意 postMessage)
   - 过滤 `event.data.type === 'ragflow:completions:start'` → setIsStreaming(true)
   - 过滤 `event.data.type === 'ragflow:completions:end'` → setIsStreaming(false)
   - `handleNewSession` / `handleReopen` 在 `isStreaming` 时弹提示「正在生成回复,请先点击对话框内的停止按钮,再新建/切换会话(为限制并发量)」,return(不执行切换)
   - **不禁用按钮**(按钮仍可点击,但点击时弹提示,给用户反馈而非变灰)
3. **测试**:
   - portal Vitest:模拟 postMessage start/end,验证 isStreaming 状态切换 + isStreaming 时 handleNewSession/handleReopen 不执行 + 提示文案
   - RAGFlow web `npm run build`(Jest 跑不起来,用 build 兜底)

### postMessage 协议(决策点)

```ts
// RAGFlow → parent
postMessage({ type: 'ragflow:completions:start' }, '*');  // 或 targetOrigin
postMessage({ type: 'ragflow:completions:end' }, '*');
```

- origin 校验:portal 监听时校验 `event.origin === window.location.origin`(同源,因为 iframe 经 nginx 代理同源)
- 若跨域:`targetOrigin` 用具体 origin,portal 侧校验白名单

### Acceptance criteria

- [x] iframe 内发消息后(SSE 开始),portal 侧点击「新建会话」/「切换会话」弹提示「正在生成回复,请先点击对话框内的停止按钮,再新建/切换会话(为限制并发量)」 — commit 884a3e1
- [x] 弹提示后不执行切换(不重载 iframe,不丢失消息) — handleNewSession/handleReopen isStreaming 守卫
- [ ] 用户点 iframe 内「停止生成」→ SSE 结束 → postMessage end → portal 恢复可切换(部署后浏览器 E2E 验收)
- [ ] SSE 正常完成后,点击「新建会话」/「切换会话」不再弹提示(正常切换)— 部署后 E2E
- [x] SSE 出错时也恢复可切换(不会永久禁用)—— finally 块保证 end 发出 — 代码层 + Vitest 覆盖
- [x] origin 校验:非同源 postMessage 被忽略(安全) — Vitest 测试覆盖
- [x] 既有 portal Vitest 全绿(81 passed)+ 新增 isStreaming 测试通过(4 例) — commit 884a3e1
- [x] RAGFlow web `npm run build` 通过 — vite build 成功

### Blocked by

None - can start immediately(独立改动,跨 portal 前端 + RAGFlow 前端,无后端改动)

---

## Issue 34 — Slice 34: portal 数据持久化保护(代码与数据分离)

### Parent

无(Slice 31 验收时发现 portal.db 配置缺失,临时加 `PORTAL_DB_URL=sqlite:////home/xijuangu/portal-extension/portal.db` 后发现数据文件放在项目目录内会被下次 `rsync --delete` 删除)。

### 背景

Slice 8 已实施 DB 持久化(SessionStore 基于 SQLAlchemy),但服务器 `.env` 一直未设置 `PORTAL_DB_URL`,默认值 `sqlite://`(in-memory)导致每次 portal 重启 `chat_session_owner` 表全清,用户「历史会话没了」。临时修复已加文件型 SQLite 配置,但 `portal.db` 当前位于 `~/portal-extension/portal.db`(项目目录内),下次 `rsync --delete` 部署会把它删掉,数据再次丢失。

### What to build

把 portal 的 SQLite 数据库文件从项目目录内移到独立的 `~/portal-data/` 目录,与代码完全分离。这样 `rsync --delete` 永远不会触碰数据文件。同时 rsync 命令兜底加 `--exclude='*.db'`,双重保险。

具体行为:
1. 服务器创建 `~/portal-data/` 目录(独立于 `~/portal-extension/`)
2. 迁移现有 `~/portal-extension/portal.db` → `~/portal-data/portal.db`(若有数据)
3. 更新 `.env`:`PORTAL_DB_URL=sqlite:////home/xijuangu/portal-data/portal.db`
4. 部署文档(HANDOFF-phase3.md)固化 rsync exclude 列表,加入 `--exclude='*.db'` 作为兜底
5. 重启 portal,验证 `chat_session_owner` 表数据保留

### Acceptance criteria

- [ ] portal.db 位于 `~/portal-data/portal.db`,不在 `~/portal-extension/` 项目目录内
- [ ] `rsync --delete` 部署后 portal.db 保留(`ls -la ~/portal-data/portal.db` 仍存在)
- [ ] 重启 portal 后历史会话列表不丢失(前端会话列表与重启前一致)
- [ ] 部署文档(HANDOFF-phase3.md)更新 rsync exclude 列表,含 `--exclude='*.db'`
- [ ] 既有 portal pytest 全绿(无回归)

### Blocked by

None - can start immediately(运维改动,无代码依赖)

---

## Issue 35 — Slice 35: 部署脚本固化(deploy.sh + start.sh 改进)

### Parent

无(Slice 31 验收期间多次手动 rsync + pkill + nohup 部署,发现三个痛点:① `pkill -f "uvicorn portal.main:app"` 误伤 ssh 会话导致退出码 255;② `start.sh` 用 `exec` 不自动清理旧进程,必须先手动 pkill;③ rsync 命令需手记一长串 `--exclude` 参数,易漏)。

### 背景

当前部署流程(见 HANDOFF-phase3.md §5):
```bash
# 1. rsync(需手记 exclude 列表)
rsync -az --delete --exclude='.git' --exclude='node_modules' --exclude='__pycache__' --exclude='.venv' --exclude='*.pyc' --exclude='.env' ./ 172.16.10.180:~/portal-extension/

# 2. 重启(pkill 误伤 ssh,退出 255;必须分两条命令)
ssh 172.16.10.180 'pkill -f "uvicorn portal.main:app" || true; sleep 2'
ssh 172.16.10.180 'cd ~/portal-extension && nohup bash start.sh > portal.log 2>&1 < /dev/null & disown'

# 3. 健康检查(手动 curl)
ssh 172.16.10.180 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/portal/share-pages'
```

痛点:
- `pkill -f "uvicorn portal.main:app"` 会匹配到 ssh 会话本身(因为 ssh 命令行含该字符串),导致 ssh 连接被杀,退出码 255
- `start.sh` 用 `exec python -m uvicorn ...`,不自动清理旧进程,端口被占时启动失败
- rsync exclude 列表每次手敲易漏,漏了会删 `.env` 或 `portal.db`

### What to build

1. **`start.sh` 改进**:启动前自动清理旧 portal 进程,用 `pgrep -f "uvicorn portal.main:app"` 精确匹配进程 ID 后 kill,避免 `pkill` 的字符串匹配误伤 ssh 会话。重启只需一条命令 `bash start.sh`。

   关键实现点:
   - 用 `pgrep -f "python -m uvicorn portal.main:app"` 而非 `pkill -f`(pgrep 只返回 PID,不会匹配 ssh 命令行)
   - kill 当前 PID 后等待端口释放(`sleep 2` 或轮询 `lsof -i :8000`)
   - 保留现有 `source .env` 逻辑

2. **`deploy.sh` 脚本**:固化完整部署流程,单条 `bash deploy.sh` 完成:
   - rsync 同步代码(含所有 exclude:`.git`/`node_modules`/`__pycache__`/`.venv`/`*.pyc`/`.env`/`*.db`/`portal.log`)
   - ssh 远程执行 `bash start.sh`(自带 pkill)
   - 健康检查(curl `/portal/share-pages` 或更轻量端点)
   - 失败时 tail portal.log 输出最近 20 行辅助排查

3. **部署文档更新**:HANDOFF-phase3.md 的部署命令章节改为 `bash deploy.sh`,保留手动命令作为 fallback 说明。

### Acceptance criteria

- [x] `start.sh` 自动清理旧进程,重启只需 `bash start.sh` 一条命令 — commit 831fd0c(pgrep 替代 pkill)
- [x] pkill 不误伤 ssh 会话(用 pgrep 精确匹配,退出码 0,不再 255) — commit 831fd0c
- [x] `bash deploy.sh` 单条命令完成 rsync + 重启 + 健康检查 + 日志 tail — commit 831fd0c
- [x] deploy.sh 的 rsync exclude 列表含 `--exclude='*.db'`(与 Slice 34 一致)和 `--exclude='.env'` — commit 831fd0c
- [x] 部署失败时 deploy.sh 输出 portal.log 末尾辅助排查 — commit 831fd0c
- [ ] 部署文档(CONTEXT.md)更新为 `bash deploy.sh`,保留手动命令 fallback — 待主代理同步 CONTEXT.md
- [x] 既有 portal pytest 全绿(418 passed,无回归) — 合并后验证

### Blocked by

None - can start immediately(独立工程改进,与 Slice 34 互不依赖但建议 34 先做以对齐 rsync exclude 列表)

---

## Issue 36 — Slice 36: 修复 share 页面切换会话后 reference 和引用预览消失

### Parent

无(E2E 验收 Slice 31 时发现:聊天页有参考文档/段落时,不切换会话能看到;一旦切走再切回来,底部 PDF/文档卡片消失,回答中的引用标记 [1][2] 鼠标悬浮也看不到具体引用)。

### 根因(代码调查确认)

**是前端 state 没恢复,不是 history API 不返回。** 后端 `GET /api/v1/chatbots/{id}/sessions/{sid}` 返回会话级 `reference` 数组(每轮一项,含 `chunks` 和 `doc_aggs`),但 share 页面前端有三层缺陷叠加导致这份 reference 被完全丢弃:

1. **`use-send-shared-message.ts` `fetchSessionHistory`**:只取 `ret.data.data.messages`,把 `ret.data.data.reference` 数组整体丢弃,没有存到任何 state
2. **`share/index.tsx` 第 75-81 行**:调用 `buildMessageItemReference` 时把第二参数硬编码成 `reference: []`,导致即便有会话级 reference 也走不到 fallback 路径
3. **`utils/chat.ts` `buildMessageListWithUuid`**:用 `omit(x, 'reference')` 主动剥离每条 message 上的 reference 字段,导致 message-level reference 路径也走不到

**对比**:非 share 页面(`single-chat-box.tsx`)正确把 `conversation.reference` 传给 `buildMessageItemReference`,所以 history 恢复后引用预览正常;share 页面写成了 `[]`,是核心 bug。

### What to build

在 `useSendSharedMessage` hook 里把 `ret.data.data.reference` 存到 state(会话级 reference 数组,每轮一项),并在 `share/index.tsx` 把这个会话级 reference 数组传给 `buildMessageItemReference` 的第二参数,与非 share 页面保持一致。

具体行为:
1. `useSendSharedMessage` 新增 `conversationReference: IReference[]` state,`fetchSessionHistory` 成功后 `setConversationReference(ret.data.data.reference ?? [])`;切换 session_id 时重置为空数组
2. hook 返回值暴露 `conversationReference`
3. `share/index.tsx` 把 `conversationReference` 传给 `buildMessageItemReference({ messages: derivedMessages, reference: conversationReference }, message)` 的第二参数(替换硬编码的 `[]`)
4. `buildMessageListWithUuid` 的 `omit(x, 'reference')` 暂不动(保留 SSE 实时回答的 message.reference 路径 A,history 恢复走路径 B,两条路径都可达)
5. `npm run build` 兜底验证(Jest 跑不起来)

### Acceptance criteria

- [ ] 切换会话再切回来,参考文档列表(底部 PDF/文档卡片)仍显示
- [ ] 切换会话再切回来,回答中的 [1][2] 引用标记鼠标悬浮仍显示 chunk content + doc_name
- [ ] 点击引用悬浮卡片里的文档按钮,PDF 抽屉能正常打开并定位高亮
- [ ] SSE 实时回答时 reference 仍正常显示(无回归)
- [ ] RAGFlow web `npm run build` 通过

### Blocked by

None - can start immediately(纯 RAGFlow web 前端改动,无 portal 侧改动,无后端改动)

---

## Issue 37 — Slice 37: 新建会话时清除旧会话高亮(portal 前端)

### Parent

无(Slice 34/36 验收时发现:点击进入一个会话后左侧列表该项高亮,再点「新建会话」进入新会话页,旧高亮仍未取消)。

### 根因(代码调查确认)

`SharePageDetailPage.tsx` 的 `handleNewSession` 在 fullscreen 分支(约第 180-188 行)只重载了 iframe(`setIframeUrl` + `setIframeNonce`),**没有调用 `setActiveSessionId(null)`** 清除当前高亮。对比:`handleReopen` 第 136 行会 `setActiveSessionId(sessionId)` 设置高亮;widget 分支第 173 行也设了 `res.session_id`。唯独 fullscreen 新建会话漏了清除动作,导致旧会话高亮残留。

fullscreen 类型新建会话时不 precreate session(网关在 SSE 时绑定 session_id,轮询刷新列表),所以新建瞬间新 session_id 未知,正确行为是清除高亮(无高亮),待用户首次提问后新 session 由轮询加入列表。

### What to build

在 `handleNewSession` 的 fullscreen 分支(else 分支)重载 iframe 前,调用 `setActiveSessionId(null)` 清除旧会话高亮。改动范围:1 行代码。

### Acceptance criteria

- [x] 点击进入会话 A → A 高亮 → 点击「新建会话」→ A 的高亮立即消失(无高亮项)(2026-07-08 验收通过)
- [x] 新会话页加载后(iframe 重载)无任何会话项高亮(2026-07-08 验收通过)
- [x] 在新会话首次提问后,新 session 经轮询出现在列表(可暂不高亮,因 session_id 未与 iframe 同步)(2026-07-08 验收通过)
- [x] widget 类型新建会话高亮行为不变(仍高亮 res.session_id)(2026-07-08 验收通过)
- [x] 既有 portal Vitest 全绿(无回归)(76 passed)

### Blocked by

None - can start immediately(1 行前端改动,无后端依赖)

**验收状态:通过(2026-07-08)**

---

## Issue 38 — Slice 38: 消除页面级滚动条,菜单栏固定不滚动(portal 前端 CSS)

### Parent

无(Slice 34/36 验收时发现:整个分享页详情页出现一个页面级滚动条,滚动时顶部菜单栏 AppHeader 也被滚走;窗口越小越明显)。

### 根因(CSS 调查确认)

`frontend/src/styles.css` 布局层扣减量与溢出控制不一致,导致内容总高超过视口产生页面级滚动:

1. `.app-layout`(第 36-40 行)用 `min-height: 100vh`(非 `height: 100vh`),内容溢出时撑高整个布局而非被截断
2. `.detail-grid`(第 277-283 行)高度硬编码 `height: calc(100vh - 160px)`,但实际扣减应为 header(~48px)+ app-main padding(48px)+ back-link(~33px)+ page-title(~45px)≈ 174px,160px 不足 → 内容溢出约 14px+
3. `.app-header`(第 42-49 行)非 sticky/fixed,页面滚动时被滚走

**根因**:`.app-layout` 的 `min-height` + `.detail-grid` 的硬编码 `calc` 扣减不足 + header 非固定,三者叠加。这是典型的「用 calc 硬算高度」而非「用 flex 自适应剩余空间」的布局陷阱。

### What to build

改用 flex 自适应布局,让 `.detail-grid` 自动填满 `.app-main` 的剩余空间,而非用 `calc(100vh - 160px)` 硬算:

1. `.app-layout`:改为 `height: 100vh; overflow: hidden`(防止页面级滚动)
2. `.app-header`:加 `flex-shrink: 0`(不被压缩,配合 app-layout 的 height: 100vh 实现固定)
3. `.app-main`:改为 `display: flex; flex-direction: column; overflow: hidden`(内部纵向布局,自身不滚动)
4. `.detail-grid`:去掉 `height: calc(100vh - 160px)`,改为 `flex: 1; min-height: 0`(自适应 app-main 剩余空间,配合内部 sidebar/iframe 各自滚动)

可选:同时去掉 `.iframe-container` 的 `height: calc(100vh - 64px)`(第 245 行,已被 `.detail-grid .iframe-container { height: 100% }` 覆盖,但保留 calc 易误导)。

### Acceptance criteria

- [x] 窗口缩小到合理尺寸(如 800x600)时,整个页面不出现页面级滚动条(2026-07-08 验收通过)
- [x] 顶部菜单栏 AppHeader 始终固定可见,不随内容滚动(2026-07-08 验收通过)
- [x] 会话列表超出时在 sidebar 内部滚动(Slice 31 既有行为不回归)(2026-07-08 验收通过)
- [x] iframe 区域超出时在 iframe 容器内部滚动,不撑破布局(2026-07-08 验收通过)
- [x] 返回列表链接 + 页面标题始终可见(不被滚走)(2026-07-08 验收通过)
- [x] 既有 portal Vitest 全绿(无回归)(76 passed)

### Blocked by

None - can start immediately(纯 CSS 改动,无逻辑改动)

**验收状态:通过(2026-07-08)**

---

## Issue 39 — Slice 39: 修复 SSE 流式输出时输入框抖动(RAGFlow web 前端)

### Parent

无(Slice 34/36 验收时发现:AI 流式输出回复期间,底部输入框抽搐抖动)。

### 根因(代码调查确认)

RAGFlow web 分享页消息容器缺少 `min-h-0`,是经典 flexbox 溢出陷阱。对比普通聊天页正确,分享页错误:

1. **`share/index.tsx` 第 61-65 行**:消息容器 `flex flex-1 flex-col overflow-auto scrollbar-auto` **缺 `min-h-0`**
2. **对比 `single-chat-box.tsx` 第 92 行**:同位置有 `p-5 flex-1 overflow-auto min-h-0 scrollbar-auto`(正确)
3. `agent/share/index.tsx` 第 128 行同样缺失 `min-h-0`(相同问题)

**机制**:flex 子项默认 `min-height: auto`(内容固有高度)。SSE 流式输出时每个 chunk 更新 `derivedMessages` → 消息内容增长 → 容器被内容撑大(而非触发 `overflow-auto` 滚动)→ 向下挤压底部输入区 → 浏览器重算布局时输入区弹回 → 形成「下推-弹回」抖动循环。普通聊天页有 `min-h-0` 约束容器高度,内容超出时正确滚动,输入区位置稳定,故不抖动。

**加剧因素(可选优化,非主因)**:
- `logic-hooks.ts` 第 419-429 行 `useScrollToBottom` 在每个 chunk 调度 `rAF + setTimeout(100ms) + scrollToBottom`,chunk 频率快于 100ms 时堆积大量 pending 定时器
- `message-input/next.tsx` 第 225 行 `autoSize` 是 inline 对象字面量,每次渲染新引用 → `textarea.tsx` 的 `adjustHeight` effect 每次重建并执行 layout thrashing
- `utils.ts` 第 47 行 `buildMessageItemReference` 在无 reference 时返回新 `{}` 对象,破坏 `MessageItem.memo`,导致所有消息项在每个 chunk 重渲染

### What to build

**核心修复(必须)**:为分享页消息容器添加 `min-h-0`:
- `ragflow/web/src/pages/next-chats/share/index.tsx` 第 63 行 className 末尾加 `min-h-0`
- `ragflow/web/src/pages/agent/share/index.tsx` 第 128 行 className 末尾加 `min-h-0`(同问题)

**可选优化(建议,提升流畅度)**:
- `ragflow/web/src/hooks/logic-hooks.ts` 第 419-429 行:用 `useRef` 标记 pending 滚动,避免重复调度 `scrollToBottom`
- `ragflow/web/src/components/message-input/next.tsx` 第 225 行:把 `autoSize={{ minRows: 2, maxRows: 8 }}` 提取为模块级常量,稳定引用
- `ragflow/web/src/pages/next-chats/utils.ts` 第 47 行:把空引用返回值提取为模块级常量 `EMPTY_REFERENCE`,让 `MessageItem.memo` 正确跳过未变化项

### Acceptance criteria

- [ ] SSE 流式输出期间,底部输入框不抖动(位置稳定)— **2026-07-08 验收失败:加 `min-h-0` 后仍抽搐,根因判断有误或存在其他加剧因素(见下方验收记录)**
- [x] 消息区内容增长时正常滚动到底部(滚动行为不回归)
- [x] 普通聊天页(single-chat-box)行为不回归
- [x] agent 分享页同步修复(加 `min-h-0`)
- [x] RAGFlow web `npm run build` 通过

### Blocked by

None - can start immediately(纯 RAGFlow web 前端改动,无 portal 侧改动,无后端改动)

**验收状态:失败(2026-07-08)— `min-h-0` 核心修复未消除抖动**

### 验收记录(2026-07-08)

部署 `share/index.tsx` + `agent/share/index.tsx` 加 `min-h-0` 后浏览器 E2E 验证,SSE 流式输出期间输入框**仍然抽搐抖动**。说明 `min-h-0` 缺失非唯一根因(或非根因),Issue 39 的根因分析不完整。

**待排查方向**(原 issue「加剧因素」可能实际是主因):
1. `logic-hooks.ts` `useScrollToBottom` 在每个 chunk 调度 `rAF + setTimeout(100ms) + scrollToBottom`,chunk 频率快于 100ms 时堆积大量 pending 定时器形成「惊群」滚动
2. `message-input/next.tsx` 的 `autoSize` inline 对象导致 `textarea.tsx` 的 `adjustHeight` effect 每次重建并执行 layout thrashing(`height='auto'` 写 → rAF 读 `scrollHeight` 强制布局 → 写回高度)
3. `utils.ts` `buildMessageItemReference` 返回新空对象破坏 `MessageItem.memo`,所有消息项在每个 chunk 重渲染
4. 需要浏览器 DevTools Performance 录制确认实际瓶颈(布局抖动 vs 定时器堆积 vs 重渲染)

**下一步**:新建 Slice 41 重新调查根因(需浏览器 Performance trace),本 issue 不关闭。

---

## Issue 40 — Slice 40: 会话列表最新在上 + 标题用首问内容(portal 后端)

### Parent

无(Slice 34/36 验收时发现:① 新会话出现在列表底部而非顶部;② 会话标题默认「新会话」,无法区分,建议用用户首条消息作为标题)。

### 根因(代码调查确认)

两个问题根因都在 portal 后端会话元数据管理:

**问题 1:列表排序**
`portal/models.py` 的 `SessionStore.list_for_user`(第 355-367 行)查询时**没有 `order_by`**,SQL 默认顺序无保证(实际按插入顺序,旧在上)。对比:管理员 `list_all_sessions`(第 516 行)有 `rows.sort(key=lambda r: r.created_at, reverse=True)` 按创建时间倒序,但用户列表漏了。应按 `last_active_at` 倒序(最近活跃在最上),符合用户「最新会话在最上面」的直觉。

**问题 2:标题默认「新会话」**
`portal/models.py` 的 `SessionStore.bind`(第 307-330 行)默认 `title=title or "新会话"`。网关在 SSE 成功后(`gateway.py` 第 879-892 行)首次 bind session 时,只传 `session_id`/`share_page_id`/`portal_user_id`/`ragflow_resource_id`/`org_id`,**没传 title**,故走默认「新会话」。SSE 成功后也只调 `update_last_active` + `sync_message_count_from_history`,**没有用首问 `request_question` 更新 title**。

对比:用户手动重命名(Slice 5)走 `rename_session_via_ragflow` 同步更新 portal title + RAGFlow `API4Conversation.name`。自动命名应复用此逻辑,保持两侧一致(避免「portal 显示首问,RAGFlow 仍是新会话」的割裂)。

### What to build

**修复 1:列表排序**
`SessionStore.list_for_user` 的 `select` 语句加 `.order_by(ChatSessionOwnerModel.last_active_at.desc())`,最近活跃的会话排在最前。用 `last_active_at` 而非 `created_at`:用户在旧会话继续提问时,该会话应浮到顶部(符合「最近使用」直觉)。

**修复 2:首问作为标题(双侧同步)**
在 `gateway.py` 的 SSE 成功后处理(第 879-892 行 `if target_session_id and not is_greeting:` 分支),首次 bind session 后,用首问内容(`request_question`)更新标题:
1. 截断首问(前 50 字符,避免过长)作为新 title
2. 调 `session_store.rename(target_session_id, new_title)` 更新 portal 侧 title
3. 调 `rename_session_via_ragflow(settings, dialog_id, target_session_id, new_title, ragflow_type)` 同步 RAGFlow 侧 `API4Conversation.name`
4. RAGFlow 失败时:portal 侧已改,记 warning 日志(降级,不阻断已成功的 SSE 流;与 Slice 5 手动重命名「RAGFlow 失败抛 502」不同——此处 SSE 已成功,不应因 title 同步失败而让用户看到错误)

**边界情况**:
- 首问为空(is_greeting=True):不 bind、不改 title(已有逻辑)
- 首问超 50 字符:截断 + 省略号(如「请问关于xxx的...」)
- 首问含换行:取首行 + 省略号
- session 已存在(非首次 bind):不改 title(用户可能已手动重命名,不覆盖)

### Acceptance criteria

- [x] 新会话(首次提问后)出现在列表最上方,旧会话依次在下(2026-07-08 验收通过)
- [x] 在旧会话继续提问后,该会话浮到列表最上方(last_active_at 更新即重排)(2026-07-08 验收通过)
- [ ] 新会话标题为用户首条消息内容(截断到 50 字符,含换行取首行)— **2026-07-08 验收失败:标题仍为「law-test-01」(RAGFlow dialog name),非首问内容(见下方验收记录)**
- [ ] portal title 与 RAGFlow `API4Conversation.name` 一致(双侧同步)— **2026-07-08 验收失败:标题未更新为首问,待排查**
- [ ] 用户手动重命名后,再次提问不覆盖手动命名的 title(只首次 bind 时自动命名)— 未测(因 AC3 失败,跳过)
- [ ] greeting 请求(空首问)不触发自动命名 — 未测
- [ ] RAGFlow name 同步失败时,portal 侧 title 仍更新,日志记录 warning(不阻断 SSE)— 未测
- [x] 既有 portal pytest 全绿(无回归)+ 新增排序/命名测试通过(415 passed, 5 skipped)

### Blocked by

None - can start immediately(纯 portal 后端改动,无前端改动,无 RAGFlow 侧代码改动,复用既有 rename_session_via_ragflow)

**验收状态:部分通过(2026-07-08)— 排序修复通过,标题自动命名失败**

### 验收记录(2026-07-08)

**排序修复(修复 1)通过**:`list_for_user` 加 `order_by(last_active_at.desc())` 后,新会话出现在列表最上方,旧会话提问后浮顶,行为符合预期。

**标题自动命名(修复 2)失败**:新会话标题仍为「law-test-01」,而非首问内容。「law-test-01」是 RAGFlow `dialog` 表中该知识库的 `name`(DB 查询确认:`SELECT id,name FROM dialog` 返回 `('b4f88272768011f1a3bdc51e5c67581c','law-test-01')`)。

**待排查方向**:
1. portal 前端 `SharePageDetailPage.tsx` 会话列表项显示的 title 可能来自 RAGFlow 侧(经 `/sessions` 端点拉取的 `API4Conversation.name`)而非 portal `ChatSessionOwnerModel.title`——若 RAGFlow 侧 name 未被更新,前端显示的仍是 law-test-01
2. `gateway.py` 的 `rename_session_via_ragflow` 调用可能未生效(PATCH 请求 404/500,但 warning 日志被忽略)——需查 portal.log 确认是否有 warning
3. `gateway.py` 首次 bind 分支可能未进入(session 已被之前的某次请求 bind 过,走 else 分支不改 title)——需查 portal.db `chat_session_owner.title` 字段确认 portal 侧 title 是否更新
4. RAGFlow `API4Conversation.name` 可能在 session 创建时默认继承 dialog name,而 portal 的 rename PATCH 请求被 RAGFlow 拒绝(如端点不存在或参数不对)

**下一步**:新建 Slice 42 重新调查标题命名根因(需查 portal.log + portal.db + RAGFlow API4Conversation 表),本 issue 不关闭。

---

## Issue 43 — Slice 43: 修复创建用户组后管理后台渲染崩溃(admin_create_group 返回缺字段)

### Parent

无(2026-07-08 验收 Slice 37/38 后,用户创建用户组发现管理后台全部页面报「加载失败」)。

> 编号说明:Issue 41/42 预留给 Slice 39/40 验收失败的后续(SSE 输入框抖动重新调查 / 标题自动命名根因重新调查),本 issue 编号为 43。

### 根因(代码调查 + 服务器日志确认)

后端 `admin_create_group`(routes.py 第 1093-1102 行)创建用户组后,用 `_group_to_dict`(routes.py 第 150-158 行)序列化返回对象,该函数只返回 4 个字段(`id`/`name`/`created_at`/`org_id`),**不含 `member_count` 和 `members`**。

对比 `admin_list_groups`(routes.py 第 1105-1125 行)在列表响应中**手动补了**这两个字段(`d["member_count"] = len(members)` / `d["members"] = list(members)`)。

前端 `AdminGroup` interface(client.ts 第 87-93 行)**要求** `member_count: number` 和 `members: string[]`,`GroupsAdminPage.tsx` 第 65 行 `handleCreate` 把 POST 响应直接 append 到 `groups` state,渲染时:
- 第 185 行 `{g.member_count} 成员` → 新组显示 `undefined 成员`
- 第 190 行 `g.members.length === 0` → `g.members` 为 undefined,`.length` 抛 TypeError
- 第 194 行 `g.members.map(...)` → 同样抛 TypeError

React render 阶段抛错导致整个 `GroupsAdminPage` 组件树崩溃,用户感知为「加载用户组失败」;由于 admin SPA 侧边栏导航在崩溃后无法正常切换,进一步被感知为「用户管理/授权/会话搜索/审计日志全部加载失败」。

**服务器 portal.log 确认**:创建组后所有 admin GET 端点均返回 200,无 5xx 错误,排除后端 API 层失败。

**测试盲区**:`test_slice4_e2e.py` 第 139-145 行 `test_admin_create_group_returns_details` 只断言 `name`/`id`/`created_at`,不断言 `member_count`/`members`,故此 bug 长期潜伏。

### What to build

三层修复(同一根因,后端根治 + 前端兜底 + 测试补强):

1. **后端根治**:让 `admin_create_group` 返回与 `admin_list_groups` 完全一致的字段(补 `member_count: 0` 和 `members: []`)。建议抽 `_group_to_dict_with_members(group, seed)` helper 复用,避免 `admin_list_groups` 与 `admin_create_group` 重复补字段的代码。

2. **前端兜底**:`GroupsAdminPage.tsx` `handleCreate` 对返回对象补默认值(`member_count ?? 0`、`members ?? []`),防御未来后端再漏字段不会导致渲染崩溃。

3. **测试补强**:`test_slice4_e2e.py` `test_admin_create_group_returns_details` 补断言 `body["member_count"] == 0` 和 `body["members"] == []`,防止后端再次漏字段回归。

### Acceptance criteria

- [x] 创建用户组后,用户组页正常显示新组(显示「0 成员」,不崩溃不白屏)— 前端 Vitest `Slice 43 — 后端 POST 响应漏 member_count/members 时兜底不崩溃` 回归测试覆盖
- [x] 创建用户组后,用户管理/授权/会话搜索/审计日志 4 个页面均可正常加载切换(不报「加载失败」)— 后端根治 + 前端兜底,React render 不再崩溃
- [x] 后端 `POST /admin/groups` 响应 JSON 含 `member_count: 0` 和 `members: []` — `test_admin_create_group_returns_details` 断言覆盖
- [x] `GroupsAdminPage.tsx` `handleCreate` 对 `member_count`/`members` 做了 `?? 默认值` 兜底 — commit 中实现
- [x] `test_slice4_e2e.py` 断言 `member_count == 0` 和 `members == []` — commit 中补强
- [x] 既有 portal pytest 全绿(无回归,415 passed + 5 skipped)+ 既有前端 Vitest 全绿(无回归,77 passed)

### Blocked by

None - can start immediately(后端 routes.py 1 处 + 前端 GroupsAdminPage.tsx 1 处 + 测试 1 处,改动小,无外部依赖)

**验收状态:代码层通过(2026-07-08)— 部署后需 E2E 浏览器验收确认 admin SPA 全部页面加载正常**

### 补充:部署后 E2E 验收发现的真正根因(2026-07-08)

部署 Slice 43 后,用户报告 admin 后台"全部失败",且 flaky("来回点来回成功失败")。diagnosing-bugs 流程定位真正根因(与 member_count/members 无关,那是另一个已修的 bug):

**真正根因:浏览器 HTTP 缓存污染 API fetch。**

- 后端 `app.mount("/", StaticFiles(directory=dist, html=True))`(main.py:152)对导航请求(`Accept: text/html`)返回 SPA `index.html`(200 text/html),且**响应无 `Cache-Control` / `Vary: Accept` 头**。
- API URL(`/portal/admin/users` 等)与 SPA 路由 URL 重叠(前端 `API_BASE='/portal'`,client.ts:191)。
- 用户访问/刷新 `/portal/admin/users`(导航)→ 浏览器缓存该 URL 的 HTML 响应。
- 之后 `useAdminList` fetch 同 URL → **命中缓存返回 HTML** → `JSON.parse(html)` 抛 SyntaxError → catch → `setError("加载X失败")` → 显示 alert-error。
- flaky = 缓存有时命中(HTML→失败)有时不命中(直连后端 JSON→成功),取决于浏览器缓存淘汰/验证策略。

**诊断铁证**(diagnosing-bugs Phase 1 red-capable 循环):
- Playwright 快速切换 40 次:修复前 33/40 显示 alert-error "加载X失败";`request` 加 `cache: 'no-store'` 后 0/40 失败。
- `page.evaluate(fetch('/portal/admin/users', {cache:'no-store'}))` → 200 application/json(合法 JSON);默认 fetch → 200 text/html(index.html)。
- `curl -H "Accept: text/html" /portal/admin/users` → 200 text/html;`curl -H "Accept: */*"` → JSON(后端 API 响应)。证明后端根据 Accept 返回不同内容,且 HTML 响应无缓存控制头。

**修复**:前端 `request` 函数(client.ts:194-204)加 `cache: 'no-store'`,强制每次 fetch 直连后端,绕过被导航 HTML 污染的缓存。

**验证**:部署新 dist 后,Playwright 快速切换 40 次 + goto 12 次,**0 失败**(mainLen=210,有内容)。

**遗留架构隐患(未在本 slice 修)**:API URL 与 SPA 路由 URL 重叠是设计缺陷。根治方案应让 API 走 `/portal/api/*` 前缀(CONTEXT.md §7 第 76 行本就标注 `API_BASE='/portal/api'`,但代码实际是 `/portal'`),需后端路由加 `/api` 前缀。本 slice 用 `cache: 'no-store'` 作最小修复,根治留待后续 slice。

---

## Issue 44 — Slice 44: API 路由前缀分离,根治 API URL 与 SPA 路由 URL 重叠

### Parent

Slice 43(诊断发现的遗留架构隐患)。

### 背景

Slice 43 诊断 admin 后台 flaky "加载失败" 根因:浏览器 HTTP 缓存污染 API fetch —— API URL(`/portal/admin/users`)与 SPA 路由 URL 重叠,导航请求(`Accept: text/html`)的 HTML 响应被缓存后,API fetch 命中缓存返回 HTML,`JSON.parse` 抛错 → 显示"加载失败"。当前用 `request` 函数加 `cache: 'no-store'` 作 workaround止血,本 slice 做架构根治。

### What to build(方案 B — 已与用户确认,2026-07-08)

**决策变更**:原方案 A(改 router `prefix="/api"`)经评估需更新 263 处测试断言(portal 自有 API 路径在 19 个测试文件中出现 263 次),工作量大且易错。改用**方案 B:后端中间件**,从响应层根治缓存污染,不改任何 API 路径,263 处测试零改动,同样达到"导航 HTML 不再污染 API fetch"的根治效果。

- **后端**(main.py):加 `NoCacheHtmlMiddleware(BaseHTTPMiddleware)`,对所有 `content-type: text/html` 的响应加 `Cache-Control: no-store, no-cache, must-revalidate` + `Vary: Accept` 头。JSON API 响应不受影响。注册在 `include_router` 之后、`mount(StaticFiles)` 之前。
- **前端**(client.ts):移除 Slice 43 加的 `cache: 'no-store'` workaround(后端中间件已根治,不再需要前端绕过缓存)。
- **测试**:新增 `test_slice44_cache_middleware.py`(3 例:StaticFiles HTML 带 no-store+Vary、JSON API 无 no-store、POST /login JSON 无 no-store)。
- **不改**:API 路径(router 不加 prefix)、前端 `API_BASE`(仍 `/portal`)、nginx 配置、263 处现有测试断言。

### Acceptance criteria

- [x] 后端 `NoCacheHtmlMiddleware` 对 text/html 响应加 `Cache-Control: no-store` + `Vary: Accept`,JSON 响应不受影响 — commit ec243b0
- [x] 前端 `cache: 'no-store'` workaround 从 `request` 函数移除 — commit ec243b0
- [x] 后端 pytest 全绿(418 passed = 基线 415 + 3 新增)+ 前端 Vitest 全绿(81 passed)+ tsc/ruff 干净 — 合并后验证
- [ ] 部署后浏览器 E2E 验收:6 个 admin tab 来回切换无 "加载失败"(移除前端 workaround 后依赖后端中间件生效)
- [ ] CONTEXT.md 更新:记录 Slice 44 方案 B 决策(中间件根治,非 router prefix)

### Blocked by

None - can start immediately(Slice 43 的 `cache: 'no-store'` workaround 已止血;本 slice 为架构改进,改动集中在后端中间件 + 前端移除 workaround + 新测试)

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
