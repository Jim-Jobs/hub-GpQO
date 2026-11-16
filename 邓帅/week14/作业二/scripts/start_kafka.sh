#!/usr/bin/env bash
# 启动 Kafka（brew 安装的 KRaft 单节点）并幂等创建 topic：rag-data × 3 分区
# 注：设计原为 docker compose（见 docker-compose.yml），因本机 Docker Hub 拉取受限
#     （无代理 + 国内加速源限流），降级为 Homebrew Kafka —— 行为完全一致。
set -euo pipefail

BOOTSTRAP=${KAFKA_BOOTSTRAP:-localhost:9092}
TOPIC=${KAFKA_TOPIC:-rag-data}
BIN=/opt/homebrew/opt/kafka/bin
CONF=/opt/homebrew/etc/kafka/server.properties

if ! command -v kafka-server-start >/dev/null 2>&1 && [ ! -x "$BIN/kafka-server-start" ]; then
  echo "❌ 未找到 kafka（brew install kafka）"; exit 1
fi
export PATH="/opt/homebrew/opt/kafka/bin:$PATH"

if pgrep -f "kafka.Kafka" >/dev/null 2>&1; then
  echo "✓ Kafka 已在运行"
else
  echo "→ 启动 Kafka (KRaft)…"
  nohup kafka-server-start "$CONF" >/tmp/week14-kafka.log 2>&1 &
  for i in $(seq 1 30); do
    sleep 1
    pgrep -f "kafka.Kafka" >/dev/null && break
  done
  pgrep -f "kafka.Kafka" >/dev/null || { echo "❌ 启动失败，看 /tmp/week14-kafka.log"; exit 1; }
fi

for i in $(seq 1 30); do
  kafka-topics --bootstrap-server "$BOOTSTRAP" --list >/dev/null 2>&1 && break
  sleep 1
done

if kafka-topics --bootstrap-server "$BOOTSTRAP" --list 2>/dev/null | grep -qx "$TOPIC"; then
  echo "✓ topic $TOPIC 已存在"
else
  kafka-topics --bootstrap-server "$BOOTSTRAP" --create --topic "$TOPIC" --partitions 3 --replication-factor 1
  echo "✓ topic $TOPIC 已创建（3 分区）"
fi
kafka-topics --bootstrap-server "$BOOTSTRAP" --describe --topic "$TOPIC" | head -3
