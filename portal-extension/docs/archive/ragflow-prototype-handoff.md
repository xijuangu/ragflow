# RAGFlow 权限门户改造 — 原型会话交接

> 本文档由 `grill-with-docs` 访谈后整理,供独立 prototype 会话验证关键技术假设。
> 原型范围严格限定为**一个全屏 Chat 链路**,不涉及悬浮组件、Agent、多租户、公开分享。
> 决策依据见 `/tmp/ragflow-portal-decisions.md`。

## 原型目标

验证「全屏 Chat + 网关签发短期令牌 + 历史会话恢复 + 原生引用保留 + 用户隔离 + 撤销即失效」核心链路。八项验收点全部通过后才进入 PRD。

## 已验证事实(无需再验证,直接采用)

以下结论已由 RAGFlow v0.26.0 源码核实,原型直接基于这些事实构建,不必重复确认:

1. **iframe URL 含租户 Token:** RAGFlow 生成的 iframe URL 包含 `shared_id` 与租户 API Token,前端将 Token 写入浏览器 `localStorage`(源码:`web/src/components/embed-dialog/index.tsx`)。
2. **租户 Token 是租户级:** 该 Token 代表租户,不是限定某个用户或某个分享页面的令牌。不能把 RAGFlow 原始 iframe URL 直接交给最终用户。
3. **仅外层登录保护不够:** 用户仍可能复制 iframe URL 或直接调用后端接口,因此必须有嵌入网关校验。
4. **Chat iframe 用 API4Conversation:** Chat iframe 的 `async_iframe_completion` 使用 `API4Conversation` 模型(源码:`api/apps/restful_apis/bot_api.py`、`api/db/services/conversation_service.py`)。
5. **普通 Chat 会话 API 用 Conversation:** 普通 Chat 会话 API 使用 `Conversation` 模型(源码:`api/apps/restful_apis/chat_api.py`),与 iframe 会话表不同。
6. **官方普通 Chat 会话 API 不能管理 iframe 历史:** 因此查看/重命名/删除必须通过 RAGFlow 最小后端扩展(为 `API4Conversation` 增加受控 list/get/rename/delete 接口),不能假设现有 API 已覆盖。
7. **正文唯一事实源:** 消息正文、引用片段、文档信息以 `API4Conversation` 为唯一事实源;门户不复制正文,删除会话必须双删(门户 `chat_session_owner` + RAGFlow `API4Conversation`)。
8. **悬浮组件也含引用渲染:** 全屏 iframe 与悬浮组件都用 RAGFlow 原生引用数据,悬浮组件也含引用片段弹层与 PDF 抽屉(源码:`web/src/components/floating-chat-widget.tsx`、`floating-chat-widget-markdown.tsx`)—— 但一期不验证悬浮。
9. **Agent 与 Chat 底层会话模型相同:** Agent iframe 与 Agent 会话 API 也用 `API4Conversation`(源码:`api/apps/restful_apis/agent_api.py`)—— 但一期不验证 Agent。
10. **RAGFlow tenant 是资源租户:** 不提供门户级用户角色体系,门户必须自建身份与角色。

## 待验证假设(必须由 prototype 实测,按风险排序)

### H1 [最高风险] 网关短期令牌能否替代 localStorage 中的租户 Token

**假设:** 网关签发的短期、可撤销、资源受限嵌入令牌,能通过 URL 参数或 postMessage 注入 RAGFlow 前端(`embed-dialog/index.tsx`),使前端用它替代 `localStorage` 中的租户 Token 调用后端 SSE 与会话接口。

**失败影响:** 若 RAGFlow 前端不接受外部注入令牌,整个「隐藏真正 API Token」架构不成立,需重新设计(可能需修改 RAGFlow 前端源码或自建前端代理层)。

**验证方式:**
- 构造一个网关签发的令牌,通过 URL 参数传给 iframe。
- 观察 RAGFlow 前端是否用该令牌发起 SSE 请求,而非读取 `localStorage` 中的租户 Token。
- 若 URL 参数无效,尝试 postMessage 注入。
- 记录哪种注入方式有效,或全部无效。

### H2 [高风险] 按 session_id 加载已有消息与引用片段

**假设:** RAGFlow 的 `API4Conversation` 已有或可低成本增加「按 session_id 读取消息与引用片段」的接口,供最小后端扩展复用。

**失败影响:** 若无现成接口且读取逻辑复杂,「关闭页面后重新打开恢复历史」无法实现,验收点 4-5 失败。

**验证方式:**
- 查阅 `api/db/services/conversation_service.py` 与 `bot_api.py`,确认 `API4Conversation` 是否有按 session_id 读取消息的方法。
- 若有,通过网关调用验证返回消息正文 + 引用片段 + 引用标记。
- 若无,评估新写读取逻辑的成本。

### H3 [高风险] 历史会话完整恢复(消息 + 引用标记 + PDF 预览)

**假设:** 关闭页面后从「我的会话」重新打开 iframe 并传入已绑定的 session_id,能完整恢复消息、引用片段、引用标记与 PDF 预览,体验与首次对话一致。

**失败影响:** 验收点 4-5 失败;原生引用体验未保留,违反核心硬约束。

**验证方式:**
- 首次对话产生含引用的回答。
- 捕获 session_id,关闭 iframe。
- 用该 session_id 重新加载 iframe,验证引用标记可点击、引用片段弹层正常、PDF 抽屉可打开。

### H4 [中风险] 旧 session_id 上继续提问流式响应正常

**假设:** 在已绑定的旧 session_id 上继续提问,RAGFlow 的 `async_iframe_completion` 流式响应正常,且新消息追加到同一会话。

**失败影响:** 验收点 6 失败;「继续对话」能力不成立。

**验证方式:**
- 在 H3 恢复的会话上继续提问,观察 SSE 流是否正常、回答是否追加到同一 session_id。

### H5 [中风险] session_id 归属隔离

**假设:** 用户 A 不能访问用户 B 的 session_id;网关校验 `chat_session_owner.portal_user_id == 当前用户` 后才允许加载/继续。

**失败影响:** 验收点 7 失败;用户隔离不成立,安全漏洞。

**验证方式:**
- 用户 A 创建会话得到 session_id_A。
- 用户 B 登录后尝试用 session_id_A 加载 iframe,验证网关拒绝。
- 用户 B 尝试直接调 RAGFlow 后端接口用 session_id_A,验证网关代理路径拒绝(因令牌不匹配)。

### H6 [中风险] 撤销授权后立即失效

**假设:** 删除 `share_page_grant` 行后,用户已有的 iframe 与历史链接立即失效,无法继续会话。

**失败影响:** 验收点 8 失败;授权撤销不生效,安全漏洞。

**验证方式:**
- 用户登录并加载分享页 iframe,正常对话。
- 管理员撤销该用户对该分享页的授权。
- 用户在已打开的 iframe 上继续提问,验证网关拒绝(因 grant 不存在)。
- 用户刷新 iframe,验证网关拒绝签发新令牌。

### H7 [低风险] 重命名落点

**假设:** 重命名可落到 `API4Conversation` 的字段;若不可写,则仅门户侧 `chat_session_owner.title` 承载显示标题,RAGFlow 原生 iframe 内显示原名。

**失败影响:** 不阻塞核心链路;最坏情况是「我的会话」列表标题与 iframe 内标题不一致。

**验证方式:**
- 查阅 `API4Conversation` 模型字段,确认是否有 name/title 字段可更新。
- 若有,通过后端扩展接口更新并验证 iframe 内标题变化。
- 若无,确认仅门户侧 title 用于列表显示可接受。

### H8 [低风险] 双删事务一致性

**假设:** 删除会话时双删(门户 `chat_session_owner` + RAGFlow `API4Conversation`);若 RAGFlow 侧删除失败,门户侧回滚或标记 `chat_session_owner.deleted_at` 待重试。

**失败影响:** 不阻塞核心链路;最坏情况是出现少量「幽灵会话」(门户已删但 RAGFlow 残留),需运维清理。

**验证方式:**
- 模拟 RAGFlow 后端扩展接口不可用,观察门户侧事务行为。
- 评估回滚 vs 标记重试的实现成本,选定策略。

### H9 [低风险] 管理员 elevated 查看他人正文渲染

**假设:** 管理员通过网关以 elevated 模式调 RAGFlow 扩展接口读取他人会话正文,渲染保真度可接受(至少消息 + 引用标记可见)。

**失败影响:** 不阻塞核心链路;最坏情况是管理员查看正文时渲染不完整,需改走 iframe 模式或简化只读视图。

**验证方式:**
- 管理员查看另一用户的会话正文。
- 验证消息正文与引用标记可见;PDF 预览可选(若不完整,一期可只保证消息 + 引用标记)。

## 原型验收点(8 项,来自交接第 154-166 行)

原型必须完整跑通以下 8 项,任一未通过则不进入 PRD:

1. ✅ 用户登录并获得某个分享页权限。
2. ✅ 网关不给浏览器暴露 RAGFlow 真正的 API Token。
3. ✅ 新建会话后捕获并绑定 `session_id` 到 `chat_session_owner`。
4. ✅ 关闭页面后从「我的会话」重新打开(传 session_id 加载 iframe)。
5. ✅ 完整恢复消息、引用片段、引用标记和 PDF 预览。
6. ✅ 在旧 `session_id` 上继续提问,流式响应正常。
7. ✅ 用户不能访问另一个用户的 `session_id`。
8. ✅ 撤销分享权限后,已有 iframe 与历史链接立即失效。

## 原型不验证的范围(明确排除)

- 悬浮组件(`floating-chat-widget.tsx`)的令牌注入与引用渲染。
- Agent iframe 的会话恢复与引用渲染。
- 多租户隔离。
- 公开分享(匿名访问)。
- 第三方站点嵌入(跨域 cookie、frame-ancestors 白名单、SSE 跨域)。
- 导出会话。
- 部门树、授权有效期、审批流。
- 分享页管理员角色(一期仅平台管理员 + 普通用户两层)。
- 审计日志的自动清理(一期永久保留)。

## 原型数据模型参考

见 `/tmp/ragflow-portal-decisions.md` 的「一期数据模型汇总」。原型实现只需最小子集:

```text
portal_user(id, username, password_hash, is_admin, enabled)
share_page(id, name, ragflow_resource_id, embed_type='fullscreen', ragflow_type='chat', enabled)
share_page_grant(share_page_id, subject_type='user', subject_id, permission='use')
chat_session_owner(session_id, share_page_id, portal_user_id, ragflow_resource_id, title, created_at, last_active_at)
```

原型可硬编码用户与授权数据,无需实现用户组、审计日志、管理员 UI;这些是 PRD 阶段实现。

## 原型完成后输出

原型会话结束后,通过 `/handoff` 输出:
1. 每个验收点(1-8)的通过/失败状态与证据。
2. 每个待验证假设(H1-H9)的验证结果与最终方案。
3. 若有失败项,说明阻塞原因与建议补救方向。
4. 确认或修正后的最终数据模型与架构(供 `/to-prd` 使用)。

## 下一步工作流

```text
prototype(本交接) -> /handoff -> /to-prd -> /to-issues -> 按 issue /implement
```
