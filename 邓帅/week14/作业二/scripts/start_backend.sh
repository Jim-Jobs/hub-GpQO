#!/usr/bin/env bash
# 启动后端：FastAPI(:8001) + 3 个 Kafka worker（独立进程同消费者组）
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-/opt/miniconda3/envs/py312/bin/python}
export PYTHONPATH=backend/src
export PATH="/opt/homebrew/opt/kafka/bin:$PATH"

mkdir -p logs

# mineru 本地解析服务（4.x 为 client-server 架构；CLI 入口在 conda bin）
MINERU="$(dirname "$PY")/mineru"
if ! "$MINERU" server status >/dev/null 2>&1 || "$MINERU" server status 2>&1 | grep -q "未运行"; then
  "$MINERU" server start || true
fi

pkill -f "uvicorn mrag.api.main" 2>/dev/null || true
pkill -f "mrag.worker.main" 2>/dev/null || true
sleep 1

nohup "$PY" -m uvicorn mrag.api.main:app --host 127.0.0.1 --port 8001 > logs/api.log 2>&1 &
echo "✓ API pid=$! -> logs/api.log"

for i in 1 2 3; do
  nohup "$PY" -m mrag.worker.main > "logs/worker$i.log" 2>&1 &
  echo "✓ worker$i pid=$! -> logs/worker$i.log"
done
echo "等待就绪…"
sleep 6
curl -s http://127.0.0.1:8001/api/documents | head -c 200 && echo ""
tail -2 logs/worker1.log
