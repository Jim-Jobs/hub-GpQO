#!/usr/bin/env bash
# 停掉 API / worker（不动 Kafka 与 mineru server）
pkill -f "uvicorn mrag.api.main" 2>/dev/null || true
pkill -f "mrag.worker.main" 2>/dev/null || true
echo "✓ 后端进程已停止（Kafka/mineru server 保留）"
