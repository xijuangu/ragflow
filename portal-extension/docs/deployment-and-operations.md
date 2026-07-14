# 部署运维

## 生产拓扑

当前生产环境以 `172.16.10.180` 为主机，nginx 对外监听 80：

```text
浏览器 -> nginx :80
  /portal/                         -> portal uvicorn :8000
  /portal/assets/                  -> portal uvicorn :8000
  /api/v1/chatbots|agentbots/...   -> portal uvicorn :8000
  /api/v1/thumbnails               -> portal uvicorn :8000
  /api/v1/documents/{id}/preview   -> portal uvicorn :8000
  /api/v1/documents/images/...     -> portal uvicorn :8000
  /                                -> RAGFlow web :8080
```

组件：

| 组件 | 说明 |
|---|---|
| `portal-nginx` | nginx 容器，对外统一入口。 |
| `portal` | 宿主机 uvicorn 进程，运行 `portal.main:app`。 |
| `docker-ragflow-cpu-1` | RAGFlow 官方容器。 |
| `docker-mysql-1` | RAGFlow MySQL。 |
| `docker-es01-1` | Elasticsearch。 |
| `docker-minio-1` | MinIO。 |
| `docker-redis-1` | Redis。 |

Portal 上游访问 RAGFlow 使用 `RAGFLOW_HOST`，浏览器 iframe 地址使用 `RAGFLOW_BROWSER_ORIGIN`。同源部署时 `RAGFLOW_BROWSER_ORIGIN` 留空。

## nginx 引用资源分流

Issue 82/85 增加的三个入口必须先到 Portal；如果仍落到 RAGFlow :8080，`pt_` 会被 RAGFlow 当作无效 token 返回 401，前端随后跳到 `/login`。在 `~/portal-nginx/conf.d/default.conf` 中保留现有 chat/agent 规则，并增加：

```nginx
location = /api/v1/thumbnails {
    proxy_pass http://172.17.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location ^~ /api/v1/documents/images/ {
    proxy_pass http://172.17.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Issue 85 的 preview 精确 location 以版本库中的
[`deployment/nginx-document-preview.conf`](../deployment/nginx-document-preview.conf)
为准；把该片段合并到同一个 nginx `server` 块。它只匹配单段 `document_id` 的
`/preview`，不能改成覆盖整个 `/api/v1/documents/` 的宽泛代理。

`proxy_pass` 不带 URI 尾部，原始路径和 `portal_ticket` query 会完整保留。修改后先检查再 reload：

```bash
ssh 172.16.10.180 'docker exec portal-nginx nginx -t'
ssh 172.16.10.180 'docker exec portal-nginx nginx -s reload'
```

2026-07-14 的 Issue 82 生产变更已应用；变更前配置备份为 `~/portal-nginx/conf.d/default.conf.bak.issue82-20260714`。

## 一键部署

在本地执行：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
bash deploy.sh
```

脚本做四件事：

1. `rsync --delete` 同步后端代码，排除 `.env`、`.venv`、`*.db`、日志、缓存和 `project-materials`。
2. 如果本地存在 `frontend/dist`，同步前端构建产物。
3. 用 `ssh -f` 远程执行 `start.sh`，避免 uvicorn 后台进程挂住本地 shell。
4. 循环健康检查 `/portal/share-pages`。

如果修改了前端，部署前先构建：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/frontend
npm run build

cd ..
bash deploy.sh
```

## 服务器启动脚本

服务器侧 `~/portal-extension/start.sh` 会：

1. 进入 `~/portal-extension`。
2. 用 `pgrep -f "python -m uvicorn portal.main:app"` 找旧 portal 进程并 kill。
3. 激活 `.venv`。
4. `source .env`。
5. `exec python -m uvicorn portal.main:app --host 0.0.0.0 --port 8000`。

`start.sh` 依赖 `.env` 存在。缺失 `.env` 时，管理员密码等配置会为空，最常见表现是登录一直“密码错误”。

## 部署前检查

```bash
ssh 172.16.10.180 'test -f ~/portal-extension/.env && echo ".env exists" || echo "FATAL: .env missing"'
ssh 172.16.10.180 'grep -E "^(PORTAL_DB_URL|RAGFLOW_HOST|RAGFLOW_BROWSER_ORIGIN)=" ~/portal-extension/.env'
ssh 172.16.10.180 'ls -la ~/portal-data/portal.db 2>/dev/null || echo "portal.db missing"'
```

生产 `PORTAL_DB_URL` 推荐：

```bash
PORTAL_DB_URL=sqlite:////home/xijuangu/portal-data/portal.db
```

不要把 `portal.db` 放在 `~/portal-extension` 代码目录内。

## 部署后验证

```bash
# 进程和端口
ssh 172.16.10.180 'pgrep -f "uvicorn portal.main:app" | head -1'
ssh 172.16.10.180 'ss -tlnp 2>/dev/null | grep :8000'

# nginx 可访问
ssh 172.16.10.180 'curl -s -o /dev/null -w "nginx /portal/ -> %{http_code}\n" http://localhost:80/portal/'

# 引用资源路径必须由 Portal 接管；无登录/令牌时 401/403 是预期，404 表示路由未部署
ssh 172.16.10.180 'curl -s -o /dev/null -w "thumbnails -> %{http_code}\n" "http://localhost:80/api/v1/thumbnails?doc_ids=probe"'
ssh 172.16.10.180 'curl -s -o /dev/null -w "document preview -> %{http_code}\n" http://localhost:80/api/v1/documents/probe/preview'
ssh 172.16.10.180 'curl -s -o /dev/null -w "document image -> %{http_code}\n" http://localhost:80/api/v1/documents/images/probe'

# 后端健康，未登录可能返回 401/403，能连通即可进一步看日志
ssh 172.16.10.180 'curl -s -o /dev/null -w "portal -> %{http_code}\n" http://localhost:8000/me'
```

登录验证需要真实管理员密码：

```bash
ssh 172.16.10.180 'curl -s -X POST http://localhost:8000/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"<pwd>\"}" -w "\nHTTP %{http_code}\n"'
```

## 修改用户密码

优先使用管理后台：`/portal/admin/users` → 用户行里的“改密码”。

也可以用 API 修改。接口会更新 `portal_user.password_hash` 并写 `user_password_change` 审计：

```bash
curl -s -c /tmp/portal-cookie.txt \
  -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"<admin-password>\"}" \
  -w "\nHTTP %{http_code}\n"

curl -s -X PATCH http://localhost:8000/admin/users/<user_id>/password \
  -H "Content-Type: application/json" \
  -b /tmp/portal-cookie.txt \
  -d "{\"password\":\"<new-password>\"}" \
  -w "\nHTTP %{http_code}\n"
```

如果无法使用 UI/API，再在服务器上生成新的 bcrypt hash 并直接更新数据库。

推荐在服务器交互式执行，避免把新密码写进 shell 历史：

```bash
ssh -t 172.16.10.180
cd ~/portal-extension
source .venv/bin/activate
set -a
source .env
set +a
read -p "username: " TARGET_USERNAME
read -s -p "new password: " NEW_PASSWORD
echo
TARGET_USERNAME="$TARGET_USERNAME" NEW_PASSWORD="$NEW_PASSWORD" python -c 'import os; from sqlalchemy import create_engine; from sqlalchemy.orm import sessionmaker; from portal.db import PortalUserModel; from portal.password import hash_password; db=os.environ["PORTAL_DB_URL"]; kwargs={"connect_args":{"check_same_thread":False}} if db.startswith("sqlite") else {}; engine=create_engine(db, future=True, **kwargs); Session=sessionmaker(bind=engine, future=True); session=Session(); user=session.query(PortalUserModel).filter_by(username=os.environ["TARGET_USERNAME"]).one_or_none(); assert user is not None, "user not found"; user.password_hash=hash_password(os.environ["NEW_PASSWORD"]); session.commit(); print("password updated for", user.username)'
unset TARGET_USERNAME NEW_PASSWORD
```

改完后用新密码验证登录：

```bash
curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"<username>\",\"password\":\"<new-password>\"}" \
  -w "\nHTTP %{http_code}\n"
```

注意：`PORTAL_ADMIN_PASSWORD` 和 `PORTAL_USER2_PASSWORD` 只用于种子用户初始化/启动时补齐；已有用户的密码以数据库里的 `password_hash` 为准。
`build_seed_data` 只在固定 ID 用户不存在时写入种子用户；用户已存在时，修改 `.env` 中的密码不会自动覆盖数据库中的 `password_hash`。

## RAGFlow 容器补丁

如果生产依赖 chatbot sessions 扩展端点：

```text
GET    /api/v1/chatbots/{dialog_id}/sessions/{session_id}
PATCH  /api/v1/chatbots/{dialog_id}/sessions/{session_id}
DELETE /api/v1/chatbots/{dialog_id}/sessions/{session_id}
```

这些端点不属于官方镜像默认能力，需要确认容器内 `bot_api.py` 是否已替换。`docker restart` 通常不丢，容器重建会丢。

备份、替换、重启 RAGFlow API server 的历史命令记录在 `CONTEXT.md`。执行前应重新核对当前容器名、文件路径和备份点。

### RAGFlow Web 主题补丁

Issue 83 的 Portal 浅色默认值同时依赖 Portal iframe URL 中的 `default_theme=light` 和本仓库 RAGFlow `web/` 对该参数的初始化支持。仅部署 Portal 会使 URL 参数存在，但旧 RAGFlow dist 不会识别它。

部署步骤：

1. 在 `ragflow/web` 执行 `npm run build`。
2. 将 `web/dist` 打包并传到 RAGFlow 主机。
3. 备份容器内 `/ragflow/web/dist` 为带时间戳的 `dist.bak.*`。
4. 替换 dist 后在容器内执行 `nginx -s reload`。
5. 用全新浏览器上下文只登录 Portal，验证“劳动法”的新会话和历史会话背景均为白色，且初始化 class 记录不经过 `dark`。

该补丁不增加环境变量。回滚时需同时回滚 Portal 代码和 RAGFlow web dist；只回滚一侧会留下无效参数或恢复黑色默认界面。

## 回滚

Portal 代码回滚：

1. 回到上一版本代码。
2. 重新构建前端 dist。
3. 执行 `bash deploy.sh`。

RAGFlow 容器补丁回滚：

1. 找到容器内 `bot_api.py.bak.<timestamp>`。
2. 覆盖回 `/ragflow/api/apps/restful_apis/bot_api.py`。
3. 重启 RAGFlow API server。

前端 dist 回滚：

1. 找到容器或服务器中的 dist 备份。
2. 恢复后 reload nginx 或重启对应服务。

## 常见问题

### 登录一直密码错误

优先检查服务器 `.env` 是否存在，是否包含 `PORTAL_ADMIN_PASSWORD`。

### 分享页 iframe 闪一下后进入 RAGFlow 登录页

通常是 iframe URL 绕过门户代理，直接打到 RAGFlow，导致 RAGFlow 不认识 `pt_` 门户令牌。检查 `RAGFLOW_BROWSER_ORIGIN` 和 nginx `/api/v1/...` 代理。

如果只有“打开含引用的历史会话”才跳登录页，检查 nginx 是否同时把 `/api/v1/thumbnails` 和 `/api/v1/documents/images/` 分流到 Portal，并在浏览器 Network 中确认 sessions、thumbnails、images 均无 401。

如果引用内容正常显示，但点击回答末尾的文档卡片后跳登录页，检查 `GET /api/v1/documents/{document_id}/preview` 是否被精确分流到 Portal。该请求携带 `pt_`；若直接进入 RAGFlow 会返回 401，并触发 iframe 跳转 `/login`。

### 历史会话重启后消失

检查 `PORTAL_DB_URL`。如果仍是默认 `sqlite://`，说明使用了内存数据库。

### 修改前端后生产没变化

确认已经运行 `npm run build`，且 `deploy.sh` 同步了 `frontend/dist`。

如果变化位于 RAGFlow `web/`（例如 Portal 嵌入主题），还要替换 RAGFlow 容器内 `/ragflow/web/dist` 并 reload 容器 nginx；Portal 的 `deploy.sh` 不会同步这份产物。

### API 偶发返回 HTML 导致前端 JSON 解析失败

`NoCacheHtmlMiddleware` 已给 HTML 响应加 `Cache-Control: no-store` 和 `Vary: Accept`。若复现，检查是否绕过了 portal 后端或旧进程仍在运行。
