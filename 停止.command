#!/bin/bash
# 闲鱼自动回复系统 - macOS 一键停止脚本

pkill -f "backend-web/main.py" 2>/dev/null && echo "已停止 backend-web"
pkill -f "websocket/main.py" 2>/dev/null && echo "已停止 websocket"
pkill -f "scheduler/main.py" 2>/dev/null && echo "已停止 scheduler"
pkill -f "xianyu-admin-frontend" 2>/dev/null
lsof -tiTCP:9000 -sTCP:LISTEN | xargs kill 2>/dev/null && echo "已停止 frontend"

# MySQL / Redis 是系统服务，默认保留运行（如想一并停止，取消下面两行注释）
# brew services stop mysql
# brew services stop redis

echo "全部应用服务已停止"
