# Handoff — RAGFlow Portal 同源部署修复(Phase 3)

> 生成时间:2026-07-06。接手前必读:`CONTEXT.md`(项目级约束/约定/已知限制)、`docs/archive/ISSUES.md` Phase 3 区段、`docs/archive/PRD.md` L21/L120-128/L131-133。
> 本文档只记录操作细节与当前环境状态,项目记忆见 `CONTEXT.md`。

## 1. 项目位置与入口

- 项目根:`/Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension`
- 后端入口:`portal/main.py`(`app = create_app()`),`portal/routes.py`(路由),`portal/gateway.py`(网关/SSE 代理/TokenStore)
- 前端入口:`frontend/src/main.tsx`(Router basename),`frontend/src/api/client.ts`(API_BASE),`frontend/vite.config.ts`(base)
- 配置:`portal/config.py`(`load_settings`),`.env`(本地)/`~/.env`(服务器)
- Issue 跟踪:`docs/archive/ISSUES.md`(本地文件作 tracker,Issue 1-30 + TD1-TD20)
- PRD:`docs/archive/PRD.md`

## 2. 当前部署架构(172.16.10.180)

详见 `CONTEXT.md` §1。本文档只补充操作命令。

### 2.1 nginx 配置(Slice 18 放宽后,服务器 `~/portal-nginx/conf.d/default.conf`)

```nginx
# Slice 18:统一代理 /api/v1/(chatbots|agentbots)/* → portal:8000
#   - portal 按 T_short 决定换 beta Token 或透传(原生分享页无 T_short)
#   - SSE 子规则(/completions)保留 proxy_buffering off
location ~ ^/api/v1/(chatbots|agentbots)/[^/]+/completions$ {
    proxy_pass http://portal:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;           # SSE 流式必须关
    proxy_cache off;
    proxy_read_timeout 300s;
    chunked_transfer_encoding on;
}

# 非 SSE 子路径(/info、/inputs)走普通代理
location ~ ^/api/v1/(chatbots|agentbots)/ {
    proxy_pass http://portal:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

> 注:Nginx `location ~` 正则按声明顺序匹配,先匹配到的生效,因此 `/completions$` 规则须在通用 `/` 规则之前。

## 3. 待修复的 3 个 bug(详见 ISSUES.md Phase 3,勿重复此处的根因)

- **B2 → Slice 17**(独立,先做):portal `SessionMiddleware` 未传 `session_cookie`,与 RAGFlow 都用默认 `session`,同源 cookie 踩踏。修复:`portal/main.py:89` 加 `session_cookie="portal_session"`。
- **B1 + B3 → Slice 18**(依赖 17):网关只代理 `/completions`,缺 `/info`/`/inputs`(B1);nginx 把所有 completions 路由到 portal,原生分享页无 portal 会话 → 403(B3)。修复:网关统一代理 `/api/v1/(chatbots|agentbots)/*`,有 T_short 换 beta Token,无 T_short 透传 RAGFlow;nginx 路由改为 `~ ^/api/v1/(chatbots|agentbots)/`。

用户实际反馈现象:① iframe 仍跳 RAGFlow 登录页;② 登录 RAGFlow 后刷新 portal 登出;③ 直接访问 RAGFlow 原生分享页 `http://172.16.10.180/chats/share?shared_id=xxx` 发消息 403「未登录」。

iframe 实际嵌入代码(用户提供):
```html
<iframe src="http://172.16.10.180/chats/share?shared_id=b4f88272768011f1a3bdc51e5c67581c&from=chat&auth=FbUMKuqDoLNcx_dSUbN7fSoZPZcXZql2&theme=light" style="width:100%;height:100%;min-height:600px" frameborder="0"></iframe>
```
注意 URL 路径是 `/chats/share`(非 `/chat/share`),`auth=` 是 portal 签发的 T_short。

## 4. 本地命令

```bash
# 后端测试(366 passed, 5 skipped — 含 Slice 17 + 18 新增 16 测试)
cd ragflow/portal-extension && .venv/bin/python -m pytest tests/ -x -q

# ruff(全绿)
cd ragflow/portal-extension && .venv/bin/ruff check portal/ tests/

# 前端构建(产物 base = /portal/)
cd ragflow/portal-extension/frontend && npm run build
# 验证 dist/index.html 含 /portal/assets/
head -12 frontend/dist/index.html
```

## 5. 部署命令

```bash
# 同步前端 dist + 后端改动
# Slice 34:portal.db 已移到 ~/portal-data/(代码与数据分离);rsync 兜底加
#   --exclude='*.db' --exclude='.env',防止 --delete 删数据/覆盖服务器 .env
rsync -az --delete --exclude='*.db' --exclude='.env' ragflow/portal-extension/frontend/dist/ 172.16.10.180:~/portal-extension/frontend/dist/
rsync -az --exclude='*.db' --exclude='.env' ragflow/portal-extension/portal/main.py 172.16.10.180:~/portal-extension/portal/main.py
# (改 routes.py/gateway.py/models.py 时同步对应文件)
rsync -az --exclude='*.db' --exclude='.env' ragflow/portal-extension/portal/{gateway.py,models.py,routes.py} 172.16.10.180:~/portal-extension/portal/

# 重启 portal(注意:start.sh 用 exec,不会自动 pkill 旧进程;必须先 pkill 再 start)
ssh 172.16.10.180 'pkill -f "uvicorn portal.main:app"; sleep 2; nohup bash ~/portal-extension/start.sh > ~/portal-extension/portal.log 2>&1 < /dev/null &'

# curl 验证(服务器本地)
ssh 172.16.10.180 'curl -s -H "Accept: text/html" -o /dev/null -w "%{http_code} %{content_type}\n" http://127.0.0.1/portal/share-pages'
```

### 5.1 RAGFlow bot_api 扩展端点部署(Slice 27 — docker cp 临时替换)

RAGFlow 容器是官方 v0.26.0 镜像,不含本地 fork 在 Slice 2/5 加的 chatbot sessions 端点
(`GET/PATCH/DELETE /api/v1/chatbots/<dialog_id>/sessions/<session_id>`)。
Slice 27 通过 docker cp 把本地 `api/apps/restful_apis/bot_api.py` 替换容器内版本。

```bash
# 1. 备份容器内原文件(带时间戳)
ssh 172.16.10.180 'TS=$(date +%Y%m%d-%H%M%S); docker exec docker-ragflow-cpu-1 cp /ragflow/api/apps/restful_apis/bot_api.py /ragflow/api/apps/restful_apis/bot_api.py.bak.$TS'

# 2. cp 本地 bot_api.py 到容器
scp ragflow/api/apps/restful_apis/bot_api.py 172.16.10.180:/tmp/bot_api.py
ssh 172.16.10.180 'docker cp /tmp/bot_api.py docker-ragflow-cpu-1:/ragflow/api/apps/restful_apis/bot_api.py && rm /tmp/bot_api.py'

# 3. 重启 RAGFlow api server(容器内 pkill,entrypoint 自动拉起)
ssh 172.16.10.180 'docker exec docker-ragflow-cpu-1 pkill -f ragflow_server.py; sleep 15'

# 4. 验证端点存在(返回 200,非 404)
ssh 172.16.10.180 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1/api/v1/chatbots/<dialog_id>/sessions/<session_id>'
```

**运维约束(重要):**
- docker cp 替换在容器重建(`docker compose down/up`、`docker restart` 不会丢,但 `down -v` 或重新 `up` 镜像会丢)时会丢失。
- 若容器重建,需重新执行上述 cp 步骤,或改为 volume 挂载 / rebuild 镜像。
- 容器内备份文件 `bot_api.py.bak.<timestamp>` 是回滚点,回滚命令:
  `docker exec docker-ragflow-cpu-1 bash -c "cp /ragflow/api/apps/restful_apis/bot_api.py.bak.<timestamp> /ragflow/api/apps/restful_apis/bot_api.py && pkill -f ragflow_server.py"`
- agent 类型(`ragflow_type=agent`)的 sessions 端点仍有已知问题:portal 网关 `_ragflow_bot_segment` 对 agent 返回 "agentbots",但 RAGFlow 官方只有 `/agents/<id>/sessions/<sid>`(非 `/agentbots/`)。chat 类型已修复,agent 类型留作后续 issue。

## 6. 已完成的 Phase 3 前置工作(本轮)

- 前端改子路径:`vite.config.ts` base `/portal/`、`main.tsx` Router basename、`client.ts` API_BASE、`LoginPage.tsx` SSO 跳转 —— 本地 build 成功,服务器 dist 已含 `/portal/assets/`。
- nginx 容器 + RAGFlow :8080 + portal :8000 三层就绪,路由 curl 验证通过(`/`→RAGFlow, `/portal/`→portal)。
- portal 后端新增 `spa_html_fallback` 中间件([portal/main.py:112-126](portal/main.py)):浏览器导航(Accept: text/html)命中 API 返回 JSON 时改返 index.html,解决刷新 `/portal/share-pages` 返回 JSON 的问题。350 测试通过。
- 上述改动已 rsync + 重启到服务器,curl 验证 SPA 兜底生效。

## 7. 下一步(接手即做)

### 7.1 已完成(本轮,Slice 17 + 18 代码实现 + 单测)

1. **Slice 17 ✅**:`portal/main.py` SessionMiddleware 加 `session_cookie="portal_session"`;新增 `tests/test_slice17_session_cookie.py`(2 测试);更新 `tests/test_slice1_e2e.py` cookie 名断言。
2. **Slice 18 ✅**(代码 + 单测全绿,366 passed + 5 skipped):
   - `portal/gateway.py`:`proxy_sse_to_ragflow` 加透传分支(无 T_short 或 token 不在 TokenStore → 透传 RAGFlow,保留原始 Authorization);新增 `_build_passthrough_headers`、`_build_sse_passthrough_response`、`proxy_bot_json_to_ragflow`(JSON 代理 /info + /inputs,复用校验链 + 透传分支)。
   - `portal/routes.py`:新增 `GET /api/v1/chatbots/{dialog_id}/info` 与 `GET /api/v1/agentbots/{agent_id}/inputs` 代理端点。
   - 新增 `tests/test_slice18_bot_api_proxy.py`(14 测试:T_short 换 beta Token / 透传 / 校验链拒绝 / agent /inputs / SSE 透传 / 上游错误码透传)。
   - 更新 `tests/test_slice1_e2e.py` 与 `tests/test_slice16_widget_agent.py`:原「无 T_short → 401」断言改为「透传」断言;`test_proxy_rejects_expired_token` / `test_proxy_rejects_revoked_token`(T_short 在 TokenStore 但无效 → 401)仍保持。
   - ruff 全绿(自动修复 3 个 import 问题)。

### 7.2 待做(部署 + E2E,需在服务器 172.16.10.180 上操作)

1. **rsync 代码到服务器并重启 portal**:
   ```bash
   rsync -az --exclude='*.db' --exclude='.env' ragflow/portal-extension/portal/main.py ragflow/portal-extension/portal/gateway.py ragflow/portal-extension/portal/routes.py 172.16.10.180:~/portal-extension/portal/
   ssh 172.16.10.180 'bash ~/portal-extension/start.sh'
   ```
2. **改服务器 nginx 配置**(`~/portal-nginx/conf.d/default.conf`):按本文 §2.1 的两个 `location ~` 块替换原仅匹配 `/completions$` 的规则,然后 `docker exec portal-nginx nginx -s reload`。
3. **浏览器 E2E 验证**:
   - portal iframe 不再跳 RAGFlow 登录页(`/info`、`/inputs`、`/completions` 全部经 portal,有 T_short 换 beta Token)。
   - 直接访问 RAGFlow 原生分享页 `http://172.16.10.180/chats/share?shared_id=xxx` 发消息不再 403(无 portal T_short → 透传,RAGFlow 自身 beta Token / session cookie 处理)。
   - 登录 RAGFlow 后再访问 portal 不会登出(B2 cookie 名隔离)。

## 8. 关键代码位置(实现 Slice 18 时必读)

- `portal/gateway.py`:`TokenStore`、`IPRateLimiter`、`_proxy_sse_public_core`(SSE 校验链核心)、`proxy_sse_public_to_ragflow`(公开分享页 SSE)、`*_session_via_ragflow`(chat/agent session 历史拉取)
- `portal/routes.py:831-848`:`POST /api/v1/chatbots/{dialog_id}/completions` 代理端点(挂 cookie 校验 + grant + T_short → 换 beta Token → 调 RAGFlow)
- RAGFlow 侧(只读,不改):`api/apps/restful_apis/bot_api.py:226` `/chatbots/<id>/info`(`@login_required(AUTH_BETA)`)、`api/apps/__init__.py:172-184` AUTH_BETA 校验(`APIToken.query(beta=auth_token)`)
- RAGFlow 前端(只读):`web/src/utils/authorization-util.ts:50-57` `getAuthorization()` 优先读 URL `?auth=`,回退 localStorage;`web/src/pages/next-chats/share/index.tsx:43` 分享页挂载调 `useFetchExternalChatInfo` → `GET /api/v1/chatbots/{id}/info`

## 9. Suggested skills

- **`to-issues`**:如需把 Slice 17/18 进一步拆分或新增 Phase 3 后续 issue,用此 skill。
- **`diagnosing-bugs`**:Slice 18 实现后若 E2E 仍有 iframe 跳转问题,用此 skill 的诊断循环定位(重点查 RAGFlow 前端 axios 401 拦截器 `web/src/utils/next-request.ts:150-167`)。
- **`tdd`**:Slice 18 的「有 T_short / 无 T_short」分发逻辑适合先写测试(网关单测),再实现透传分支。
- **`review`**:Slice 17 + 18 实现完成后,用此 skill 对 Phase 3 改动做 Standards + Spec review。

## 10. 注意事项

- 不要改 RAGFlow 侧代码(`ragflow/web/`、`ragflow/api/`)—— PRD 明确 RAGFlow 零修改,令牌注入走原生 `getAuthorization()` 的 `?auth=` 注入点。
- 服务器 172.16.10.180 防火墙已放行 80 + 8000(用户手动 ufw allow)。
- RAGFlow :8080 绑定在 `172.16.10.180:8080`(非 0.0.0.0),服务器本地 curl 须用 `http://172.16.10.180:8080/` 而非 `127.0.0.1:8080`。
- portal 进程重启才会加载新 dist(StaticFiles 在启动时读取目录)。
- nginx 容器若有同名 stopped 容器,`docker rm -f portal-nginx` 后重建。
- **工具限制**:本环境的 Edit/Write/Delete 操作被限制在工作目录内,无法写入 `/tmp`。handoff 文档已写到 `docs/HANDOFF-phase3.md`(而非系统临时目录)。
