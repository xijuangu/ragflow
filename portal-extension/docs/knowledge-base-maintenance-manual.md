# 知识库文档维护手册（人工定期更新）

> 版本：2026-09-15 首版
> 适用环境：172.16.10.180 生产环境（RAGFlow v0.26.0 + portal）
> 背景：2026-09-15 决策——知识库文档更新**不建自动化模块**，采用人工定期维护（可行性调研见 [knowledge-base-auto-update-research.md](knowledge-base-auto-update-research.md)，该调研的 API 结论仍然有效，本手册的更新规程以其为依据）。

---

## 1. 必须知道的三件事

在开始任何操作前，先理解 RAGFlow 的三条硬约束（来源：调研报告 §3/§4，已对 fork 源码核实）：

1. **RAGFlow 没有「替换文档内容」的功能。** Web UI 和 API 都只能改文档的名称/元数据/解析配置，不能替换内容。「更新一篇文档」= **删旧文档 + 上传新文件 + 触发解析**，且文档的 `document_id` 一定会变。
2. **document_id 变化的代价：历史会话里的引用图片失效。** 旧文档删除后，历史会话中该文档的缩略图、预览、引用图片会 404——这是**预期行为，不是故障**。引用的文字内容（chunk 快照）保留在会话消息里，不受影响；新会话完全不受影响。portal 侧无需任何操作。
3. **同名上传不去重。** 直接上传同名文件会产生两篇同名文档并存的副本，检索时会同时命中新旧两版条文，导致回答引用废止条文。**所有更新操作必须走 §6 的七步法**，禁止「传完就走」。

另外一条经济性约束：全部 8 个生产知识库的解析配置都启用了 **RAPTOR + GraphRAG（light）**（LLM 为 deepseek-v4-flash，chunk 512 token），**单篇文档解析需要数分钟并消耗 LLM token**。批量更新要预留时间窗口，逐篇串行操作，不要一次触发几十篇解析。

---

## 2. 维护对象清单

本地语料（本工作区根目录）与 RAGFlow 知识库的对应关系：

| 本地语料目录 | RAGFlow 知识库 | 本地文件数 | 库内文档数（2026-09-15 基线） | 对账状态 |
|---|---|---|---|---|
| `cfachina_rules/`（4 个分类目录，含附件） | 中期协法律法规库 | 173 md + 99 附件 = 272 | 272 | ✅ 数量一致 |
| `exchange_rules/上期所/` | 上期所 | 45 | 45 | ✅ |
| `exchange_rules/大商所/` | 大商所 | 44 | 44 | ✅ |
| `exchange_rules/郑商所/` | 郑商所 | 49 | 49 | ✅ |
| `exchange_rules/中金所/` | 中金所 | 37 | 37 | ✅ |
| `exchange_rules/能源中心/` | 能源中心 | 26 | 25 | ⚠️ 库内缺 1，首次维护对账 |
| `exchange_rules/广期所/` | 广期所 | 27 | 27 | ✅ |
| `财务文档/` | 财务 | 11 | 13 | ⚠️ 库内多 2 篇 PDF（增值税法、增值税法实施条例，来自中国政府网），首次维护对账 |

不维护：劳动法、law-test-01、刑法（测试库）。

**附件是独立文档**：`cfachina_rules/` 里每篇 md 引用的附件（pdf/doc/docx/xls，共 99 个）在知识库内各自是一篇独立文档。更新一篇带附件的法规时，若附件本身也变了，附件要单独走一遍更新流程。

---

## 3. 维护节奏

- **月度例行**：每月第一个工作周做一次（中期协 + 证监会新发文驱动）。
- **事件驱动**：得知交易所发布修订公告、证监会发布新规章时随时做（交易所/证监会部分）。
- **操作窗口**：选低峰时段（解析期间该主题检索可能短暂空洞，见 §6 的顺序说明）。

---

## 4. 准备工作

### 4.1 本地环境

- macOS 本机 + python3（脚本只依赖标准库）
- 语料目录即本工作区根目录下的 `cfachina_rules/`、`exchange_rules/`、`财务文档/`
- RAGFlow 管理界面：`http://172.16.10.180:8080`（服务器本地）或经 nginx `http://172.16.10.180/`（首页即 RAGFlow），用 RAGFlow 管理员账号登录

### 4.2 一次性：给语料目录建 git（变更识别的基础）

语料目录此前不在版本控制内，无法回答「上次维护以来哪些文件变了」。每个目录做一次：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/cfachina_rules
printf '__pycache__/\n.DS_Store\n' > .gitignore
git init && git add -A && git commit -m "基线 2026-09-15"

cd ../exchange_rules
printf '.DS_Store\n' > .gitignore
git init && git add -A && git commit -m "基线 2026-09-15"

cd ../财务文档
printf '.DS_Store\n' > .gitignore
git init && git add -A && git commit -m "基线 2026-09-15"
```

此后每次维护：抓取完成后 `git add -A && git commit`，变更清单靠 git 给出（§5.4）。

---

## 5. 第一步：抓取与变更识别

### 5.1 中期协（`cfachina_rules/`）

脚本走中期协 JSON API，幂等（已有附件不重复下载，md 每次重写）：

```bash
cd cfachina_rules
# 把 fetch_cfachina.py 里的 FETCH_DATE = "YYYY-MM-DD" 改成当天日期
python3 fetch_cfachina.py
```

### 5.2 证监会补充发文（`cfachina_rules/`）

证监会新发文没有自动发现机制，靠人工补录清单：

1. 把内容页 URL 逐行追加到 `csrc_urls.txt`，格式：`URL` 或 `URL | 自定义标题 | 分类目录名`（标题可省略，自动从页面推导；分类默认「部门规章和规范性文件」）
2. 运行 `python3 fetch_csrc_docs.py`（幂等）

### 5.3 交易所规则（`exchange_rules/`）

无脚本，按 **[exchange_rules/抓取与维护指南.md](../../exchange_rules/抓取与维护指南.md)** 执行。要点速查：

- 上期所/广期所/中金所/能源中心：官网静态可抓（上期所品种细则有 SafeLine WAF，需浏览器头）
- 大商所/郑商所：官网有 JS 反爬，用 WebFetch 且失败重试 3–5 次；品种细则从 futureslaw.org.cn 抓
- 更新后按指南 §4.3 重新生成 `exchange_rules/README.md` 索引

### 5.4 财务文档（`财务文档/`）

人工放置 doc/pptx/pdf 文件，文件名即文档名。

### 5.5 变更识别（三个目录通用）

抓取完成、提交 git 后：

```bash
# 与上次维护基线对比，忽略 md 头部的「抓取日期」行（全量重抓会改所有文件的这一行）
git diff --name-only <上次维护的commit> -I'抓取日期'
# 新增/删除的文件同样会列出；README.md 等索引文件不入库，忽略
```

产出：**变更清单**（新增 / 内容变化 / 删除 三类），据此执行 §6。若清单为空，本次维护到此结束，记录即可。

---

## 6. 第二步：RAGFlow 更新操作（七步法）

对变更清单里的**每一篇**，在 RAGFlow Web UI 执行（路径：左侧「知识库」→ 点进对应知识库 → 「数据集」文档列表）：

1. **下架旧文档**：在文档列表找到旧文档，把它的「启用」开关关掉。知识库立即不再检索它（chunks 保留，随时可恢复）。
2. **上传新文件**：「本地上传」传入本地新文件。列表会出现同名新文档，处于**未解析**状态——靠「启用状态 + 上传日期」区分新旧。
3. **触发解析**：选中新文档，点「解析」。
4. **等待解析成功**：刷新列表看状态，直到显示解析成功。单篇可能数分钟（RAPTOR + GraphRAG），**逐篇串行，不要批量触发**。
5. **核对结果**：新文档 chunk 数应 > 0；点开文档抽查内容是否为新版条文。
6. **删除旧文档**：确认新文档可用后，删除已下架的旧文档。
7. **失败回滚**：若第 4 步解析失败且重试仍失败（§8.1）——删除新文档，把旧文档「启用」开关重新打开，知识库即刻恢复原状，本次更新记入维护记录的异常栏。

**新增文档**（源站新发文）：无旧文档，直接上传 → 解析 → 核对。
**删除文档**（源站已废止下架）：直接删除库内文档，无需替换。

操作纪律：

- **不要改单篇文档的解析配置**。新上传文档默认继承知识库配置（laws 模板 + RAPTOR + GraphRAG），保持继承即可。
- 「正在解析的文档不能被删除」——等解析终态（成功/失败）后再删。
- 一次维护的更新量若超过约 20 篇，分多天窗口执行，避免 task_executor 堆积与 LLM 额度集中消耗。

---

## 7. 第三步：验收

全部变更处理完后：

1. **数量核对**：各知识库文档数与本地语料文件数一致（对照 §2 表；Web UI 文档列表有计数）。可选精确核对（服务器上执行）：

   ```bash
   ssh 172.16.10.180
   MYSQL_PWD=$(grep '^MYSQL_PASSWORD=' ~/ragflow/docker/.env | cut -d= -f2)
   docker exec docker-mysql-1 mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_PWD" rag_flow -e \
     "SELECT name, doc_num, chunk_num FROM knowledgebase ORDER BY doc_num DESC;"
   ```

2. **无残留失败/未解析**：Web UI 各库文档列表无「解析失败」「未解析」状态。可选 SQL 核对（`run`：0=未开始 1=解析中 2=已取消 3=完成 4=失败）：

   ```bash
   docker exec docker-mysql-1 mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_PWD" rag_flow -e \
     "SELECT kb.name AS kb, d.name AS doc, d.run FROM document d JOIN knowledgebase kb ON d.kb_id=kb.id WHERE d.run <> '3';"
   ```

3. **抽样问答**：在 portal 分享页就刚更新的条文提一个问题，确认回答引用的是新版本。
4. **提交语料基线**：三个语料目录各自 `git add -A && git commit -m "维护 YYYY-MM-DD"`。

---

## 8. 常见问题

### 8.1 解析失败（FAIL）

先在文档列表对该文档**重试解析**一次。仍失败：

```bash
ssh 172.16.10.180
docker logs docker-ragflow-cpu-1 --tail 200 2>&1 | grep -i "fail\|error" | tail -20
```

常见原因：文件损坏（重新抓取/导出）、LLM 服务抖动（稍后重试）、扫描件无文本层（laws 模板不适用，考虑对该篇单独换模板——属例外操作，需在维护记录中注明）。

### 8.2 误操作同名副本

若绕过七步法直接上传了同名文件：不要慌，列表里把**旧的**（上传日期早的）下架并删除即可；若无法区分新旧，点开两篇的「文档内容」对比正文日期。

### 8.3 更新后发现历史会话引用图片打不开

预期行为（§1 第 2 条）。引用文字仍在；如需看原文，按文档名在知识库里查新版即可。无需处理。

### 8.4 证监会新发文不知道该补哪些

定期浏览证监会官网「部门规章/规范性文件」栏目，把新发文内容页 URL 补进 `csrc_urls.txt`（§5.2）。这是人工环节，没有自动发现机制。

---

## 9. 首次维护：基线对账

首次执行本手册时，先处理 §2 表中两处已知漂移，再进入正常流程：

1. **能源中心（库内 25，本地 26）**：对比 `exchange_rules/能源中心/` 文件清单与库内文档清单，找出缺的 1 篇，按「新增文档」补传。
2. **财务（库内 13，本地 11）**：库内多出的 2 篇 PDF（`中华人民共和国增值税法__中国政府网.pdf`、`中华人民共和国增值税法实施条例…公报.pdf`）在本地 `财务文档/` 无对应文件。决定归属：要么下载入库原件存入 `财务文档/`（推荐，保持本地语料为完整事实源），要么确认后从库内删除。

对账 SQL（列出某库全部文档名）：

```bash
docker exec docker-mysql-1 mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_PWD" rag_flow -e \
  "SELECT d.name FROM document d JOIN knowledgebase kb ON d.kb_id=kb.id WHERE kb.name='能源中心' ORDER BY d.name;"
```

---

## 10. 维护记录

每次维护在下表追加一行（本表即台账）：

| 日期 | 操作者 | 范围（知识库） | 变更（新增/更新/删除 篇数） | 结果 | 异常与处置 |
|---|---|---|---|---|---|
| 2026-09-15 | — | — | — | 手册首版，未执行维护 | 已知漂移：能源中心缺 1、财务多 2，待首次维护对账 |

---

## 附：决策与边界记录

- **为什么人工而不是自动化**：2026-09-15 评估后决策不建 portal 同步模块。调研（[knowledge-base-auto-update-research.md](knowledge-base-auto-update-research.md)）确认自动化路线可行，但本期选择人工维护；若未来重启自动化立项，该调研的路线 A 结论与风险清单仍然有效。
- **更新语义**：删旧 → 传新 → 解析 = 唯一的「更新」手段（RAGFlow 无替换 API，上游 issue #17815 仍 open）。
- **portal 不受影响**：知识库内容更新对分享页问答即时生效；portal 无知识库配置项，维护操作无需触碰 portal。
