#!/usr/bin/env bash
# 使用优云智算服务器上已有的 Redis 和 Kafka，不另外安装。
#
# Redis:
#   /workspace/software/redis-7.2.5/src/redis-server /workspace/software/redis-7.2.5/redis.conf
# Kafka:
#   /workspace/software/kafka_2.13-4.4.0/bin/kafka-server-start.sh /workspace/software/kafka_2.13-4.4.0/config/server.properties
#
#   ./services.sh start
#   ./services.sh cli
#   ./services.sh once 你好
#   ./services.sh logs
#   ./services.sh status
#   ./services.sh stop-worker
#   ./services.sh stop
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RUNTIME="$ROOT/.runtime"
REDIS_HOME="${REDIS_HOME:-/workspace/software/redis-7.2.5}"
KAFKA_HOME="${KAFKA_HOME:-/workspace/software/kafka_2.13-4.4.0}"
REDIS_SERVER="$REDIS_HOME/src/redis-server"
REDIS_CONF="$REDIS_HOME/redis.conf"
REDIS_CLI="$REDIS_HOME/src/redis-cli"
KAFKA_CONF="$KAFKA_HOME/config/server.properties"
WORKER_PID="$RUNTIME/worker.pid"
WORKER_LOG="$RUNTIME/worker.log"
PY="${PYTHON:-python3}"

export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
export TZ="${TZ:-Asia/Shanghai}"
export PYTHONUNBUFFERED=1

usage() {
  cat <<EOF
使用服务器上已有的 Redis 和 Kafka：
  Redis  $REDIS_HOME
  Kafka  $KAFKA_HOME

  ./services.sh start         按原配置启动 Redis、Kafka，并创建对话 topic、启动 worker
  ./services.sh cli           交互发消息
  ./services.sh once 你好      发一句后等待回复
  ./services.sh logs          看 worker 日志
  ./services.sh status
  ./services.sh stop-worker   只停 worker，Kafka 继续收消息
  ./services.sh stop          停止 worker、Kafka、Redis
EOF
}

require_software() {
  if [ ! -x "$REDIS_SERVER" ] || [ ! -f "$REDIS_CONF" ]; then
    echo "找不到 Redis。期望命令：" >&2
    echo "  $REDIS_SERVER $REDIS_CONF" >&2
    exit 1
  fi
  if [ ! -x "$KAFKA_HOME/bin/kafka-server-start.sh" ] || [ ! -f "$KAFKA_CONF" ]; then
    echo "找不到 Kafka。期望命令：" >&2
    echo "  $KAFKA_HOME/bin/kafka-server-start.sh $KAFKA_CONF" >&2
    exit 1
  fi
}

redis_up() {
  "$REDIS_CLI" -h localhost ping 2>/dev/null | grep -q PONG
}

kafka_port_open() {
  timeout 1 bash -c 'echo >/dev/tcp/127.0.0.1/9092' >/dev/null 2>&1 \
    || timeout 1 bash -c 'echo >/dev/tcp/localhost/9092' >/dev/null 2>&1
}

kafka_up() {
  "$KAFKA_HOME/bin/kafka-topics.sh" --bootstrap-server "$KAFKA_BOOTSTRAP" --list >/dev/null 2>&1
}

worker_running() {
  [ -f "$WORKER_PID" ] && kill -0 "$(cat "$WORKER_PID")" 2>/dev/null
}

start_redis() {
  if redis_up; then
    echo "Redis 已在运行"
    return
  fi
  mkdir -p "$RUNTIME"
  # redis.conf 里如果是 daemonize no，这条命令会留在前台，所以放到后台。
  nohup "$REDIS_SERVER" "$REDIS_CONF" >>"$RUNTIME/redis.log" 2>&1 &
  echo $! > "$RUNTIME/redis.pid"
  local i
  for i in $(seq 1 30); do
    if redis_up; then
      echo "Redis 已启动: $REDIS_SERVER $REDIS_CONF"
      return
    fi
    sleep 0.5
  done
  echo "Redis 没有在 localhost:6379 响应。请看 $RUNTIME/redis.log 和 $REDIS_CONF 里的 port、requirepass。" >&2
  exit 1
}

start_kafka() {
  if kafka_up; then
    echo "Kafka 已在运行"
    return
  fi
  if ! kafka_port_open; then
    "$KAFKA_HOME/bin/kafka-server-start.sh" -daemon "$KAFKA_CONF"
    echo "已按原配置后台启动 Kafka"
  fi
  local i
  for i in $(seq 1 60); do
    if kafka_port_open; then
      break
    fi
    sleep 1
  done
  if ! kafka_port_open; then
    echo "Kafka 端口 9092 没有打开。日志在 $KAFKA_HOME/logs" >&2
    exit 1
  fi
  for i in $(seq 1 15); do
    if kafka_up; then
      echo "Kafka 已就绪: $KAFKA_BOOTSTRAP"
      return
    fi
    sleep 2
  done
  echo "Kafka 进程已起来，但 $KAFKA_BOOTSTRAP 还不能列出 topic。日志在 $KAFKA_HOME/logs" >&2
  exit 1
}

create_topics() {
  local topic existing
  existing=$("$KAFKA_HOME/bin/kafka-topics.sh" --bootstrap-server "$KAFKA_BOOTSTRAP" --list)
  for topic in chat.events chat.replies; do
    if printf '%s\n' "$existing" | grep -qx "$topic"; then
      echo "topic 已存在: $topic"
      continue
    fi
    "$KAFKA_HOME/bin/kafka-topics.sh" --create \
      --topic "$topic" \
      --bootstrap-server "$KAFKA_BOOTSTRAP" \
      --partitions 3 \
      --replication-factor 1
    echo "已创建 topic: $topic"
  done
}

start_worker() {
  if worker_running; then
    echo "worker 已在运行，pid=$(cat "$WORKER_PID")"
    return
  fi
  mkdir -p "$RUNTIME"
  cd "$ROOT"
  nohup "$PY" chat_worker.py >>"$WORKER_LOG" 2>&1 &
  echo $! > "$WORKER_PID"
  echo "worker 已在后台运行，日志: $WORKER_LOG"
}

cmd_start() {
  require_software
  start_redis
  start_kafka
  create_topics
  start_worker
  echo "可以开始对话: ./services.sh cli"
}

stop_worker() {
  if worker_running; then
    kill "$(cat "$WORKER_PID")" 2>/dev/null || true
    local i
    for i in $(seq 1 20); do
      worker_running || break
      sleep 0.2
    done
    if worker_running; then
      kill -9 "$(cat "$WORKER_PID")" 2>/dev/null || true
    fi
  fi
  rm -f "$WORKER_PID"
}

cmd_stop() {
  stop_worker
  echo "worker 已停止"
  if [ -x "$KAFKA_HOME/bin/kafka-server-stop.sh" ]; then
    "$KAFKA_HOME/bin/kafka-server-stop.sh" || true
    echo "已发送 Kafka 停止信号"
  fi
  if [ -x "$REDIS_CLI" ] && redis_up; then
    "$REDIS_CLI" -h localhost shutdown || echo "Redis 停止失败，可手动执行: $REDIS_CLI -h localhost shutdown"
  fi
  rm -f "$RUNTIME/redis.pid"
}

cmd_status() {
  require_software
  if redis_up; then
    echo "Redis  localhost:6379  运行中"
  else
    echo "Redis  localhost:6379  未运行"
  fi
  if kafka_port_open && kafka_up; then
    echo "Kafka  $KAFKA_BOOTSTRAP  运行中"
  else
    echo "Kafka  $KAFKA_BOOTSTRAP  未运行"
  fi
  if worker_running; then
    echo "worker pid=$(cat "$WORKER_PID")  运行中"
  else
    echo "worker  未运行"
  fi
}

cmd_logs() {
  mkdir -p "$RUNTIME"
  touch "$WORKER_LOG"
  tail -n 50 -f "$WORKER_LOG"
}

cmd_cli() {
  cd "$ROOT"
  exec "$PY" chat_cli.py "$@"
}

cmd_once() {
  cd "$ROOT"
  exec "$PY" chat_cli.py --once "$*"
}

main() {
  local cmd="${1:-}"
  if [ $# -gt 0 ]; then
    shift
  fi
  case "$cmd" in
    start) cmd_start "$@" ;;
    stop-worker) stop_worker; echo "worker 已停止，Kafka 仍在运行" ;;
    stop) cmd_stop "$@" ;;
    status) cmd_status "$@" ;;
    logs) cmd_logs "$@" ;;
    cli) cmd_cli "$@" ;;
    once) cmd_once "$@" ;;
    *) usage ;;
  esac
}

main "$@"
