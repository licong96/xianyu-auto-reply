#!/bin/bash
# 闲鱼自动回复系统 - macOS 一键启动脚本
# 用法: bash 启动.command   (或在 Finder 中双击)

cd "$(dirname "$0")"
ROOT="$(pwd)"

check_health() { curl -s -m 3 "http://127.0.0.1:$1/health" 2>/dev/null | grep -q "服务运行正常"; }
port_busy() { lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# 确保 MySQL / Redis 已启动
brew services list 2>/dev/null | grep -q "^mysql.*started" || brew services start mysql
brew services list 2>/dev/null | grep -q "^redis.*started" || brew services start redis

# 1. backend-web (8089)
if check_health 8089; then echo "[backend-web] 已在运行";
elif port_busy 8089; then echo "[backend-web] 端口被占用但健康检查失败，请先运行 停止.command";
else nohup backend-web/.venv/bin/python backend-web/main.py > backend-web/logs_start.log 2>&1 & echo "[backend-web] 启动中 (8089)"; fi

# 2. websocket (8090) - 依赖 backend-web
for i in $(seq 1 60); do check_health 8089 && break; sleep 2; done
if check_health 8090; then echo "[websocket] 已在运行";
elif port_busy 8090; then echo "[websocket] 端口被占用但健康检查失败，请先运行 停止.command";
else nohup websocket/.venv/bin/python websocket/main.py > websocket/logs_start.log 2>&1 & echo "[websocket] 启动中 (8090)"; fi

# 3. scheduler (8091)
if check_health 8091; then echo "[scheduler] 已在运行";
elif port_busy 8091; then echo "[scheduler] 端口被占用但健康检查失败，请先运行 停止.command";
else nohup scheduler/.venv/bin/python scheduler/main.py > scheduler/logs_start.log 2>&1 & echo "[scheduler] 启动中 (8091)"; fi

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
