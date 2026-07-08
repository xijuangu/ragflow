#!/bin/bash
# Portal 部署脚本 — Slice 35 部署脚本固化。
# 单条 `bash deploy.sh` 完成:rsync 代码 + 前端 dist → 远程重启 → 健康检查。
# start.sh 内置 pgrep 清理旧进程,不再依赖 pkill(避免误杀 ssh 会话)。

set -euo pipefail

# 远程服务器配置(单引号防止 ~ 被本地 shell 展开,交由远程 shell 展开)
REMOTE_HOST=172.16.10.180
REMOTE_DIR='~/portal-extension'

# 本地代码根目录(脚本所在目录即 portal-extension)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="$SCRIPT_DIR"

# rsync exclude 列表(后端代码同步用,前端 dist 单独同步故此处 exclude)
RSYNC_EXCLUDES=(
  --exclude='.git'
  --exclude='node_modules'
  --exclude='frontend/node_modules'
  --exclude='__pycache__'
  --exclude='.pytest_cache'
  --exclude='*.pyc'
  --exclude='.venv'
  --exclude='*.db'
  --exclude='.env'
  --exclude='portal.log'
  --exclude='.ruff_cache'
  --exclude='frontend/dist'
)

echo "=== 1. 同步后端代码到 $REMOTE_HOST:$REMOTE_DIR ==="
# --delete 保持远端与本地一致(删除远端已删除的文件),配合 exclude 保护 .env/.venv/portal.db
rsync -az --delete "${RSYNC_EXCLUDES[@]}" "$LOCAL_DIR/" "$REMOTE_HOST:$REMOTE_DIR/"
echo "后端代码同步完成"

echo "=== 2. 同步前端 dist ==="
if [ -d "$LOCAL_DIR/frontend/dist" ]; then
  rsync -az "$LOCAL_DIR/frontend/dist/" "$REMOTE_HOST:$REMOTE_DIR/frontend/dist/"
  echo "前端 dist 同步完成"
else
  echo "警告:本地 frontend/dist 不存在,跳过前端同步"
  echo "如需更新前端,请先在 frontend/ 目录执行 npm run build"
fi

echo "=== 3. 远程重启 portal(start.sh 内置 pgrep 清理旧进程) ==="
# 关键:用 `ssh -f` 让 ssh 本身后台化(执行命令前 fork 到后台,命令完成 ssh 退出)。
# 纯 `setsid`/`nohup &` 方案在 uvicorn 长期进程上仍挂起 —— ssh 远端 shell 退出后
# 仍等待继承 stdout fd(portal.log)的后台进程。`ssh -f` 从 ssh 客户端侧解决:
# ssh 进程立即后台化,远端命令 `nohup ... &` 后台化 uvicorn,远端 shell 退出,
# ssh 在后台等待命令返回(因 `&` 远端 shell 立即退出)后自行关闭。
# `</dev/null` 重定向 ssh stdin 避免密码交互挂起(要求密钥认证)。
# start.sh 会先 pgrep 杀旧 uvicorn,再 exec 启动新进程。
ssh -f "$REMOTE_HOST" "cd $REMOTE_DIR && nohup bash start.sh > portal.log 2>&1 </dev/null &" </dev/null
echo "远程重启命令已发送(ssh -f 后台执行,不阻塞)"

echo "=== 4. 健康检查(最多 5 次,每次间隔 2s) ==="
# 经 nginx /portal/share-pages(Accept: text/html 触发 SPA fallback 返回 200)
# 200 = portal 完全就绪(后端 + 前端 dist);401 = 后端就绪但前端 dist 未部署
# 连接失败/502 = portal 未启动
HEALTH_URL="http://127.0.0.1/portal/share-pages"
HEALTH_OK=false
FINAL_CODE=000
for i in 1 2 3 4 5; do
  CODE=$(ssh "$REMOTE_HOST" "curl -s -o /dev/null -w '%{http_code}' -H 'Accept: text/html' '$HEALTH_URL' 2>/dev/null" || echo "000")
  echo "第 $i 次健康检查:HTTP $CODE"
  FINAL_CODE=$CODE
  case "$CODE" in
    200)
      echo "portal 完全就绪(后端 + 前端 dist)"
      HEALTH_OK=true
      break
      ;;
    401)
      echo "portal 后端已就绪(前端 dist 未部署,HTTP 401,后端健康)"
      HEALTH_OK=true
      break
      ;;
    *)
      sleep 2
      ;;
  esac
done

if [ "$HEALTH_OK" = true ]; then
  echo "=== 部署成功(portal 已启动,HTTP $FINAL_CODE) ==="
  exit 0
fi

echo "=== 部署失败:健康检查未通过(HTTP $FINAL_CODE) ==="
echo "=== portal.log 最近 20 行 ==="
ssh "$REMOTE_HOST" "tail -20 $REMOTE_DIR/portal.log" || true
exit 1
