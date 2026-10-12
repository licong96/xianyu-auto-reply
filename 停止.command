#!/bin/bash
# 闲鱼自动回复系统 - macOS 一键停止脚本

cd "$(dirname "$0")"
ROOT="$(pwd)"

pkill -f "backend-web/main.py" 2>/dev/null && echo "已停止 backend-web"
pkill -f "websocket/main.py" 2>/dev/null && echo "已停止 websocket"
pkill -f "scheduler/main.py" 2>/dev/null && echo "已停止 scheduler"
pkill -f "xianyu-admin-frontend" 2>/dev/null
lsof -tiTCP:9000 -sTCP:LISTEN | xargs kill 2>/dev/null && echo "已停止 frontend"

# 开发热重载时，真正占端口的是子进程，命令行里不一定带 main.py。
# 只清理工作目录属于本项目的监听进程，避免误杀别的程序。
stop_project_port() {
  local port=$1 name=$2
  local pid cwd ppid pcmd
  pid=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1)
  [ -z "$pid" ] && return
  cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-)
  case "$cwd" in
    "$ROOT"*) ;;
    *) return ;;
  esac
  ppid=$(ps -o ppid= -p "$pid" | tr -d ' ')
  pcmd=$(ps -o command= -p "$ppid" 2>/dev/null || true)
  case "$pcmd" in
    *main.py*) kill "$ppid" 2>/dev/null || true ;;
  esac
  kill "$pid" 2>/dev/null && echo "已停止 $name (端口 $port)"
}
sleep 0.4
stop_project_port 8089 backend-web
stop_project_port 8090 websocket
stop_project_port 8091 scheduler

# MySQL / Redis 是系统服务，默认保留运行（如想一并停止，取消下面两行注释）
# brew services stop mysql
# brew services stop redis

echo "全部应用服务已停止"
