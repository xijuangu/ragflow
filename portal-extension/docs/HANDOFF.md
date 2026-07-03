# RAGFlow 权限门户改造 — 工作交接

**交接时间:** 2026-07-03
**分支:** `portal-extension`(基于 v0.26.0)
**Fork:** `xijuangu/ragflow`
**状态:** 7 个 Slice 全部完成 + review + 推送

---

## 一、项目概述

为 RAGFlow v0.26.0 增加权限门户层,解决三个问题:
1. 分享页 iframe URL 含租户 Token 泄露风险
2. Chat iframe 会话用 `API4Conversation` 表,官方普通 Chat 会话 API 无法管理历史
3. 缺乏用户/角色/授权/审计体系

架构:门户(自建账号 + 网关)+ RAGFlow 最小后端扩展(3 个端点),同源部署,不修改 RAGFlow 前端。

## 二、工作流完成状态

| 阶段 | Skill | 产出 | 状态 |
|---|---|---|---|
| 需求访谈 | grilling | 10 项决策(D1-D10) | ✅ |
| 原型验证 | prototype | 9 项假设验证(H1-H9) | ✅ |
| PRD | to-prd | PRD.md(43 user stories) | ✅ |
| Issue 拆分 | to-issues | ISSUES.md(7 个 vertical slice) | ✅ |
| Slice 1-7 实现 | implement | 7 个 slice 代码 + 测试 | ✅ |
| Slice 1-7 审查 | review | 每slice 双轴 review + 修复 | ✅ |
| 交接 | handoff | 本文档 | ✅ |

## 三、7 个 Slice 完成状态

| Slice | 内容 | 实现 commit | review 修复 commit | 状态 |
|---|---|---|---|---|
| 1 | 最小可登录分享页骨架 | `b5f058b` | `b95f58b` | ✅ 推送 |
| 2 | session_id 绑定 + 历史恢复 | `e4d2f3a` | `f944539` | ✅ 推送 |
| 3 | 继续对话 + 用户隔离 + 撤销 | `dbb62d9` | `cf082e4` | ✅ 推送 |
| 4 | 用户/组/分享页/ACL CRUD | `ff519e3` | `a332bce` | ✅ 推送 |
| 5 | 会话重命名/删除 + 双删 + 级联 | `6fecd01` | `30bc54d` | ✅ 推送 |
| 6 | 管理员后台 + 审计日志 | `0155a3d` | `72ce755` | ✅ 推送 |
| 7 | 端到端测试固化 | `6093efa` | `16cf03f` | ✅ 推送 |

**测试:** 178 passed, 5 skipped(integration 需真实 RAGFlow)
**ruff:** 全部干净
**敏感信息:** 无泄露(beta Token/密码/IP 仅环境变量)

## 四、10 项关键决策

| # | 决策点 | 结论 |
|---|---|---|
| D1 | 身份来源 | 门户自建账号 |
| D2 | 租户隔离 | 单租户,不预留 org_id |
| D3 | 公开分享 | 仅登录用户 |
| D4 | 权限主体 | 用户 + 用户组,无部门树/有效期/审批 |
| D5 | 管理员层级 | 两层(普通用户 + 平台管理员) |
| D6 | 用户会话能力 | 查看+继续+重命名+删除,无导出,硬删除 |
| D7 | 管理员访问与审计 | 分级查看(查正文需二次确认+审计),仅敏感操作 |
| D8 | 数据保留 | 禁用保留/硬删用户清会话;审计永久;撤销授权立即拒绝+保留 |
| D9 | 一期范围 | 仅全屏 Chat,字段保留但固定值 |
| D10 | 运行位置 | 仅门户内同源,X-Frame-Options: SAMEORIGIN |

## 五、9 项假设验证结论

| 假设 | 判定 | 关键证据 |
|---|---|---|
| H1 令牌注入 | ✅ 通过 | URL `?auth=` 原生注入,localStorage 被绕过 |
| H2 按 session_id 加载 | ⚠️ 需加端点 | service 层有 get_by_id,bot_api 无公开 GET,已加 |
| H3 历史会话恢复 | ✅ 通过 | reference 含 chunks + doc_aggs;PDF/图片可加载 |
| H4 旧 session 继续流式 | ✅ 通过 | 三轮 SSE 正常,session_id 保持 |
| H5 session_id 归属隔离 | ✅ 结论性 | RAGFlow 仅租户级,用户级由网关实现 |
| H6 撤销授权立即失效 | ✅ 结论性 | RAGFlow 不感知 grant,撤销由网关实现 |
| H7 重命名落点 | ✅ 通过 | API4Conversation.name 字段存在,update_by_id 可用 |
| H8 双删事务一致性 | ✅ 通过 | delete_by_id 可用,失败标记 deleted_at 待重试 |
| H9 管理员 elevated 查看 | ✅ 通过 | 租户 Token 可读任意 session,复用 GET 端点 |

## 六、RAGFlow 侧改动(3 个端点)

文件:`api/apps/restful_apis/bot_api.py`

| 端点 | 方法 | 复用 | 用途 |
|---|---|---|---|
| `/chatbots/<dialog_id>/sessions/<session_id>` | GET | `API4ConversationService.get_by_id` | 读取会话消息+引用 |
| `/chatbots/<dialog_id>/sessions/<session_id>` | PATCH | `API4ConversationService.update_by_id` | 重命名会话 |
| `/chatbots/<dialog_id>/sessions/<session_id>` | DELETE | `API4ConversationService.delete_by_id` | 删除会话 |

抽取 `_assert_dialog_access` helper(三处 dialog 校验复用)。无新业务逻辑。

## 七、门户侧架构

```
portal-extension/
├── portal/
│   ├── main.py          # FastAPI 入口 + 中间件(X-Frame-Options)
│   ├── config.py        # 环境变量配置
│   ├── auth.py          # 会话校验 + require_admin + 登录错误
│   ├── password.py      # bcrypt 哈希
│   ├── models.py        # SeedData(可变内存存储) + SessionStore + AuditStore + TokenStore
│   ├── gateway.py       # T_short 签发/撤销 + iframe URL + SSE 代理 + RAGFlow 调用
│   └── routes.py        # 全部端点(login/embed-url/sessions/proxy/admin CRUD/audit)
├── tests/
│   ├── conftest.py      # fixture + mock helper
│   ├── test_slice1_e2e.py    ~ 14 测试
│   ├── test_slice2_e2e.py    ~ 15 测试
│   ├── test_slice3_e2e.py    ~ 15 测试
│   ├── test_slice4_e2e.py    ~ 44 测试
│   ├── test_slice5_e2e.py    ~ 27 测试
│   ├── test_slice6_e2e.py    ~ 48 测试
│   └── test_e2e_regression.py ~ 20 测试(8 验收点固化)
├── docs/                # PRD/ISSUES/决策/验证/交接文档
├── pyproject.toml       # uv + ruff 配置
└── README.md
```

## 八、关键设计实现

### 令牌注入(H1 验证)
- 网关签发 `T_short`(5min,内存,可撤销)
- iframe URL `{RAGFLOW_HOST}/chat/share?shared_id={dialog_id}&auth={T_short}&from=chat`
- RAGFlow 前端 `getAuthorization()` 原生读 URL `?auth=`,绕过 localStorage
- 真实 beta Token 全程不离开网关

### 首次对话双步行为(原型关键发现)
- `async_iframe_completion` 无 session_id 时只创建 session 返回 prologue,不处理 question
- 解决:门户预创建 session(调 RAGFlow 创建空 API4Conversation),绑定到用户,注入 iframe URL

### 网关校验链(五步,任一失败 → 403/401)
0. 同源 cookie 校验(get_current_user)— 失败 403
1. grant 存在(has_use_grant)— 失败 403
2. T_short 有效性 — 失败 401
3. session 归属(_assert_session_ownership)— 失败 403
4. dialog_id 一致 — 失败 403

顺序说明:grant 先于 T_short 有效性,使撤销授权(删 grant + 吊销 T_short)→ 403(grant 先失败),符合 ISSUES 验收点。

### 双删事务策略
- 重命名:同步策略(RAGFlow 失败则 502,门户 title 不更新)
- 删除会话:RAGFlow 成功→硬删除;失败→标记 deleted_at 待重试,返回 200
- 硬删除用户:级联双删所有会话,RAGFlow 失败也硬删除门户侧(无孤儿),失败记日志

### 审计日志(8 类敏感操作)
`login_success` / `login_failure` / `grant_create` / `grant_revoke` / `session_delete` / `session_view_elevated` / `user_enable` / `user_disable`
- 内存存储,永久保留(PRD D8b)
- 普通用户日常操作不记审计(PRD D7b)

## 九、后续待办

### 必须(部署前)
1. **部署 RAGFlow 端点到服务器** — 把 `bot_api.py` 的 GET/PATCH/DELETE 改动部署到 172.16.10.180,重启 RAGFlow 容器,跑 integration 测试验证真实链路。
2. **DB 化** — 当前内存存储,生产前需迁移到真实数据库(PRD 数据模型已就绪)。涉及 `portal_user` / `portal_group` / `portal_group_member` / `share_page` / `share_page_grant` / `chat_session_owner` / `audit_log` 七张表。

### 建议(迭代增强)
3. **前端实现** — 当前只有后端 API,需实现门户前端(登录页、分享页 iframe 加载、我的会话列表、管理后台)。
4. **后台重试任务** — `list_pending_deletion()` 已提供查询,需加定时任务或管理员手动触发端点(Slice 6 已加 retry-delete 端点)。
5. **SSE 代理 message_count 更新** — 当前 message_count 在恢复会话时更新,SSE 代理后不更新(可能滞后)。可解析 SSE 流实时更新。
6. **多租户扩展** — D2 决定一期单租户不预留 org_id,未来需要时加迁移。

### 可选(优化)
7. **OAuth/SSO** — D1 决定一期自建账号,未来可在自建账号上叠加 OIDC/LDAP/飞书 SSO。
8. **公开分享** — D3 决定一期仅登录用户,未来可加 `is_public` 字段 + 限流。
9. **悬浮组件/Agent** — D9 决定一期仅全屏 Chat,字段已预留,验证后扩展。

## 十、关键文件位置

| 文档 | 路径 |
|---|---|
| PRD | `portal-extension/docs/PRD.md` |
| Issues(7 slice) | `portal-extension/docs/ISSUES.md` |
| 10 项决策 | `portal-extension/docs/ragflow-portal-decisions.md` |
| 9 项假设验证 | `portal-extension/docs/ragflow-prototype-handoff.md` |
| 原型验证结论 | `prototype/NOTES.md`(工作区,未进仓库) |
| 本交接文档 | `portal-extension/docs/HANDOFF.md` |

## 十一、运行方式

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
uv sync --python 3.13 --extra dev
uv run pytest -v                    # 全套测试(178 passed, 5 skipped)
uv run pytest tests/test_e2e_regression.py -v  # 回归套件(20 passed)
uv run uvicorn portal.main:app --reload  # 启动门户
```

## 十二、review 历史总结

每个 slice 经双轴 review(Standards + Spec),关键修复:
- Slice 1: iframe SSE 路由断链(改 /api/v1/chatbots/ 路径)+ X-Frame-Options + uv/ruff
- Slice 2: SSE 代理归属隔离 + last_active_at 位置 + Duplicated Code helper
- Slice 3: 死代码删除 + 同源 cookie 校验 + has_use_grant subject_type 过滤
- Slice 4: create_grant 幂等(安全漏洞)+ ACL 解析统一 + Literal 类型
- Slice 5: rename 同步策略(不吞异常)+ admin_delete_user 无孤儿 + dual_delete helper
- Slice 6: 关键词搜索 + message_count + _audit helper
- Slice 7: 5 项分支测试补齐 + AC1 正向用例 + 删未使用 fixture

---

**交接完毕。** 下一位代理可从「后续待办」的任一项接手,建议优先部署 RAGFlow 端点到服务器跑 integration 测试,再做 DB 化。
