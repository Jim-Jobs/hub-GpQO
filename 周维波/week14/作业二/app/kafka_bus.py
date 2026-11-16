"""Kafka 接入层：topic 管理、上传消息生产、状态事件生产。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from confluent_kafka import KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic

from .config import settings

logger = logging.getLogger(__name__)

_PRODUCER: Producer | None = None


def _producer_config() -> dict[str, Any]:
    return {
        "bootstrap.servers": settings.kafka_bootstrap,
        "client.id": "rag-api-producer",
        "acks": "all",
        "enable.idempotence": True,
        "retries": 5,
        "linger.ms": 5,
        "message.timeout.ms": 30000,
    }


def get_producer() -> Producer:
    global _PRODUCER
    if _PRODUCER is None:
        _PRODUCER = Producer(_producer_config())
    return _PRODUCER


def ensure_topics() -> dict[str, str]:
    """创建所需 topic（已存在则忽略），返回每个 topic 的状态说明。"""
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    topics = [
        settings.topic_parse_request,
        settings.topic_parse_status,
        settings.topic_parse_dlq,
    ]
    new_topics = [
        NewTopic(
            topic,
            num_partitions=settings.kafka_partitions,
            replication_factor=settings.kafka_replication,
        )
        for topic in topics
    ]
    result: dict[str, str] = {}
    futures = admin.create_topics(new_topics)
    for topic, future in futures.items():
        try:
            future.result(timeout=15)
            result[topic] = "created"
        except Exception as exc:  # TopicAlreadyExists 等
            message = str(exc)
            if "already exists" in message.lower() or "TOPIC_ALREADY_EXISTS" in message:
                result[topic] = "exists"
            else:
                result[topic] = f"error: {message}"
                logger.warning("创建 topic %s 失败：%s", topic, message)
    return result


def _publish(topic: str, payload: dict, key: str | None, timeout: float = 10.0) -> None:
    producer = get_producer()
    producer.produce(
        topic,
        key=key,
        value=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    deadline = time.time() + timeout
    while True:
        remaining = max(0.0, deadline - time.time())
        if producer.flush(remaining) == 0:
            return
        if remaining <= 0:
            raise KafkaException(f"消息投递 {topic} 超时")
        producer.poll(0)


def publish_parse_request(
    document_id: str, filename: str, stored_path: str, attempt: int = 1
) -> None:
    """上传成功后投递解析任务；key=document_id 保证同一文档落到同一分区、顺序处理。"""
    payload = {
        "type": "parse_request",
        "document_id": document_id,
        "filename": filename,
        "stored_path": stored_path,
        "attempt": attempt,
        "requested_at": time.time(),
    }
    _publish(settings.topic_parse_request, payload, key=document_id)


def publish_status(document: dict, stage: str) -> None:
    """解析状态事件：API 的 SSE 网关消费后实时推给前端。"""
    payload = {
        "type": "document_status",
        "stage": stage,
        "document": document,
        "published_at": time.time(),
    }
    _publish(settings.topic_parse_status, payload, key=document.get("id"), timeout=5.0)


def publish_dlq(payload: dict) -> None:
    _publish(settings.topic_parse_dlq, payload, key=payload.get("document_id"), timeout=5.0)
