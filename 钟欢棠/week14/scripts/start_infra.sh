#!/usr/bin/env bash
# 在课程镜像（Linux）里启动真实 Redis + Kafka，供 APP_MODE=kafka 提交作业使用。
# 优先用 Docker；镜像里没有 Docker 时，自动改为原生安装 + KRaft 单节点启动。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo "==> Week14 作业基础设施：Redis + Kafka"

# ---------- 方案 A：有 Docker ----------
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "检测到可用 Docker，执行 docker compose up -d"
  docker compose up -d
  echo "==> 就绪：Redis localhost:6379 / Kafka localhost:9092"
  echo "接下来：pip install -r requirements.txt && python run.py"
  exit 0
fi

echo "未检测到可用 Docker，改为镜像内原生启动（仍使用真实 Redis + Kafka）"

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

# ---------- Redis ----------
if ! command -v redis-server >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "缺少 redis-server 且无法自动安装，请手动安装 Redis 后重试" >&2
    exit 1
  fi
  echo "安装 redis-server ..."
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq redis-server
fi

if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
  echo "Redis 已在运行"
else
  redis-server --daemonize yes --port 6379 --save "" --appendonly no || true
  for _ in $(seq 1 10); do
    if redis-cli ping >/dev/null 2>&1; then break; fi
    sleep 1
  done
  redis-cli ping >/dev/null 2>&1 || { echo "Redis 启动失败，请检查 6379 端口" >&2; exit 1; }
  echo "Redis 已启动（localhost:6379）"
fi

# ---------- Java（Kafka 依赖）----------
if ! command -v java >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "缺少 Java，请手动安装 JRE 后重试" >&2
    exit 1
  fi
  echo "安装 Java 运行环境 ..."
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq default-jre-headless
fi

# ---------- Kafka (KRaft 单节点，无需 ZooKeeper) ----------
KAFKA_VERSION="3.7.0"
INFRA_DIR="$ROOT/.infra"
KAFKA_DIR="$INFRA_DIR/kafka_2.13-$KAFKA_VERSION"
mkdir -p "$INFRA_DIR"

if [ ! -f "$KAFKA_DIR/bin/kafka-server-start.sh" ]; then
  TARBALL="$INFRA_DIR/kafka.tgz"
  URLS=(
    "https://archive.apache.org/dist/kafka/$KAFKA_VERSION/kafka_2.13-$KAFKA_VERSION.tgz"
    "https://mirrors.tuna.tsinghua.edu.cn/apache/kafka/$KAFKA_VERSION/kafka_2.13-$KAFKA_VERSION.tgz"
    "https://mirrors.aliyun.com/apache/kafka/$KAFKA_VERSION/kafka_2.13-$KAFKA_VERSION.tgz"
  )
  DOWNLOADED=0
  for url in "${URLS[@]}"; do
    echo "尝试下载 Kafka：$url"
    if command -v curl >/dev/null 2>&1; then
      if curl -fL --retry 2 --connect-timeout 15 -o "$TARBALL" "$url"; then DOWNLOADED=1; break; fi
    elif command -v wget >/dev/null 2>&1; then
      if wget -q -O "$TARBALL" "$url"; then DOWNLOADED=1; break; fi
    fi
  done
  if [ "$DOWNLOADED" -ne 1 ] || [ ! -s "$TARBALL" ]; then
    echo "Kafka 下载失败，请手动下载 kafka_2.13-$KAFKA_VERSION.tgz 解压到 $INFRA_DIR" >&2
    exit 1
  fi
  tar -xzf "$TARBALL" -C "$INFRA_DIR"
  rm -f "$TARBALL"
fi

cd "$KAFKA_DIR"
if [ ! -f /tmp/kraft-combined-logs/meta.properties ]; then
  CLUSTER_ID="$(bin/kafka-storage.sh random-uuid)"
  bin/kafka-storage.sh format -t "$CLUSTER_ID" -c config/kraft/server.properties
fi

if bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092 >/dev/null 2>&1; then
  echo "Kafka 已在运行"
else
  export KAFKA_HEAP_OPTS="-Xmx1G -Xms1G"
  bin/kafka-server-start.sh -daemon config/kraft/server.properties
  READY=0
  for _ in $(seq 1 60); do
    if bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092 >/dev/null 2>&1; then
      READY=1
      break
    fi
    sleep 2
  done
  if [ "$READY" -ne 1 ]; then
    echo "Kafka 启动超时，请查看 $KAFKA_DIR/logs/server.log" >&2
    exit 1
  fi
  echo "Kafka 已启动（localhost:9092）"
fi

cd "$ROOT"
cat <<'EOF'
==> 基础设施就绪（真实 Redis + Kafka，非模拟）

    export APP_MODE=kafka
    export REDIS_URL=redis://localhost:6379/0
    export KAFKA_BOOTSTRAP=localhost:9092
    pip install -r requirements.txt
    python run.py

浏览器打开 http://localhost:8000
EOF
