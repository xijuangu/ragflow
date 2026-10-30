# 知识库文档自动化更新可行性调研

> 调研日期：2026-09-15
> 场景：法规网站（中期协、证监会、各期货交易所）内容重新抓取后，自动更新到 RAGFlow 知识库
> 基线：本仓库 fork 基于 RAGFlow v0.26.0（`v0.26.0-194-gc7aa2b1`），调研时上游最新版 v0.27.2（2026-09-10）
> 状态：**已决策不立项自动化模块**（2026-09-15），改人工定期维护，操作规程见 [knowledge-base-maintenance-manual.md](knowledge-base-maintenance-manual.md)。本文保留为调研存档，其 API 结论（§3/§4）与路线对比（§5）在未来重启立项时仍然有效。

---

## 1. 结论（TL;DR）

**可行。** 推荐做法：在 portal 内新增一个"知识库同步"模块——定时执行抓取脚本、与 RAGFlow 中已有文档比对、对变化文档执行"删旧 → 重传 → 触发解析 → 轮询状态"，并在 portal 管理后台提供状态展示与审计。

必须接受的核心约束：**RAGFlow 没有"替换文档内容"的 API**（截至 v0.27.2 确认不存在，社区 issue #17815 仍 open）。`Update document` 接口只能改名称、元数据、解析配置、上下架，不接受新内容。因此"更新一篇文档"在 API 层面 = 删除旧文档 + 重新上传 + 显式触发解析，且 **document_id 会变**，所有依赖旧 id 的引用需要重建。

---

## 2. 现状梳理

### 2.1 语料来源与当前流程

| 来源 | 规模 | 抓取方式 | 现状 |
|---|---|---|---|
| `cfachina_rules/`（中期协 + 证监会） | 173 篇 md + 附件 | `fetch_cfachina.py`（调中期协 JSON API）、`fetch_csrc_docs.py`（`csrc_urls.txt` 清单驱动，幂等） | **手动运行**，`FETCH_DATE` 硬编码 |
| `exchange_rules/`（6 家期货交易所） | 228 篇 md | 脚本未入库，只有《抓取与维护指南.md》 | 手动 |
| `财务文档/` | 11 个 doc/pptx | 手工放置 | 手动 |

**抓取产物 → RAGFlow 知识库这一步完全没有任何自动化**：全仓搜索 `upload_documents` / `/api/v1/datasets` 无命中，入库靠人工在 RAGFlow Web UI 操作。

### 2.2 portal 与 RAGFlow 的边界

- portal（`portal-extension/`）只做认证、授权、会话代理、审计，**不碰知识库文档管理**；本地 DB 8 张表中没有任何文档/知识库表
- portal 调 RAGFlow 的现成模式：`portal/gateway.py` 内联 httpx + `RAGFLOW_HOST` + 服务端代持 token
- portal 已有周期性任务的现成模式：`portal/tasks.py`（asyncio + sleep 循环，会话双删重试），在 `main.py` startup hook 启动——**新增定时任务可直接照搬，无需引入 APScheduler**
- fork 自带上游 Python SDK（`sdk/python/ragflow_sdk`），`DataSet.upload_documents` / `async_parse_documents` / `parse_documents`（阻塞轮询至终态）链路完整

---

## 3. RAGFlow 能力边界清单

信息来源：官方文档 [HTTP API Reference](https://ragflow.io/docs/http_api_reference) / [Python API Reference](https://ragflow.io/docs/python_api_reference)（main 分支 ≈ v0.27.x）及上游源码。

### 3.1 文档级操作（核心）

| 能力 | HTTP 接口 | 能力边界 |
|---|---|---|
| 上传文档 | `POST /api/v1/datasets/{id}/documents` | **不覆盖同名文件**（重传产生副本）；上传后 `run=UNSTART`，**不自动解析** |
| 更新文档 | `PATCH .../documents/{doc_id}` | **仅元数据/配置**（name、meta_fields、chunk_method、parser_config、enabled），**不能替换内容** |
| 删除文档 | `DELETE .../documents` | 连带删除 chunks 与解析结果 |
| 触发解析 | `POST .../documents/parse`（fork 内路径；上游公开文档为 `POST /api/v1/datasets/{id}/chunks`） | 重跑即重建 chunks |
| 查询状态 | `GET .../documents`（支持按 name/id/run 过滤） | 返回 `run`（UNSTART/RUNNING/CANCEL/DONE/FAIL/SCHEDULE）、`progress`、`progress_msg`、`chunk_count` |
| 下载文档 | `GET .../documents/{doc_id}` | 可用于与本地内容比对 |

### 3.2 Chunk 级操作（细粒度通道）

`add_chunk` / `update chunk`（改 content 会自动重新 embedding）/ `delete_chunks` / 上下架。如果"更新"能表达到 chunk 粒度，可以绕过"删文档重传"。**但法规文档是整篇抓取的 md，走 chunk 级维护等于自管理 chunking，成本高，不推荐作为首选。**

### 3.3 内置自动化：Data Source 连接器

- v0.22.0（2025-11）起官方主推：40+ 连接器（S3、Confluence、Google Drive、Notion、SharePoint、**通用 REST API**、RSS 等），每个连接器可配 **Refresh interval**（定时增量同步）、**Sync deleted files**（外部删除同步移除索引）、手动 Rebuild（删后重导）
- **与"爬法规网站"场景的匹配度**：中期协有 JSON API，理论上可用通用 REST API 连接器（v0.25.4 引入，在 fork 基线内）适配；证监会/交易所站点无标准接口，无现成连接器；**Sitemap 连接器是 v0.27.2 新增，不在本 fork**
- 连接器管理 REST 接口（`/connectors/...`）存在于源码但**未收录进公开 API 文档**，对外稳定性未确认

### 3.4 明确不存在的能力

- 知识库文档更新的 inbound webhook（webhook 仅用于触发 Agent 执行，v0.23.0）
- 本地文件系统变更监听（无 watch folder）
- 文档内容替换 / 文档版本管理（issue #17815 仍 open）

---

## 4. 本 fork 实测验证结果

以只读方式对 fork 源码核实（非上游文档转述）：

1. **连接器框架在本 fork 是活的**
   - `api/apps/restful_apis/connector_api.py` 存在，`/connectors` 系列路由由 `api/apps/__init__.py` 的 `restful_apis/*.py` glob 自动注册
   - `docker/entrypoint.sh:331` 会启动 `rag/svr/sync_data_source.py` 后台进程（导入 `common/data_source/connector_runner.py`），即官方容器栈跑起来时同步守护进程就在跑
2. **认证差异（关键坑）**：fork 的 dataset/document 管理路由用 `@login_required` 默认认证 = `(AUTH_JWT, AUTH_API)`，**不接受 AUTH_BETA**。portal 现有的 `RAGFLOW_BETA_TOKEN`（`api_token.beta` 字段，iframe 嵌入用）**调不动** dataset/document 管理接口。需要在 RAGFlow 中另配一个 API key（`api_token.token` 字段，即官方文档说的 Bearer API key），portal 新增配置项（如 `RAGFLOW_API_KEY`）
3. **fork 自带 SDK 可直接复用**：`sdk/python/ragflow_sdk/modules/dataset.py` 有 `upload_documents` / `list_documents` / `delete_documents` / `async_parse_documents` / `parse_documents`（阻塞轮询终态，返回 `(doc_id, status, chunk_count, token_count)`）
4. **本地↔RAGFlow 文档映射目前不存在**：portal DB 无任何文档表；文档元数据只存在于 md 文件头（来源 URL / 抓取日期 / 发布修订日期）和两个手工维护的 README 索引。实现自动化必须新建映射记录

---

## 5. 三条路线对比

| 维度 | A. 做进 portal（推荐） | B. 独立脚本 + cron | C. RAGFlow 连接器 |
|---|---|---|---|
| 原理 | portal 新增定时任务：跑抓取 → 比对 → 调 RAGFlow API 删旧传新重解析 | 系统 crontab 跑独立 Python 脚本，同样调 RAGFlow API | 配置 REST API / Sitemap 连接器，RAGFlow 自己定时拉取 |
| 与权限/审计体系 | 一体（portal 管理员可见同步状态、日志进 audit_log） | 脱节（无 UI、无审计，失败靠邮件/日志） | 脱节（配置在 RAGFlow UI，portal 用户无感知） |
| 对"法规网站"场景适配 | 抓取逻辑复用现有脚本，完全可控 | 同 A | **差**：证监会/交易所无标准 API；需为每个站自定义适配 |
| 开发量 | 中（抓取调度 + RAGFlow client + 映射表 + 简单管理页） | 小 | 小~中（但卡在连接器能力边界上） |
| 依赖 fork 改动 | 无（纯 portal 侧） | 无 | 无（但 Sitemap 需升级到 v0.27.x） |
| 主要风险 | 需妥善处理 document_id 变化、解析失败重试 | 同 A，且运维分散 | 连接器行为不可控、删除同步可能误删 |

**推荐 A**，理由：用户已明确希望做进 portal；法规网站抓取逻辑必须自定义（现有脚本已验证可用），连接器的"通用性"在这个场景反而是负担；portal 承载后管理员能在同一界面看到知识库新鲜度，符合权限门户的定位。

---

## 6. 推荐方案的实现要点（供后续开发参考，本轮不实施）

### 6.1 整体流程

```
定时触发（照搬 tasks.py 的 asyncio 循环模式）
  → 执行/复用抓取脚本，产出 md 到语料目录
  → 对每篇：算内容 hash，与 portal DB 映射表中的记录比对
      ├─ 新增 → upload → parse
      ├─ 内容变化 → delete 旧文档 → upload → parse（document_id 变更，更新映射）
      └─ 无变化 → 跳过
  → 轮询解析状态（run=DONE/FAIL），写回映射表
  → 结果落 audit_log；失败标状态由后续周期重试
```

### 6.2 需要新增的组件

- **RAGFlow dataset client**：参照 `gateway.py` 的 httpx 模式封装 upload/delete/parse/list/status 五个接口；新增配置项 `RAGFLOW_API_KEY`（AUTH_API 类型，见 §4.2）
- **数据表**（portal DB 加表，复用 `db.py` 现有 init/ALTER 迁移模式）：`kb_document`（source_url、source_site、内容 hash、ragflow_document_id、dataset_id、解析状态、最近同步时间）、`kb_sync_run`（每次同步批次的统计与错误）
- **定时任务**：照搬 `tasks.py` 模式，间隔可配（如每天凌晨）；抓取与入库解耦，入库任务对抓取失败有容忍
- **管理界面（可选最小集）**：管理后台加一页展示映射表 + 最近同步批次 + 失败列表 + 手动触发按钮

### 6.3 风险点

1. **document_id 变化的连锁影响**：portal 的引用资源代理（`ReferenceAccessStore`）按 document_id 授权，删旧传新后历史会话引用里的旧 id 会失效——缩略图/预览打不开。**缓解**：引用授权按"会话曾经引用过"放宽到 dataset 级，或对历史引用做 id 映射改写；需在方案设计时定夺
2. **解析失败/部分失败**：必须轮询到终态再更新映射表；失败的文档保留旧映射并报警，避免"删了旧的新的没解析好"造成知识库空洞——**更稳妥的顺序是先传新文档解析成功后再删旧文档**（代价是短暂双份，同名副本需改名规避）
3. **增量判定的可靠性**：内容 hash 要排除抓取日期等易变字段（md 文件头含抓取日期），建议只对正文算 hash
4. **抓取侧**：交易所站点反爬（《抓取与维护指南.md》有记录）、证监会清单靠人工维护 `csrc_urls.txt`——自动化后这些仍是薄弱环节，建议保留人工补录入口
5. **并发与限流**：批量删传解析对 RAGFlow task_executor 有压力，需限速串行化

---

## 7. 未确认事项

- 各连接器的 refresh interval 默认值与最小粒度（官方文档只写"可配置"）
- 连接器管理接口（`/connectors/...`）作为外部 API 的长期稳定性承诺
- 若未来升级到 v0.27.x：可获得 Sitemap 连接器（对交易所站点可能有价值）与 File Management 的 workspace commits（git 式文件版本管理），但升级需重新评估 fork 的 194 个提交与上游的冲突面——本报告不涉及升级决策

## 附：主要信息来源

- 上游文档：`docs/references/http_api_reference.md`、`docs/references/python_api_reference.md`、`docs/guides/data_source/*`、`docs/guides/dataset/files_dataset_document_management.md`、`docs/release_notes.md`（github.com/infiniflow/ragflow）
- 上游 issue/PR：#17815（文档版本管理需求，open）、#19540、#16908（file2document replace 模式，内部接口）、#12566
- 本 fork 源码核实：`api/apps/__init__.py`（认证类型与蓝图注册）、`api/apps/restful_apis/{dataset,document,connector}_api.py`、`docker/entrypoint.sh`、`rag/svr/sync_data_source.py`、`sdk/python/ragflow_sdk/`、`portal-extension/portal/{gateway,tasks,db,config}.py`
