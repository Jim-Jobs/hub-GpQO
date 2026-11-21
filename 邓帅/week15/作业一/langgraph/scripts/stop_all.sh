#!/usr/bin/env bash
# langgraph 套一键停止（幂等）：前端 5174 → 网关 8101 → 进程1 8102 → 进程2 8103。

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

kill_port 5174 "前端"
kill_port 8101 "网关"
kill_port 8102 "进程1 情感识别"
kill_port 8103 "进程2 情感回应"

echo "全部停止。"
