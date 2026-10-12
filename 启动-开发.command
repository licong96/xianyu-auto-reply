#!/bin/bash
# 闲鱼自动回复系统 - 开发启动脚本
# 用法: bash 启动-开发.command   (或在 Finder 中双击)
#
# 与 启动.command 使用同一组端口：
#   backend-web 8089 / websocket 8090 / scheduler 8091 / frontend 9000
# Python 服务保存 .py 后自动重载；前端 Vite 本身就会热更新。
# 停服务仍用 停止.command。

cd "$(dirname "$0")"
ROOT="$(pwd)"

check_health() { curl -s -m 3 "http://127.0.0.1:$1/health" 2>/dev/null | grep -q "服务运行正常"; }
port_busy() { lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
port_pid() { lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }
port_cwd() { local pid; pid=$(port_pid "$1"); [ -n "$pid" ] && lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-; }
started_here() { case "$(port_cwd "$1")" in "$ROOT"*) return 0;; *) return 1;; esac }

# 停掉本项目占用的端口。热重载时监听进程的父进程才是重载器，要一起停。
stop_ours() {
  local port=$1
  local pid ppid pcmd
  port_busy "$port" || return 0
  if ! started_here "$port"; then
    echo "❌ 端口 $port 被其他目录占用：$(port_cwd "$port")"
    echo "   请先处理该进程，再运行本脚本。"
    exit 1
  fi
  pid=$(port_pid "$port")
  ppid=$(ps -o ppid= -p "$pid" | tr -d ' ')
  pcmd=$(ps -o command= -p "$ppid" 2>/dev/null || true)
  case "$pcmd" in
    *main.py*) kill "$ppid" 2>/dev/null || true ;;
  esac
  kill "$pid" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    port_busy "$port" || return 0
    sleep 0.3
  done
  echo "❌ 端口 $port 仍被占用，已中止"
  exit 1
}

# ============ 启动前自检（与 启动.command 相同） ============

case "$ROOT" in
  */.Trash/*|*/Trash/*)
    echo "❌ 当前项目位于废纸篓：$ROOT"
    echo "   请回到正式项目目录再启动，避免定时任务跑旧副本代码。"
    exit 1;;
esac

MISSING=0
for f in scheduler/main.py backend-web/main.py websocket/main.py \
         backend-web/app/services/pan/quark_pan_client.py \
         backend-web/app/services/material_pool_service.py \
         backend-web/app/services/xianyu_personal_publisher.py; do
  [ -f "$f" ] || { echo "❌ 缺少关键文件：$f（当前目录可能不是最新的项目副本）"; MISSING=1; }
done
[ "$MISSING" = "1" ] && { echo "已中止，请确认项目目录完整性。"; exit 1; }

if ! scheduler/.venv/bin/python -c "import cv2, Crypto, httpx" 2>/dev/null; then
  echo "⚠️ scheduler venv 缺少依赖（cv2 / pycryptodome / httpx），正在补装..."
  scheduler/.venv/bin/pip install -q opencv-python-headless pycryptodome httpx \
    || echo "❌ 补装失败，请手动执行: scheduler/.venv/bin/pip install opencv-python-headless pycryptodome httpx"
fi

brew services list 2>/dev/null | grep -q "^mysql.*started" || brew services start mysql
brew services list 2>/dev/null | grep -q "^redis.*started" || brew services start redis

echo "开发模式：Python 保存后自动重载，端口与日常启动相同"
echo ""

# 本项目已占用的 Python 端口先停掉，再以热重载方式拉起
for spec in "8089:backend-web" "8090:websocket" "8091:scheduler"; do
  port=${spec%%:*}
  name=${spec#*:}
  if port_busy "$port"; then
    echo "[$name] 正在切换到热重载..."
    stop_ours "$port"
  fi
done

start_dev_svc() {
  local name=$1 port=$2 dir=$3
  # 每个服务只把自己的目录放在 PYTHONPATH 前面，避免三个 main.py 互相抢模块名
  XY_DEV_RELOAD=1 PYTHONPATH="$ROOT/$dir:$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    nohup "$ROOT/$dir/.venv/bin/python" "$ROOT/$dir/main.py" > "$ROOT/$dir/logs_start.log" 2>&1 &
  echo "[$name] 热重载启动中 ($port)"
}

start_dev_svc backend-web 8089 backend-web
for i in $(seq 1 60); do check_health 8089 && break; sleep 2; done
start_dev_svc websocket 8090 websocket
start_dev_svc scheduler 8091 scheduler

if port_busy 9000; then
  if started_here 9000; then
    echo "[frontend] 已在运行 (9000)，Vite 会自动热更新"
  else
    echo "❌ 端口 9000 被其他目录占用：$(port_cwd 9000)"
    exit 1
  fi
else
  (cd frontend && nohup /opt/homebrew/bin/npm run dev > /tmp/xianyu_frontend.log 2>&1 &)
  echo "[frontend] 启动中 (9000)"
fi

echo ""
echo "等待服务就绪..."
for p in 8089 8090 8091; do
  for i in $(seq 1 60); do check_health $p && { echo "  端口 $p ✅"; break; }; sleep 3; done
  check_health $p || echo "  端口 $p ❌ 未就绪，请查看对应 logs_start.log"
done
for i in $(seq 1 20); do curl -s -m 3 http://127.0.0.1:9000/ >/dev/null 2>&1 && { echo "  端口 9000 ✅"; break; }; sleep 2; done

echo ""
echo "==============================================="
echo "  开发模式已启动（保存 .py / 前端文件会自动生效）"
echo "  管理后台:   http://localhost:9000"
echo "  API 文档:   http://localhost:8089/docs"
echo "  停止服务:   双击 停止.command"
echo "==============================================="
