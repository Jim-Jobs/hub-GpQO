"""解析任务队列：confluent-kafka producer 单例 + mock 记录。"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_producer = None
_producer_lock = threading.Lock()
_delivery_errors: list[str] = []
_mock_sent: list[dict[str, Any]] = []


def _mock_enabled() -> bool:
    return get_settings().mock_external


def _on_delivery(error, _message) -> None:  # pragma: no cover - 需要真实 Kafka
    if error is not None:
        _delivery_errors.append(str(error))


def _get_producer():
    global _producer
    if _mock_enabled():
        return None
    with _producer_lock:
        if _producer is None:
            from confluent_kafka import Producer

            settings = get_settings()
            _producer = Producer(
                {
                    "bootstrap.servers": settings.kafka_bootstrap,
                    "acks": "all",
                    "enable.idempotence": True,
                    "linger.ms": 5,
                }
            )
        return _producer


def enqueue_parse_task(payload: dict[str, Any], *, timeout: float = 20.0) -> None:
    """发送解析任务；失败抛异常，由上传/重解析流程落 FAILED。"""
    if _mock_enabled():
        _mock_sent.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        logger.debug("mock enqueue: file=%s", payload.get("id"))
        return

    settings = get_settings()
    producer = _get_producer()
    producer.produce(
        settings.kafka_topic,
        key=str(payload.get("id")),
        value=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        on_delivery=_on_delivery,
    )
    remaining = producer.flush(timeout)
    if remaining:
        raise TimeoutError(f"kafka flush timeout, {remaining} message(s) pending")
    if _delivery_errors:
        raise RuntimeError(_delivery_errors.pop(0))


def close() -> None:
    global _producer
    with _producer_lock:
        if _producer is not None:
            try:
                _producer.flush(5)
            except Exception:  # pragma: no cover - 关闭失败不影响退出
                logger.warning("kafka producer close failed", exc_info=True)
            _producer = None


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
def mock_sent() -> list[dict[str, Any]]:
    return list(_mock_sent)


def reset_mock_queue() -> None:
    _mock_sent.clear()
    _delivery_errors.clear()
