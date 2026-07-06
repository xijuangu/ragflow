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

## 迁移至正式 issue tracker 时的说明

- 每个 Issue 节对应一个 issue,标题用「Slice N: <描述>」。
- `ready-for-agent` 标签打在每个 issue 上。
- Phase 1(Issue 1-7)已完成,迁移时标 `done` 或归档。
- Phase 2 发布顺序按依赖:Issue 8 → (9 并行) → (10/11 并行,依赖 9) → 12(依赖 8);可选项 13(依赖 8)/14(独立)/15(依赖 8)/16(依赖 9)按需发布。
- `Blocked by` 字段填上游 issue 编号。
- Issue body 直接复用本文档对应章节。
