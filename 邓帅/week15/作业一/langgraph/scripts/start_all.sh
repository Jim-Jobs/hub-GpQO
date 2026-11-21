#!/usr/bin/env bash
# langgraph 套一键启动（无 Temporal 依赖，直连编排）：
#   进程1 (:8102) → 进程2 (:8103) → 网关 (:8101) → 前端 (:5174)
# health 轮询就绪再拉下一个；日志落各自 logs/；重复执行安全。

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

# ---------- 1. 进程1 情感识别 :8102 ----------
http_ok http://127.0.0.1:8102/health || {
  (cd "$ROOT/sentiment-service" && nohup "$PY" -m uvicorn main:app \
    --host 127.0.0.1 --port 8102 > "$LOGS/sentiment-service.log" 2>&1 &)
  log "进程1 情感识别启动中…"
}
wait_http http://127.0.0.1:8102/health "进程1 情感识别"

# ---------- 2. 进程2 情感回应 :8103 ----------
http_ok http://127.0.0.1:8103/health || {
  (cd "$ROOT/respond-service" && nohup "$PY" -m uvicorn main:app \
    --host 127.0.0.1 --port 8103 > "$LOGS/respond-service.log" 2>&1 &)
  log "进程2 情感回应启动中…"
}
wait_http http://127.0.0.1:8103/health "进程2 情感回应"

# ---------- 3. 网关（StateGraph + AsyncSqliteSaver）:8101 ----------
http_ok http://127.0.0.1:8101/health || {
  (cd "$ROOT/gateway" && nohup "$PY" -m uvicorn main:app \
    --host 127.0.0.1 --port 8101 > "$LOGS/gateway.log" 2>&1 &)
  log "网关启动中…"
}
wait_http http://127.0.0.1:8101/health "网关（StateGraph 直连）"

# ---------- 4. 前端 :5174 ----------
export PATH="$HOME/.nvs/default/bin:$PATH"
if ! lsof -ti :5174 > /dev/null 2>&1; then
  (cd "$ROOT/frontend" && nohup pnpm run dev --host 127.0.0.1 --port 5174 \
    > "$LOGS/frontend.log" 2>&1 &)
  log "前端 dev server 启动中…"
fi
wait_http http://127.0.0.1:5174/ "前端（vite :5174）"

log "全部就绪：前端 http://127.0.0.1:5174 · 网关 :8101 · 进程1 :8102 · 进程2 :8103"
