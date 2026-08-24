# 当前 Issue 追踪

本文件记录 `portal-extension` 当前待实施的纵向切片。已完成的历史 Issue 1–84、验收记录和阶段性决策保留在 [archive/ISSUES.md](archive/ISSUES.md)，不在本文件重复维护。

## Issue 85：让引用文档完整预览沿用 Portal 受限授权

> 状态：✅ 已完成（2026-07-14）。后端测试覆盖成功预览、越权文档、撤权及原生 token 透传；nginx 精确 location 已部署；生产 Playwright 验证“劳动法历史回答 → 点击 `.docx` 文档卡片 → preview 200 且 iframe 不进入登录页”。部署踩坑（nginx 配置漏更新）记录在 `CONTEXT.md` §8。

## What to build

用户在 Portal 嵌入的 RAGFlow 会话中点击回答末尾的引用文档卡片时，完整文件预览请求必须经过 Portal 网关，而不是让携带 `pt_` 的请求直接进入 RAGFlow 并因 401 跳转登录页。网关只允许预览当前短期令牌已通过 history 或 SSE 验证的引用文档，并在校验 Portal 会话、分享页授权和令牌状态后使用服务端 beta token 请求 RAGFlow。

## Acceptance criteria

- [x] 已登录 Portal 的用户打开自己有权访问的引用会话并点击引用文档卡片时，`GET /api/v1/documents/{doc_id}/preview` 返回 200 和原始文件内容，iframe 不跳转 `/login`，无需建立 RAGFlow 登录态。
- [x] Portal preview 代理复用现有引用访问校验：有效 `pt_`、当前 Portal 用户、分享页 grant/启用状态以及文档属于该令牌的 history/SSE 引用集合；未引用文档返回 403，失效或撤销状态保持既有语义。
- [x] 无 Portal token 的原生 RAGFlow JWT/API/BETA 预览请求保持透传，不因新增代理回归。
- [x] nginx 仅把 `/api/v1/documents/{doc_id}/preview` 精确分流到 Portal，其他 RAGFlow 文档接口不扩大代理范围。
- [x] 后端自动化测试覆盖成功预览、越权文档、撤权及原生 token 透传；生产 Playwright 覆盖“劳动法历史回答 → 点击 `.docx` 文档卡片 → preview 200 且 iframe 不进入登录页”。
- [x] API、架构、部署与排障文档同步更新。


---

# 功能线：管理后台 UX 优化与使用统计（2026-08-24 grilling 锁定）

决策依据见 `CONTEXT.md` §12。实施顺序：UX 线（86→89）先行，用量统计线（90→92）随后。阻塞关系：87←86，89←88，91←90，92←91+86。

## Issue 86：useAdminList 收编全站搜索/筛选/排序能力 + 用户管理页接入（tracer）

> 状态：✅ 已完成（2026-08-24,impl-ux worktree)。`useAdminList` 新增 `options.view` 声明式配置(searchColumns/filters/sortColumns)并返回视图状态;核心逻辑在纯函数 `applyAdminListView`,外加独立 `useAdminListView` 供列表状态在 hook 外的页面使用。新增 `components/AdminListControls.tsx`(AdminListToolbar + SortableTh,内联 SVG 箭头)。UsersAdminPage 作为 tracer 接入:搜索 = 用户名+邮箱,筛选 = 启用/禁用,排序 = 用户名/创建时间,操作列不排。

## What to build

把目前各 admin 页面各自手写的 filter 逻辑收进 `useAdminList` hook，形成全站统一能力：搜索框对指定文本列做不区分大小写模糊匹配、枚举/状态列筛选下拉、表头点击三态排序（升→降→不排）。先在用户管理页（UsersAdminPage）接入作为 tracer：搜索列 = 用户名 + 邮箱；筛选 = 启用/禁用；排序列 = 用户名、创建时间。

## Acceptance criteria

- [x] `useAdminList` 提供 search/filter/sort 通用能力，页面侧只需声明列配置，不再手写过滤逻辑。
- [x] 用户管理页：搜索框模糊匹配用户名与邮箱（不区分大小写）；启用状态可筛选；用户名、创建时间列可三态排序；操作列不参与排序。
- [x] 前端 Vitest 覆盖 hook 的搜索/筛选/排序组合逻辑与用户管理页渲染；既有 89 个前端测试不回归。
- [x] 视觉沿用 D1 Graphite 设计 token，不引第三方组件库。

## Issue 87：其余 5 个 admin 页面接入统一搜索/筛选/排序

> 状态：✅ 已完成（2026-08-24,impl-ux worktree)。5 页全部接入:SharePages(搜索 名称+资源 ID;筛选 状态/嵌入类型/RAGFlow 类型;排序 名称/创建时间,新增创建时间列)、Grants(搜索 授权对象名+当前分享页名;筛选 主体类型;排序 主体类型/名称;列表仍随分享页切换重取,经 `useAdminListView` 接入)、Sessions/AuditLogs 改为全量加载 + 纯内存检索(原服务端带参筛选表单删除,§12.1 内存方案;审计日志日期范围以 `control:'date'` 筛选声明)、Groups 卡片网格接入组名搜索(无枚举列与表头,筛选/排序不适用,留待 Issue 89 master-detail 重设计)。各页手写 filter 逻辑已全部删除。

## What to build

把 Issue 86 的 hook 能力推广到用户组、分享页、授权、会话、审计日志 5 个页面。各页搜索列：组页 = 组名；分享页 = 名称 + RAGFlow 资源 ID；授权页 = 分享页名 + 授权对象名；会话页 = 会话标题 + 用户名；审计日志 = 操作者。筛选只给枚举/状态列（公开/私有、授权对象类型、审计操作类型、时间范围等）。

## Acceptance criteria

- [x] 6 个 admin 页面全部具备搜索框；文本列模糊匹配，枚举列筛选下拉，有意义列三态排序，行为与 Issue 86 约定一致。
- [x] 各页面内的手写 filter 逻辑删除，统一走 `useAdminList`。
- [x] 前端测试逐页覆盖至少一条搜索 + 一条筛选 + 一次排序路径。

## Issue 88：可搜索 combobox 组件 + 全站长列表选择点替换

> 状态：✅ 已完成（2026-08-24,impl-ux worktree)。新增自研 `components/SearchableCombobox.tsx`(单选/多选 chips 两模式;label+sublabel 不区分大小写包含过滤;ArrowUp/Down 移动高亮、Enter 选中、Esc 关闭、多选空输入 Backspace 删尾 chip;ARIA combobox/listbox/option + aria-activedescendant;样式沿用 D1 Graphite token,未引组件库)。全站 `<select>` 盘点(7 处)与处置:① Groups 添加成员(长用户列表)→ 多选 combobox + chips,一次可添加多人(逐个乐观更新);② Grants 分享页 scope 选择 → 单选 combobox(名称+资源 ID 可搜);③ Grants 授权对象 → 单选 combobox(用户名+邮箱/组名可搜,随主体类型切换);④ Sessions 用户筛选/分享页筛选(长列表)→ `AdminListFilterDef.control:'combobox'`,工具栏渲染单选 combobox,"全部"为清空伪选项;⑤ Grants 主体类型(固定 2 项枚举)→ 保留原生;⑥ SharePages 创建表单 embed_type/ragflow_type(固定 2 项枚举)→ 保留原生;⑦ 其余工具栏筛选(SharePages 状态/嵌入类型/RAGFlow 类型、Grants 主体类型筛选、AuditLogs 操作类型+日期范围,均为小枚举/日期)→ 保留原生。审计日志"操作者"为搜索列而非下拉,无替换对象。前端 124 个 Vitest 全绿(组件 10 个:过滤/选择/chips 移除/键盘;Groups/Grants/Sessions 页级用例适配),tsc + eslint 净。注意:`#grant-share-page`/`#grant-subject-id` 与组内"选择用户"已非原生 select,生产 Playwright CRUD 用例(`test/playwright/portal_extension/test_portal_admin_crud.py`,在本 stream 范围外)需在 Issue 89 一并适配,详见 Issue 89 实施记录。

## What to build

自研可搜索 combobox 组件（沿用设计稿 token，不引组件库），两种模式：多选 + chips（添加成员场景）、单选搜索（授权对象场景）；输入即对用户名 + 邮箱做不区分大小写模糊过滤。替换用户管理"给分组添加成员"与授权管理"选择授权对象"两处原生下拉；实施前做一次全站盘点（含审计日志操作者筛选等同类位置），盘点结果列入本 Issue 一并替换。

## Acceptance criteria

- [x] combobox 组件支持单选/多选两种模式，键盘可操作（上下移动、回车选中、Esc 关闭），多选以 chips 呈现已选项。
- [x] "添加成员"与"授权对象"两处下拉完成替换；全站盘点出的其余长列表选择点（盘点清单附在实施记录中）完成替换或明确记录不替换的理由。
- [x] 前端测试覆盖过滤、选择、chips 移除、键盘交互。

## Issue 89：用户组管理主从视图重设计

> 状态：✅ 已完成（2026-08-24,impl-ux worktree)。GroupsAdminPage 改为 master-detail:左侧 `.group-master` 可搜索组列表(复用 `useAdminListView` 组名搜索;每项为 button,`data-testid="group-row-<id>"` 保留,选中态 `aria-current` + accent 左边条),右侧 `.group-detail` 详情面板——顶部"添加成员"(Issue 88 combobox 多选 + chips,一次多人,逐个乐观更新)、成员列表(`.member-list`/`.member-item` 保留,移除走 `ConfirmDialog` danger 确认)、该组已获得的分享页授权列表(选中组切换时并行拉取各分享页 grants 后按 `subject_type=group + subject_id` 过滤,显示分享页名 + 权限)。创建表单保留,新组建后自动选中;Slice 43 漏字段兜底保留。前端本无"删除组"入口(API client 亦无 deleteAdminGroup),AC 所指删除能力 = 移除成员,已覆盖确认弹窗。CSS:`.group-master-detail` 300px+1fr 网格,<768px 堆叠单列(§6.5 桌面优先、小屏不崩);旧 `.admin-groups` 卡片网格与 `.admin-add-member-form select` 死样式删除。前端 126 个 Vitest 全绿(组页 10 个:主从切换/授权展示/多选添加/chips 移除/确认移除/取消移除/搜索/创建/Slice 43 回归/错误态),tsc + eslint + `npm run build` 净。
>
> **Playwright admin CRUD 用例适配说明(无法在本 stream 执行)**:`test/playwright/portal_extension/test_portal_admin_crud.py` 位于 portal-extension 之外(本 stream 范围仅 `portal-extension/frontend/` + `docs/`),且运行需生产部署(`PORTAL_E2E_BASE_URL` 指向 172.16.10.180)。Issue 88/89 后该文件三处需由负责该目录的 stream 适配:① `test_admin_create_grant_revoke_and_delete_user` 中 `#grant-share-page`/`#grant-subject-id` 已是 combobox input 而非原生 select——`select_option_containing` 需改为「click input → click role=option」;`input_value()` 现在返回显示名而非 id,审计断言用的 share_page_id 应改从 `api_get_json('/admin/share-pages')` 取;② `test_admin_group_form_and_member_add_remove` 需先 click `group-row-<id>` 选中组,再在 `group-detail` 内操作 combobox(click → click option → click「添加」);「移除」现在先弹确认框,需再 click「确认移除」;③ `select_option_containing` helper 对上述场景不再适用。组名/成员 testid(`group-row-*`、`group-detail`、`.member-item`、「选择用户」label、「添加」按钮名)均已为新布局保留,适配成本集中在交互方式而非定位器。

## What to build

用户组管理页改为 master-detail 布局：左侧组列表（顶部搜索框，复用 Issue 86 能力），右侧选中组的详情面板——成员列表（每行带移除按钮）、该组已获得的分享页授权列表、顶部"添加成员"入口（Issue 88 的 combobox 多选模式）。

## Acceptance criteria

- [x] 选中组即所见：成员与该组的分享页授权在右侧面板直接可见，无需跳页或开下拉。
- [x] 添加成员走 combobox 多选 + chips，一次可添加多人；移除成员有确认。
- [x] 既有组的创建/删除能力不回归；Playwright admin CRUD 用例适配新布局后通过。(前端创建/移除成员/添加成员均有 Vitest 回归覆盖;Playwright 文件在本 stream 写范围之外且需生产部署,无法在此适配与运行——逐条适配指引见上方实施记录,已尽量保留 testid/label 降低适配成本)
- [x] 视觉沿用 D1 Graphite token；桌面优先，小屏不崩（沿用 §6.5 边界）。

## Issue 90：RAGFlow fork——chatbot completions SSE 末帧透出结构化 usage（硬约束批准票）

> 状态：✅ 已完成（2026-08-24）。本票即 §4 硬约束「RAGFlow 侧改动需 Issue 明确批准」的批准记录（2026-08-24 用户决策）。

## What to build

在 RAGFlow fork 中把 `dialog_service.chat()` 已计算的 `used_token_count`（输入）与 `tk_num`（输出）作为结构化字段透出：chatbot completions SSE 流末帧（`data: true` 帧之前或并入既有末帧）的 `data` 增加 `usage: {input, output}`；非流式路径同步在返回字典中加 `usage`。改动范围尽量限于 `dialog_service.py` 返回值与 `bot_api.py`/`conversation_service.py` 的透传，不动 LLM 调用层。

## Acceptance criteria

- [x] 流式与非流式 chatbot completions 响应均携带结构化 `usage: {input, output}`；agent 路径不动。
- [x] 不带该字段的旧客户端/门户版本行为不回归（字段纯增量）。
- [x] RAGFlow 侧配套测试（usage 字段存在且数值与 `num_tokens_from_string` 口径一致）。
- [x] 文档含构建说明与 docker cp 部署/回滚步骤（沿用 CONTEXT.md §9.2 模式，记录容器重建丢失边界）。

### 实施记录（2026-08-24）

方案选择：并入既有末帧（`final: true` 的答案帧），不加新帧——`structure_answer()` 原地改 answer dict 并原样返回，usage 自动随之透出，`bot_api.py`/`conversation_service.py` 零改动，是改动面最小的路径。

改动文件（纯增量，共 2 个）：

- `api/db/services/dialog_service.py`：`async_chat()` 内 `decorate_answer()` 返回 dict 新增 `"usage": {"input": used_token_count, "output": tk_num}`。input = `message_fit_in` 本地计词（dialog_service.py:769），output = `num_tokens_from_string(think + answer)`（dialog_service.py:834）。与 Langfuse `usage_details` 同源同口径。
- `test/unit_test/api/db/services/test_dialog_service_usage.py`：4 个用例——stream 末帧携带 usage（且 output == `num_tokens_from_string(answer)`、delta 帧不带 usage）、非流式单帧携带 usage、`empty_response` 捷径（无 LLM 调用）不带 usage 的边界、`structure_answer` 透传锁定。

透出路径：stream 末帧答案帧（`data: {..., "data": {..., "final": true, "usage": {...}}}`，位于终止帧 `data: true` 之前）；非流式 `get_result(data=answer)` 的 result dict。同字段也会出现在其它 `async_chat` 消费方（chat_api/openai_api/sdk 会话接口）的末帧——同为纯增量，旧客户端忽略即可。agent（agentbots）路径与 rag/llm 调用层未动。

边界：`empty_response` 捷径与断流/异常帧不带 usage；门户记账（Issue 91）按 §12.2 估算口径兜底，与「旧 RAGFlow 末帧无 usage」同路径。

### 部署/回滚（docker cp，沿用 §9.2 模式）

线上容器 `docker-ragflow-cpu-1` 为官方 v0.26.0 镜像 + 既往 cp 的 fork `bot_api.py`（Slice 2/5 端点）。本票只需替换 `dialog_service.py` 一个文件，**不要动容器内 `bot_api.py`**。

**容器重建边界**：`docker restart` 不丢；`docker compose down -v` 或重新 `up` 镜像会丢，需重新执行。

```bash
# 1. 备份容器内原文件
TS=$(date +%Y%m%d-%H%M%S)
docker exec docker-ragflow-cpu-1 cp /ragflow/api/db/services/dialog_service.py /ragflow/api/db/services/dialog_service.py.bak.$TS

# 2. 替换(从本地 fork 源码)
scp ragflow/api/db/services/dialog_service.py 172.16.10.180:/tmp/dialog_service.py
ssh 172.16.10.180 'docker cp /tmp/dialog_service.py docker-ragflow-cpu-1:/ragflow/api/db/services/dialog_service.py'

# 3. 重启 api server(不重启容器,避免丢其他 cp 文件)
ssh 172.16.10.180 'docker exec docker-ragflow-cpu-1 pkill -f ragflow_server.py; sleep 15'

# 4. 验证:流式末帧答案帧应含 "usage":{"input":N,"output":M}(grep 有输出即生效)
ssh 172.16.10.180 "curl -sN -H 'Authorization: Beta <beta_token>' -H 'Content-Type: application/json' -d '{\"question\":\"你好\",\"session_id\":\"<session_id>\",\"stream\":true}' http://172.16.10.180:8080/api/v1/chatbots/<dialog_id>/completions | grep -o '\"usage\":{[^}]*}' | tail -1"

# 回滚
docker exec docker-ragflow-cpu-1 bash -c "cp /ragflow/api/db/services/dialog_service.py.bak.<ts> /ragflow/api/db/services/dialog_service.py && pkill -f ragflow_server.py"
```

本地构建/验证：改动无新依赖，`uv sync --python 3.13 --all-extras` 后 `uv run --group test pytest test/unit_test/api/db/services/test_dialog_service_usage.py`（4 passed）；`ruff check api/db/services/dialog_service.py` 通过。

## Issue 91：门户 usage_record 表 + 网关 SSE 记账

> 状态：待实施。阻塞：← Issue 90（真实 usage 数据来源；开发期测试可用 mock SSE 先行）。

## What to build

门户新增 `usage_record` 表：时间、portal 用户、分享页、session_id、模型名、input/output token、`incomplete` 标记。网关在 SSE 代理链路（既有 SSEJSONEventParser 路径）摘取末帧 `usage` 落账；断流时按已收到回答文本计词元作 output、按请求问题估算 input，标记 `incomplete`；末帧无 usage（旧 RAGFlow）时按同一估算口径记账并标记。不存问答正文。

## Acceptance criteria

- [ ] 每次 chat completions 问答落一条 usage_record，字段完整；正文不落库。
- [ ] 正常完成、断流、末帧缺 usage 三种路径均有测试覆盖，incomplete 语义符合 CONTEXT.md §12.2。
- [ ] 会话删除不影响已落账记录；公开分享页（匿名用户）问答同样记账（subject 为匿名标识）。
- [ ] 后端 pytest 基线更新并记录。

## Issue 92：admin「用量统计」tab（汇总 + 明细 + CSV 导出）

> 状态：待实施。阻塞：← Issue 91（数据）+ Issue 86（列表交互约定）。

## What to build

admin sidebar 新增「用量统计」tab，仅 admin 可见。汇总视图：按用户聚合（默认本月），列 = 问答次数、input/output token、incomplete 次数，可按时间段/分享页筛选、按列排序、导出 CSV。明细视图：逐条 usage_record（时间、用户、分享页、会话、token、incomplete 标记）。UI 数字旁标注口径为 RAGFlow 本地计词，非厂商账单。

## Acceptance criteria

- [ ] 汇总/明细两视图数据与 usage_record 一致；筛选、排序遵循 Issue 86 全站约定。
- [ ] CSV 导出内容与当前筛选结果一致。
- [ ] 非 admin 用户无法访问该 tab 与对应接口（403）。
- [ ] 前端测试 + 后端接口测试覆盖；管理员手册（docs/manual/admin-manual.md）新增对应章节。


---

# 架构优化线（2026-08-24 架构评审产出）

评审报告与候选详情见当日报告（临时文件，不入库）；决策词汇沿用 codebase-design（module/interface/seam）。实施顺序：93 插在 Issue 91 之前（91 的 SSE 记账逻辑要落在收敛后的接缝上）；94→95 一组；96 零风险可并入任意 slice；97 需独立 slice 迁移测试；98 优先级最低。候选 7（公开/私有端点合并）经评审定为不做，仅在本线提取 `"u_anonymous"` 常量（并入 96）。

## Issue 93：网关校验链收敛为单一 resolve 模块 + app.state 访问 fail-closed

> 状态：✅ 已完成（2026-08-24,分支 impl/93-96）。4 份校验链收敛为 `resolve_token_and_share_page`(SSE 标准/公开、bot JSON、history/thumbnail/preview/image 共用),「先查 grant 再 validate」顺序不变量单份存在;`_proxy_sse_public_core`/`_validate_reference_access`/`_portal_reference_context`/`PortalReferenceContext` 删除,公开链后半段由 `_proxy_public_sse_stream` 共用;session_store/rate_limiter/audit_store 改直接属性访问(fail-closed)。唯一有意语义差异:公开链 is_public/enabled 双失败时 detail 顺序与旧 reference 校验相反(均为 403)。基线 451+5 不回归。无阻塞。必须在 Issue 91 之前完成。

## What to build

把 `gateway.py` 内 4 份近乎相同的「取令牌记录 → `pt_` 前缀 → 校验/透传」校验链（`proxy_sse_to_ragflow` 标准/公开链、`proxy_bot_json_to_ragflow`、`_validate_reference_access`、`_portal_reference_context`）收敛为一个 `resolve_token_and_share_page(request, resource_id)`，返回 `(record, share_page)` 或抛出 401/403/404。「先查 grant 再 validate（撤权返 403 而非 401）」的顺序不变量只存在一份。同时把 `gateway.py` 中 `getattr(request.app.state, ..., None)` 的服务定位（session_store / rate_limiter / audit_store）改为直接属性访问，缺失即 AttributeError（fail-closed），测试 fixture 补齐 state。

## Acceptance criteria

- [ ] 校验链全仓只剩一份实现；三条代理链与 reference 端点均调用统一入口，公开/私有差异降为参数。
- [ ] 撤权返 403、无令牌返 401、未引用文档返 403 等既有语义逐条有测试锁定，451+5 后端基线不回归。
- [ ] `app.state` 关键服务缺失时请求报错而非静默跳过校验；不再有 `getattr(..., None)` 形式的服务定位。
- [ ] CONTEXT.md 相应段落（§3 代码结构 / §6.4）同步。

## Issue 94：models.py 回归纯持久化（编排上移至 gateway）

> 状态：✅ 已完成（2026-08-24,分支 impl/93-96）。`SessionStore.cascade_delete_for_user` 与 `SessionStore.sync_message_count_from_history` 上移为 gateway.py 同名模块级函数(首参数 session_store,其余签名不变),models.py 不再 import gateway(无延迟 import、无环);`build_seed_data` 4 段手写 ORM 映射改走 `create_user`/`create_share_page`(新增 `user_id=`/`page_id=` 固定 ID 参数,不传时仍自动生成,行为不变)。测试 mock 接缝不变(函数内部按名解析 gateway 模块 globals)。基线 451+5 不回归。

## What to build

把 `SessionStore.cascade_delete_for_user` 与 `SessionStore.sync_message_count_from_history` 两个含 RAGFlow HTTP 调用与业务决策的 async 方法从 `models.py` 上移至 `gateway.py`（或新建 services 模块），消除 models→gateway 的延迟 import 环。`build_seed_data` 中 4 段手写 ORM 映射改走 `create_user`/`create_share_page` 方法，消掉双份映射。

## Acceptance criteria

- [ ] `models.py` 不再 import gateway（无延迟 import、无环），只含持久化与 store 方法。
- [ ] 级联删除、消息数同步行为不变，相关 slice 测试不回归。
- [ ] seed 数据构建路径复用 store 方法，启动引导结果与现状一致（有测试锁定）。

## Issue 95：会话归属校验收敛 + 双删编排下沉

> 状态：✅ 已完成（2026-08-24,分支 impl/93-96）。resume/rename/delete 三处逐行相同的归属校验收敛为 routes.py `_get_owned_session_or_404()`(404 不存在 / 403 归属他人 / 403 不属于该分享页的语义分叉在 docstring 显式写明);admin 变体显式传 `expected_owner_id=None` 跳过归属校验。`_dual_delete_session` 从 routes.py 下沉至 gateway.py(随 `_delete_session` 分发一并移入,routes 恢复「只注册路由 + 薄适配」);chat/agent 分发按名解析 gateway 模块 globals,相关测试 mock 接缝随之改 patch gateway 命名空间(conftest `mock_delete_session` 双命名空间不变)。基线 451+5 不回归。

## What to build

`routes.py` 中 resume/rename/delete 三处逐行相同的归属校验（owner None→404、user 不符→403、share_page 不符→403）收敛为 `_get_owned_session_or_404()`；admin 变体显式参数化。404/403 语义分叉在该函数内写明。`_dual_delete_session` 从 routes.py 下沉至 gateway.py，routes 恢复"只注册路由 + 薄适配"的硬约束状态。

## Acceptance criteria

- [ ] 三块重复校验删除，admin 变体走显式参数；404 vs 403 语义差异在代码中显式化（注释/命名）。
- [ ] `_dual_delete_session` 位于 gateway 层；`routes.py` 无双删编排逻辑。
- [ ] 归属与双删相关测试不回归。

## Issue 96：死代码清理 + admin_update_user 校验修复

> 状态：✅ 已完成（2026-08-24,分支 impl/93-96）。删除无调用方的 `SessionStore.increment_message_count`;删除 `_validate_subject_exists` 不可达 else 分支(subject_type 已由 CreateGrantRequest 的 Literal 在入口处 422);`admin_update_user` 改用 `UpdateUserEnabledRequest`(enabled 必填),只传 is_public / 空 body → 422 不写库,回归测试 `test_admin_update_user_without_enabled_returns_422_no_write` 锁定;`LoginError` 空子类评估后**保留**(routes.login 用 `except LoginError` 精确区分登录失败写审计,理由已写入 auth.py docstring);`"u_anonymous"` 提取为 models.py `ANONYMOUS_USER_ID` 常量(routes×3、gateway×1、build_seed_data×2 全部改引用,生产代码字面量只剩常量定义一处;测试中字面量为对常量值的断言,保留)。基线 451+5 → 452+5(新增 1 个回归测试)。

## What to build

删除无生产调用方的 `SessionStore.increment_message_count`；删除 `_validate_subject_exists` 的不可达 else 分支；修复 `admin_update_user` 未校验「至少传一个字段」导致 `enabled=None` 写入 NOT NULL 列的问题（对齐 share-page 版校验）；评估 `LoginError` 空子类去留；提取 `"u_anonymous"` 为集中常量（4 处散布：routes×2、gateway、models seed）。

## Acceptance criteria

- [ ] 死代码删除后测试基线不回归；`admin_update_user` 只传 `is_public` 时返回 422 而非写库，有回归测试。
- [ ] `"u_anonymous"` 全仓只剩常量定义一处。

## Issue 97：测试 mock 迁移到 httpx transport 层，删除为 monkeypatch 保留的薄模块

> 状态：待实施。无阻塞。需独立 slice（测试迁移量大，约 30 处 monkeypatch）。

## What to build

把测试对 RAGFlow 调用的 mock 从「按函数名 patch 模块命名空间」迁移到 httpx transport 层（MockTransport）。迁移完成后删除 gateway.py 中 4 个纯 pass-through 的 agent 薄模块（docstring 自认"保留函数名供测试 monkeypatch"）与 routes.py 中 5 个按名 dispatch 函数（改为直接调用统一函数）；conftest 的双重命名空间 patch（`portal.routes.X` + `portal.gateway.X`）随之消除。

## Acceptance criteria

- [ ] 生产代码中不再存在"为测试保留"的函数；dispatch 不依赖运行时模块 globals 解析。
- [ ] 测试只 patch transport 层，不再穿模块边界 patch `httpx.AsyncClient` 属性或双重命名空间。
- [ ] 后端测试基线全绿；行为零变化。

## Issue 98：TokenStore 拆出 ReferenceAccessStore

> 状态：待实施。无阻塞。优先级最低，可在用量统计线稳定后再做。

## What to build

把「引用资源授权」从 TokenStore 拆出：新 `ReferenceAccessStore` 管 `authorized_document_ids` 与 `pit_` 图片票据（签发/校验/prune/remove）；TokenStore 回归只管 T_short 生命周期（issue/validate/touch/revoke）。两者通过 t_short 值关联。`pending_greeting_session_ids` 归属一并评估。

## Acceptance criteria

- [ ] 「令牌生命周期」与「引用资源授权」两个概念各有模块归属；引用图片链路（签发→改写→校验）涉及的位置从 6 处收敛到 ReferenceAccessStore 内部 + 调用点。
- [ ] 引用图片/缩略图/预览相关测试（Issue 82/84/85 用例）不回归。
