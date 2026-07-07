#!/bin/bash
# Portal 启动脚本 — 加载 .env 后启动 uvicorn。
# Slice 22 部署修复:portal main.py 不加载 .env,需在启动前 source .env
# 否则 PORTAL_ADMIN_PASSWORD 等环境变量为空,导致登录失败("密码错误")。
cd ~/portal-extension
source .venv/bin/activate
set -a
source .env
set +a
exec python -m uvicorn portal.main:app --host 0.0.0.0 --port 8000
