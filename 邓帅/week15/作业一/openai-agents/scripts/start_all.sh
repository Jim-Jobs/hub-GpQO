#!/usr/bin/env bash
# openai-agents 套一键启动（按依赖顺序，health 轮询就绪再拉下一个）：
#   temporal dev server (:7233 / UI :8233) → 进程1 (:8002) → 进程2 (:8003)
#   → Temporal worker（无端口）→ 网关 (:8001) → 前端 (:5173)
# 日志与 PID 落各自 logs/；重复执行是安全的（已就绪的组件跳过）。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY=/opt/miniconda3/envs/py312/bin/python
LOGS="$ROOT/logs"
mkdir -p "$LOGS"
export PYTHONUNBUFFERED=1

log() { echo "[$(date '+%H:%M:%S')] $*"; }

http_ok() { curl -sf -o /dev/null "$1"; }

wait_http() { # $1=url $2=名称 $3=超时秒(默认60)
  local i=0 max=${3:-60}
  until http_ok "$1"; do
    i=$((i + 1))
    if [ "$i" -ge "$max" ]; then log "❌ $2 就绪超时（$1）"; exit 1; fi
    sleep 1
  done
  log "✓ $2 就绪（$1）"
}

# ---------- 0. temporal dev server ----------
if ! temporal operator cluster health 2>/dev/null | grep -q SERVING; then
  nohup temporal server start-dev \
    --db-filename "$LOGS/temporal-dev.sqlite" --log-level error \
    > "$LOGS/temporal-server.log" 2>&1 &
  echo $! > "$LOGS/temporal-server.pid"
  log "temporal dev server 启动中…"
fi
for i in $(seq 30); do
  temporal operator cluster health 2>/dev/null | grep -q SERVING && break
  sleep 1
done
temporal operator cluster health 2>/dev/null | grep -q SERVING \
  || { log "❌ temporal server 未就绪"; exit 1; }
log "✓ temporal dev server（:7233，Web UI http://127.0.0.1:8233）"

# ---------- 1. 进程1 情感识别 :8002 ----------
http_ok http://127.0.0.1:8002/health || {
  (cd "$ROOT/sentiment-service" && nohup "$PY" -m uvicorn main:app \
    --host 127.0.0.1 --port 8002 > "$LOGS/sentiment-service.log" 2>&1 &)
  log "进程1 情感识别启动中…"
}
wait_http http://127.0.0.1:8002/health "进程1 情感识别"

# ---------- 2. 进程2 情感回应 :8003 ----------
http_ok http://127.0.0.1:8003/health || {
  (cd "$ROOT/respond-service" && nohup "$PY" -m uvicorn main:app \
    --host 127.0.0.1 --port 8003 > "$LOGS/respond-service.log" 2>&1 &)
  log "进程2 情感回应启动中…"
}
wait_http http://127.0.0.1:8003/health "进程2 情感回应"

# ---------- 3. Temporal worker（无 HTTP 端口，等 task queue 有 worker 注册）----------
if ! pgrep -f "worker/main.py" > /dev/null; then
  (cd "$ROOT" && nohup "$PY" worker/main.py > "$LOGS/worker.log" 2>&1 &)
  log "Temporal worker 启动中…"
  for i in $(seq 30); do
    grep -q "worker 启动" "$LOGS/worker.log" 2>/dev/null && break
    sleep 1
  done
fi
log "✓ Temporal worker（task queue emotion-oa）"

# ---------- 4. 网关 :8001 ----------
http_ok http://127.0.0.1:8001/health || {
  (cd "$ROOT/gateway" && nohup "$PY" -m uvicorn main:app \
    --host 127.0.0.1 --port 8001 > "$LOGS/gateway.log" 2>&1 &)
  log "网关启动中…"
}
wait_http http://127.0.0.1:8001/health "网关（Temporal client）"

# ---------- 5. 前端 :5173 ----------
export PATH="$HOME/.nvs/default/bin:$PATH"
if ! lsof -ti :5173 > /dev/null 2>&1; then
  (cd "$ROOT/frontend" && nohup pnpm run dev --host 127.0.0.1 --port 5173 \
    > "$LOGS/frontend.log" 2>&1 &)
  log "前端 dev server 启动中…"
fi
wait_http http://127.0.0.1:5173/ "前端（vite :5173）"

log "全部就绪：前端 http://127.0.0.1:5173 · 网关 :8001 · Temporal UI http://127.0.0.1:8233"
