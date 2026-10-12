#!/bin/bash
# 闲鱼自动回复系统 - macOS 一键启动脚本
# 用法: bash 启动.command   (或在 Finder 中双击)
# 改代码希望保存后自动生效时，改用: bash 启动-开发.command

cd "$(dirname "$0")"
ROOT="$(pwd)"

check_health() { curl -s -m 3 "http://127.0.0.1:$1/health" 2>/dev/null | grep -q "服务运行正常"; }
port_busy() { lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
port_pid() { lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }
port_cwd() { local pid; pid=$(port_pid "$1"); [ -n "$pid" ] && lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-; }
started_here() { case "$(port_cwd "$1")" in "$ROOT"*) return 0;; *) return 1;; esac }

# ============ 启动前自检（2026-10-10 坑位固化） ============

# 1. 废纸篓防护：绝不允许从 ~/.Trash 里的旧副本启动
case "$ROOT" in
  */.Trash/*|*/Trash/*)
    echo "❌ 当前项目位于废纸篓：$ROOT"
    echo "   请回到正式项目目录再启动，避免定时任务跑旧副本代码。"
    exit 1;;
esac

# 2. 关键文件存在性：确认这是完整的新版项目（scheduler 曾缺 xianyu_personal_publisher.py）
MISSING=0
for f in scheduler/main.py backend-web/main.py websocket/main.py \
         backend-web/app/services/pan/quark_pan_client.py \
         backend-web/app/services/material_pool_service.py \
         backend-web/app/services/xianyu_personal_publisher.py; do
  [ -f "$f" ] || { echo "❌ 缺少关键文件：$f（当前目录可能不是最新的项目副本）"; MISSING=1; }
done
[ "$MISSING" = "1" ] && { echo "已中止，请确认项目目录完整性。"; exit 1; }

# 3. scheduler venv 依赖检查（个人卖家发布器动态加载 backend-web 模块链，缺包会发布失败）
if ! scheduler/.venv/bin/python -c "import cv2, Crypto, httpx" 2>/dev/null; then
  echo "⚠️ scheduler venv 缺少依赖（cv2 / pycryptodome / httpx），正在补装..."
  scheduler/.venv/bin/pip install -q opencv-python-headless pycryptodome httpx \
    || echo "❌ 补装失败，请手动执行: scheduler/.venv/bin/pip install opencv-python-headless pycryptodome httpx"
fi

# ============ 服务启动 ============

# 确保 MySQL / Redis 已启动
brew services list 2>/dev/null | grep -q "^mysql.*started" || brew services start mysql
brew services list 2>/dev/null | grep -q "^redis.*started" || brew services start redis

# start_svc <名称> <端口> <启动命令> <日志文件>
start_svc() {
  local name=$1 port=$2 cmd=$3 log=$4 cwd
  if check_health $port; then
    if started_here $port; then
      echo "[$name] 已在运行 ✅"
    else
      cwd=$(port_cwd $port)
      echo "[$name] ⚠️ 健康检查通过，但占用进程跑的是别的目录："
      echo "        $cwd"
      echo "        可能正在运行旧副本/废纸篓副本，代码不是本目录的最新版！"
      echo "        如需切换：先 ./停止.command，再重新运行本脚本（不会自动 kill）。"
    fi
  elif port_busy $port; then
    cwd=$(port_cwd $port)
    echo "[$name] ⚠️ 端口 $port 被占用且健康检查失败：PID=$(port_pid $port)，运行目录：${cwd:-未知}"
    echo "        请先运行 停止.command，或手动 kill 该进程。"
  else
    nohup $cmd > "$log" 2>&1 &
    echo "[$name] 启动中 ($port)"
  fi
}

# 1. backend-web (8089)
start_svc backend-web 8089 "backend-web/.venv/bin/python backend-web/main.py" backend-web/logs_start.log

# 2. websocket (8090) - 依赖 backend-web
for i in $(seq 1 60); do check_health 8089 && break; sleep 2; done
start_svc websocket 8090 "websocket/.venv/bin/python websocket/main.py" websocket/logs_start.log

# 3. scheduler (8091)
start_svc scheduler 8091 "scheduler/.venv/bin/python scheduler/main.py" scheduler/logs_start.log

# 4. frontend (9000)
if port_busy 9000; then echo "[frontend] 已在运行 (9000)";
else (cd frontend && nohup /opt/homebrew/bin/npm run dev > /tmp/xianyu_frontend.log 2>&1 &); echo "[frontend] 启动中 (9000)"; fi

echo ""
echo "等待服务就绪..."
for p in 8089 8090 8091; do
  for i in $(seq 1 60); do check_health $p && { echo "  端口 $p ✅"; break; }; sleep 3; done
done
for i in $(seq 1 20); do curl -s -m 3 http://127.0.0.1:9000/ >/dev/null 2>&1 && { echo "  端口 9000 ✅"; break; }; sleep 2; done

echo ""
echo "==============================================="
echo "  管理后台:   http://localhost:9000"
echo "  默认账号:   admin / admin123"
echo "  API 文档:   http://localhost:8089/docs"
echo "  停止服务:   双击 停止.command"
echo "==============================================="
