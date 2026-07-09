# 部署运维

## 生产拓扑

当前生产环境以 `172.16.10.180` 为主机，nginx 对外监听 80：

```text
浏览器 -> nginx :80
  /portal/                         -> portal uvicorn :8000
  /portal/assets/                  -> portal uvicorn :8000
  /api/v1/chatbots|agentbots/...   -> portal uvicorn :8000
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

## 一键部署

在本地执行：

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension
bash deploy.sh
```

脚本做四件事：

1. `rsync --delete` 同步后端代码，排除 `.env`、`.venv`、`*.db`、日志和缓存。
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

# 后端健康，未登录可能返回 401/403，能连通即可进一步看日志
ssh 172.16.10.180 'curl -s -o /dev/null -w "portal -> %{http_code}\n" http://localhost:8000/me'
```

登录验证需要真实管理员密码：

```bash
ssh 172.16.10.180 'curl -s -X POST http://localhost:8000/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"<pwd>\"}" -w "\nHTTP %{http_code}\n"'
```

## 修改用户密码

当前没有改密码 UI 或 API。管理员需要在服务器上生成新的 bcrypt hash，并更新 `portal_user.password_hash`。

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

### 历史会话重启后消失

检查 `PORTAL_DB_URL`。如果仍是默认 `sqlite://`，说明使用了内存数据库。

### 修改前端后生产没变化

确认已经运行 `npm run build`，且 `deploy.sh` 同步了 `frontend/dist`。

### API 偶发返回 HTML 导致前端 JSON 解析失败

`NoCacheHtmlMiddleware` 已给 HTML 响应加 `Cache-Control: no-store` 和 `Vary: Accept`。若复现，检查是否绕过了 portal 后端或旧进程仍在运行。
