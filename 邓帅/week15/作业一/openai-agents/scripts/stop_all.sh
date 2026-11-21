#!/usr/bin/env bash
# openai-agents 套一键停止：按端口/进程名清理全部组件（幂等）。
#   前端 5173 → 网关 8001 → 进程1 8002 → 进程2 8003 → worker → temporal server

set -uo pipefail

kill_port() { # $1=端口 $2=名称
  local pids
  pids=$(lsof -ti :"$1" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    sleep 1
    kill -9 $pids 2>/dev/null || true
    echo "✓ 已停止 $2（:$1）"
  else
    echo "- $2（:$1）未在运行"
  fi
}

kill_port 5173 "前端"
kill_port 8001 "网关"
kill_port 8002 "进程1 情感识别"
kill_port 8003 "进程2 情感回应"

if pgrep -f "worker/main.py" > /dev/null; then
  pkill -f "worker/main.py" && echo "✓ 已停止 Temporal worker"
else
  echo "- Temporal worker 未在运行"
fi

if pgrep -f "temporal server start-dev" > /dev/null; then
  pkill -f "temporal server start-dev" && echo "✓ 已停止 temporal dev server"
else
  echo "- temporal dev server 未在运行"
fi

echo "全部停止。"
