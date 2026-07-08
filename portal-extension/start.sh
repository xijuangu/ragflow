#!/bin/bash
# Portal 启动脚本 — 加载 .env 后启动 uvicorn。
# Slice 22 部署修复:portal main.py 不加载 .env,需在启动前 source .env
# 否则 PORTAL_ADMIN_PASSWORD 等环境变量为空,导致登录失败("密码错误")。
# Slice 35 部署脚本固化:启动前用 pgrep 精确清理旧 portal 进程,
# 避免 pkill -f 误伤含该字符串的 ssh 会话(导致 ssh 退出码 255)。

cd ~/portal-extension

# 杀旧 portal 进程(用 pgrep 精确匹配,不误伤 ssh 会话)
# pgrep 只返回 PID,不会匹配 ssh 命令行中含该字符串的会话
OLD_PIDS=$(pgrep -f "python -m uvicorn portal.main:app" || true)
if [ -n "$OLD_PIDS" ]; then
  echo "killing old portal pids: $OLD_PIDS"
  kill $OLD_PIDS 2>/dev/null || true
  # 等待端口释放
  sleep 2
fi

source .venv/bin/activate
set -a
source .env
set +a
exec python -m uvicorn portal.main:app --host 0.0.0.0 --port 8000
