#!/usr/bin/env bash
# 多模态问答的启动脚本。和 services.sh 分开。
# 使用优云智算上已经启动的 Redis 和 Kafka，不另外安装、不另起服务。
#
# Redis  /workspace/software/redis-7.2.5          localhost:6379
# Kafka  /workspace/software/kafka_2.13-4.4.0    localhost:9092
# 模型   /models/Qwen3.5-0.8B
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -f "$ROOT/worker.py" ] && [ -f "$ROOT/api.py" ]; then
  APP="$ROOT"
elif [ -f "$ROOT/mmrag/worker.py" ] && [ -f "$ROOT/mmrag/api.py" ]; then
  APP="$ROOT/mmrag"
else
  echo "问答程序要和 run_rag.sh 放在同一个目录。" >&2
  echo "请把桌面 rag 文件夹里的这些文件全部上传到 $ROOT ：" >&2
  echo "  run_rag.sh worker.py api.py common.py indexlib.py index.html" >&2
  exit 1
fi
REDIS_HOME="${REDIS_HOME:-/workspace/software/redis-7.2.5}"
KAFKA_HOME="${KAFKA_HOME:-/workspace/software/kafka_2.13-4.4.0}"
REDIS_CLI="$REDIS_HOME/src/redis-cli"

export MODEL_PATH="${MODEL_PATH:-/models/Qwen3.5-0.8B}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export PORT="${PORT:-8080}"
export PYTHONUNBUFFERED=1

if ! "$REDIS_CLI" -h localhost ping 2>/dev/null | grep -q PONG; then
  echo "Redis 还没在 localhost:6379 上运行。请先执行原来的启动命令：" >&2
  echo "  $REDIS_HOME/src/redis-server $REDIS_HOME/redis.conf" >&2
  exit 1
fi
if ! "$KAFKA_HOME/bin/kafka-topics.sh" --bootstrap-server "$KAFKA_BOOTSTRAP" --list >/dev/null 2>&1; then
  echo "Kafka 还没在 $KAFKA_BOOTSTRAP 上运行。请先执行原来的启动命令：" >&2
  echo "  $KAFKA_HOME/bin/kafka-server-start.sh $KAFKA_HOME/config/server.properties" >&2
  exit 1
fi

existing=$("$KAFKA_HOME/bin/kafka-topics.sh" --bootstrap-server "$KAFKA_BOOTSTRAP" --list)
if ! printf '%s\n' "$existing" | grep -qx "pdf.parse"; then
  "$KAFKA_HOME/bin/kafka-topics.sh" --create \
    --topic pdf.parse \
    --bootstrap-server "$KAFKA_BOOTSTRAP" \
    --partitions 1 \
    --replication-factor 1
  echo "已在当前 Kafka 上创建 topic pdf.parse"
fi

cd "$APP"
python3 worker.py &
worker_pid=$!
trap 'kill "$worker_pid" 2>/dev/null || true' EXIT
echo "解析进程 pid=$worker_pid"
echo "Redis $REDIS_URL"
echo "Kafka $KAFKA_BOOTSTRAP"
echo "打开 http://127.0.0.1:${PORT}"
exec python3 api.py
