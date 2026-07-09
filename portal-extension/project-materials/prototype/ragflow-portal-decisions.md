# RAGFlow 权限门户改造 — 需求决策文档

> 本文档由 `grill-with-docs` 访谈沉淀,覆盖身份、隔离、ACL、角色、会话能力、审计、数据保留、一期范围与运行位置共 10 项决策。
> 每项决策均区分「已由 RAGFlow v0.26.0 源码核实的事实」与「待验证假设(留给 prototype)」。
> 本文档不包含 PRD 或实现细节;关键技术假设由独立 prototype 会话验证。

## 背景

在保留 RAGFlow 原生问答界面与引用体验的前提下,为分享页增加用户/用户组 ACL,并让每个用户查看与继续自己的历史会话。一期优先全屏 Chat,验证核心链路后再扩展悬浮组件与 Agent。

## 决策清单

### D1 身份来源:门户自建账号体系

**结论:** 门户自建账号,用户表独立于 RAGFlow;RAGFlow 侧不感知具体用户。

**已验证事实(v0.26.0 源码):**
- RAGFlow 的 `tenant` 模型代表资源租户,一个租户持有一个 API Token,该 Token 是**租户级**而非用户级令牌(交接第 51 行)。
- RAGFlow 生成的 iframe URL 包含 `shared_id` 与租户 API Token,前端将 Token 写入浏览器 `localStorage`(交接第 51 行)。
- 因此不能把 RAGFlow 原始 iframe URL 直接交给最终用户;门户必须有独立的用户身份层。

**待验证假设(prototype):**
- 网关签发的短期嵌入令牌能否通过 URL 参数或 postMessage 注入 RAGFlow 前端,替代 `localStorage` 中的租户 Token 调用后端 SSE 与会话接口。

**理由:** RAGFlow 租户 Token 不是用户级令牌,身份层必须独立;自建账号最简单可控,OIDC/LDAP/飞书可在其上叠加认证后端而不影响数据模型。排除复用 RAGFlow 用户表,以免门户绑定内部表结构。

---

### D2 单租户,不预留 org_id

**结论:** 一期为单租户,门户只有一个全局用户池与一组分享页;`portal_user`、`share_page`、`share_page_grant`、`chat_session_owner` 均不加 `org_id`。

**已验证事实:**
- RAGFlow 租户 Token 是租户级资源令牌(交接第 51 行),单租户下网关只需持有一个 Token。
- 门户数据模型(交接第 113-137 行)未含 `org_id`,设计倾向单租户。

**待验证假设(prototype):**
- 单租户场景下网关持单一 RAGFlow 租户 Token 代理全部分享页的 SSE 与会话接口即可工作。

**理由:** 最小改造原则要求字段最少;一期原型验收 8 项全部为单租户语义。多租户可在未来加迁移,一期不预留字段以保持最小。

---

### D3 仅允许登录用户访问

**结论:** 所有分享页必须经门户登录 + ACL 校验,网关才签发嵌入令牌;无公开链接。

**已验证事实:**
- RAGFlow 原生 iframe URL 含租户 Token 并写入 `localStorage`(交接第 51 行),将其当公开链接分发会泄露租户 Token,威胁整个 RAGFlow 租户的所有资源。
- 「公开分享」不能通过直接分发 RAGFlow 原始 iframe URL 实现,必须由网关签发资源受限、可撤销、与具体分享页绑定的临时令牌。

**待验证假设(prototype):**
- 网关签发的临时令牌能否在 iframe 加载时注入 RAGFlow 前端并替代 `localStorage` 中的租户 Token(与 D1 同一验证点)。

**理由:** 公开分享引入匿名会话归属缺失、限流配额、审计缺口三类复杂度;一期原型验收要求 `session_id` 绑定到 `portal_user_id`,公开会话无主无法满足。`share_page` 不加 `is_public`;`chat_session_owner.portal_user_id` 设为 `NOT NULL`。

---

### D4 权限主体:用户 + 用户组(无部门树/有效期/审批)

**结论:** `share_page_grant.subject_type ∈ {user, group}`;新增 `portal_group` 与 `portal_group_member` 两张薄表;不做部门树、不做授权有效期、不做审批流。

**已验证事实:**
- 交接第 113-137 行门户数据模型已设计 `subject_type ∈ {user, group}`。
- RAGFlow 自身租户模型不提供用户组概念,门户必须自建用户组语义(租户令牌是租户级而非用户组级)。

**待验证假设(prototype):**
- 用户组授权的继承解析(用户 → 所属组 → 组的授权)在网关每次请求时实时计算的性能是否可接受(一期用户量小,假设可接受)。

**理由:** 交接第 146 行明确要求用户组管理为一期产品目标;用户组数据模型极轻,网关 ACL 解析一次 SQL join 即可。部门树引入递归继承与跨部门调岗迁移,复杂度非线性上升,与最小改造冲突。

**数据模型新增:**
```
portal_group(id, name, created_at)
portal_group_member(group_id, user_id, added_at)
```

---

### D5 管理员层级:两层(普通用户 + 平台管理员)

**结论:** 门户只设两层角色;`portal_user.is_admin` 布尔区分平台管理员与普通用户;不实现分享页管理员角色,但保留 `share_page_grant.permission=manage` 字段值供未来扩展。

**已验证事实:**
- RAGFlow 的 `tenant` 是资源租户而非管理员角色,不提供门户级用户角色体系,门户必须自建角色语义。
- 交接第 148 行要求管理员具备「搜索、查看和删除所有人的会话」与「必要的审计能力」,需平台管理员角色承载。

**待验证假设(prototype):**
- 若未来启用分享页管理员,其授权范围校验在网关与门户 API 上能否干净实现,且不在 RAGFlow 后端扩展接口上产生越权漏洞(一期不实现,仅预留字段值)。

**理由:** 两层只需一个布尔字段;分享页管理员引入 `share_page_admin` 指派表与逐项权限矩阵定义,与最小改造冲突。一期单租户、自建账号,平台管理员直接管理所有分享页可行。

**数据模型:**
```
portal_user(id, username, password_hash, email, is_admin, enabled, created_at)
audit_log(actor_user_id, action, target_type, target_id, at, meta_json)
```
`share_page_grant.permission ∈ {use, manage}` 保留,一期只解析 `use`。

---

### D6 用户会话能力:查看 + 继续对话 + 重命名 + 删除(不含导出,硬删除无回收站)

**结论:** 普通用户对自己会话可查看、继续对话、重命名、删除;不做导出;删除采用硬删除,不设回收站。

**已验证事实:**
- Chat iframe 后端使用 `API4Conversation` 模型(交接第 54 行),普通 Chat 会话 API 使用 `Conversation` 模型(交接第 53 行),两者表不同。
- 官方普通 Chat 会话 API **不能**直接列出/管理 Chat iframe 产生的历史记录(交接第 56 行);查看、重命名、删除必须通过 RAGFlow 最小后端扩展(为 `API4Conversation` 增加受控 list/get/rename/delete 接口)实现。
- 「继续对话」在 iframe 路径上已由 RAGFlow 原生 `async_iframe_completion` 支持,网关只需代理 SSE 并校验 `session_id` 归属。
- 消息正文、引用、文档信息以 `API4Conversation` 为唯一事实源(交接第 139 行),门户不复制正文。

**待验证假设(prototype):**
- RAGFlow 的 `API4Conversation` 是否已有「按 session_id 读取消息与引用片段」的内部接口可被最小后端扩展复用,还是需新写读取逻辑。
- 关闭页面后从「我的会话」重新打开能否完整恢复消息、引用片段、引用标记、PDF 预览(原型验收点 4-5)。
- 重命名能否落到 `API4Conversation` 字段,还是仅门户侧 `chat_session_owner.title` 承载显示标题。

**理由:** 交接第 87-89 行明确要求查看/继续/重命名/删除;导出涉及序列化与文件存储,与最小改造冲突;硬删除避免状态机复杂度。删除时双删(门户 `chat_session_owner` + RAGFlow `API4Conversation`)保持一致。

---

### D7 管理员会话访问与审计范围

**结论:**
- 7a 选 C:管理员默认只看会话元数据(标题/用户/时间/消息数);查看他人会话正文需二次确认(UI 按钮 + 写审计日志后再返回正文)。
- 7b 选 X:审计仅覆盖敏感操作,不全量记普通用户行为。

**已验证事实:**
- RAGFlow 原生不提供「管理员查看任意 `API4Conversation` 消息正文」的接口(交接第 54-56 行);管理员查看正文必须依赖最小后端扩展接口,且该接口必须强制按 `dialog_id/agent_id + session_id + 调用方身份` 校验。
- 消息正文以 `API4Conversation` 为唯一事实源(交接第 139 行),门户不复制正文;管理员查正文 = 通过网关调 RAGFlow 扩展接口,不绕过网关。

**待验证假设(prototype):**
- 管理员通过网关以 elevated 模式调 RAGFlow 扩展接口读取他人会话正文的渲染保真度。管理员查看路径可能不走 iframe 而走门户自建只读视图,需原型验证渲染保真度(至少消息 + 引用标记可见)。

**理由:** 交接第 150 行要求审计日志与访问留痕;纯看元数据削弱排查能力,直接看正文缺乏留痕。二次确认 UI 成本极低且留痕;审计仅记敏感操作避免噪音与存储负担。

**审计 `action` 枚举(最小化):**
`login_success | login_failure | grant_create | grant_revoke | session_delete | session_view_elevated | user_enable | user_disable`

---

### D8 数据保留与删除规则

**结论:**
- 8a:用户禁用保留会话(管理员仍可查);用户硬删除时级联硬删除其所有会话(门户 `chat_session_owner` + RAGFlow `API4Conversation` 双删)。
- 8b:审计日志永久保留,不自动清理。
- 8c:撤销分享页授权后立即拒绝继续会话(网关每次请求校验 grant 存在),历史会话保留,管理员仍可查。

**已验证事实:**
- 任何「删除会话」必须双删(门户 `chat_session_owner` + RAGFlow `API4Conversation`),否则出现门户已删但 RAGFlow 仍有残留正文的「幽灵会话」(交接第 139 行,正文唯一事实源)。
- 撤销授权 = 删除 `share_page_grant` 行;网关每次请求校验 grant 是否存在(交接第 93-94 行),「立即拒绝继续」是既有逻辑,无需额外设计。

**待验证假设(prototype):**
- 「双删」在 RAGFlow 后端扩展接口不可用或失败时的事务一致性策略:门户侧是否回滚,还是标记 `chat_session_owner.deleted_at` 待重试。事务边界设计留给原型实测。

**理由:** 禁用是可逆状态不应丢数据,硬删除是终态应清孤儿;审计日志体积小永久保留成本可忽略;撤销授权 ≠ 删用户数据,用户可能恢复授权,删会话不可逆且无必要。

---

### D9 一期范围:仅全屏 Chat

**结论:** 一期只覆盖 `embed_type=fullscreen` + `ragflow_type=chat`;字段保留但一期固定值,创建分享页时不开放选择器。

**已验证事实:**
- 全屏 iframe 与悬浮组件都用 RAGFlow 原生引用数据,悬浮组件也含引用片段弹层与 PDF 抽屉(交接第 49 行)——「保留原生引用」在两种嵌入类型下都成立。
- Chat iframe 用 `API4Conversation`(交接第 54 行),Agent iframe 与 Agent 会话 API 也用 `API4Conversation`(交接第 55 行)—— 底层会话模型相同,后端扩展接口可统一处理。
- 但前端渲染路径不同:全屏 iframe 入口是 `embed-dialog/index.tsx`,悬浮组件入口是 `floating-chat-widget.tsx`(交接第 38-40 行),令牌注入与 session_id 加载方式可能不同。

**待验证假设(prototype):**
- 网关向全屏 iframe 注入「短期嵌入令牌 + 已有 session_id」的方式(URL 参数 vs postMessage vs cookie)是否被 `embed-dialog/index.tsx` 接受(原型验收点 1-3)。
- 同样注入方式是否对 `floating-chat-widget.tsx` 有效 —— 未验证,这是把悬浮排除出一期的关键不确定性。
- Agent iframe 的会话恢复与引用渲染是否与 Chat iframe 一致(底层 `API4Conversation` 相同但 Agent 可能含多步、工具调用)—— 未验证。

**理由:** 交接第 27 行已明确全屏 Chat 为一期优先;Agent 与悬浮各有未验证的前端注入路径,同时调试违反「先验证核心假设」。全屏 Chat 跑通后,Agent 与悬浮可增量验证,因底层会话模型与后端扩展接口已就位。

---

### D10 运行位置:仅门户内,同源

**结论:** iframe 只能在门户域名下加载,网关与门户同源,登录态走门户 cookie/session;固定 `X-Frame-Options: SAMEORIGIN`,不维护外部域名白名单。

**已验证事实:**
- RAGFlow 原生 iframe URL 含租户 Token 写入 `localStorage`(交接第 51 行),无论运行在哪都不能直接暴露原始 URL。
- 第三方 cookie 限制:Chrome、Safari、Firefox 默认阻止跨站 cookie,门户登录 cookie 无法在第三方 iframe 内自动携带(已知浏览器安全约束,非 RAGFlow 特有)。

**待验证假设(prototype):**
- 网关签发的短期嵌入令牌通过 URL 参数注入 iframe 后,RAGFlow 前端能否用它替代 `localStorage` 中的租户 Token(与 D1/D3 同一验证点,无论运行位置都必须验证)。
- 选 A(同源)则无需验证第三方站点下的 CORS/`X-Frame-Options`/CSP 阻断;若未来选 B/C 才需额外验证 SSE 跨域。

**理由:** 第三方嵌入引入第三方 cookie 阻断、令牌泄露、frame-ancestors 白名单管理、SSE 跨域适配四类复杂度,与最小改造冲突。同源下登录态直接复用门户 cookie,网关与 iframe 同域无 frame-ancestors 管理,SSE 同源无跨域适配。

---

## 一期数据模型汇总

```text
portal_user
- id
- username
- password_hash
- email
- is_admin              # 布尔,平台管理员 vs 普通用户
- enabled               # 布尔,禁用=保留会话但拒绝登录
- created_at

portal_group
- id
- name
- created_at

portal_group_member
- group_id
- user_id
- added_at

share_page
- id
- name
- ragflow_type          # 一期固定 chat,字段保留
- ragflow_resource_id
- embed_type            # 一期固定 fullscreen,字段保留
- enabled

share_page_grant
- share_page_id
- subject_type          # user / group
- subject_id
- permission            # use / manage(一期只解析 use)

chat_session_owner
- session_id
- share_page_id
- portal_user_id        # NOT NULL
- ragflow_resource_id
- title                 # 用户可读标题(重命名落点)
- created_at
- last_active_at

audit_log
- actor_user_id
- action                # login_success | login_failure | grant_create | grant_revoke | session_delete | session_view_elevated | user_enable | user_disable
- target_type
- target_id
- at
- meta_json
```

门户只保存身份、权限与会话归属映射;消息正文、引用与文档信息继续以 RAGFlow 的 `API4Conversation` 为唯一事实源。

## 一期架构(沿用交接第 60-76 行)

```text
用户
  -> 权限门户(同源)
     - 登录、用户、角色、用户组
     - 分享页 ACL(用户/用户组)
     - 我的历史会话(查看/继续/重命名/删除)
     - 管理员会话与审计后台(分级查看 + 敏感操作审计)
  -> 嵌入访问网关(同源,X-Frame-Options: SAMEORIGIN)
     - 校验用户与分享页授权
     - 签发短期、可撤销、资源受限嵌入令牌
     - 隐藏真正的 RAGFlow API Token
     - 代理流式 SSE 并校验 session_id 归属
     - 双删会话(门户 + RAGFlow API4Conversation)
  -> RAGFlow 原生 iframe(全屏 Chat)
     - 保留回答、引用片段、引用标记、PDF 预览
  -> RAGFlow(单租户,单一租户 Token)
```

## 已否决方案(不再讨论)

- 不用 Open WebUI 替代 RAGFlow 聊天界面(交接第 31 行)。
- 不复用 RAGFlow 内置用户表做身份源(D1)。
- 不做多租户、不预留 org_id(D2)。
- 不做公开分享(D3)。
- 不做部门树、授权有效期、审批流(D4)。
- 不做分享页管理员角色(D5,仅预留字段值)。
- 不做导出、不设回收站(D6)。
- 不做全量行为审计(D7)。
- 不做撤销授权即删会话(D8)。
- 不做悬浮组件、Agent(D9,延后)。
- 不做第三方嵌入与外部域名白名单(D10)。
