# RAGFlow 权限门户改造 — Issue 列表

> 由 `to-issues` 从 PRD 拆分,7 个 vertical slices(tracer bullets),每个端到端可验证。
> 本地无 issue tracker,以本地文件记录;迁移至正式 tracker 时每节对应一个 issue,标 `ready-for-agent`。
> 实施顺序:核心链路优先(Slice 1→2→3)→ 功能扩展(4→5→6)→ 测试固化(7)。

## 依赖图

```
Slice 1 (骨架/Tracer Bullet 1)
  ├─> Slice 2 (session 捕获/恢复)
  │     ├─> Slice 3 (继续/隔离/撤销)
  │     └─> Slice 5 (重命名/删除/双删) <─ Slice 3 完成
  └─> Slice 4 (CRUD) ──> Slice 6 (管理员/审计) <─ Slice 5 完成
                                              Slice 4 完成
Slice 7 (测试固化) <─ Slice 1-6 全部
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

## 迁移至正式 issue tracker 时的说明

- 每个 Issue 节对应一个 issue,标题用「Slice N: <描述>」。
- `ready-for-agent` 标签打在每个 issue 上。
- 发布顺序按依赖:Issue 1 → 2 → 3 → 4 → 5 → 6 → 7(后续可并行 4 与 2-3)。
- `Blocked by` 字段填上游 issue 编号。
- Issue body 直接复用本文档对应章节。
