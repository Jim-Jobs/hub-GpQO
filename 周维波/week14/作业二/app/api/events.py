"""状态事件网关：消费 Kafka 的 status topic，通过 SSE 实时推给前端。

为什么不让前端直接查库：
    - 上传后解析要 1 分钟级，轮询体验差；
    - 用 Kafka 作为状态总线，多 worker / 多 API 实例都能看到同一份事件流；
    - SSE 断线后前端仍会回退到 REST 轮询做最终兜底。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

from confluent_kafka import Consumer, KafkaError

from ..config import settings

logger = logging.getLogger(__name__)


class StatusHub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict]] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def register(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        with self._lock:
            self._queues.add(queue)
        return queue

    def unregister(self, queue: asyncio.Queue[dict]) -> None:
        with self._lock:
            self._queues.discard(queue)

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:  # 事件循环已关闭
            return

    def _fanout(self, event: dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._queues)
        for queue in queues:
            if queue.full():
                continue
            queue.put_nowait(event)


hub = StatusHub()


class StatusConsumer(threading.Thread):
    """独立的消费线程：group 每进程唯一 + auto.offset.reset=latest，只看新事件。"""

    def __init__(self) -> None:
        super().__init__(name="status-consumer", daemon=True)
        self._stop_event = threading.Event()
        self._consumer: Consumer | None = None

    def run(self) -> None:
        try:
            self._consumer = Consumer(
                {
                    "bootstrap.servers": settings.kafka_bootstrap,
                    "group.id": f"rag-status-gateway-{uuid.uuid4().hex[:8]}",
                    "client.id": "rag-status-gateway",
                    "auto.offset.reset": "latest",
                    "enable.auto.commit": True,
                    "session.timeout.ms": 30_000,
                }
            )
            self._consumer.subscribe([settings.topic_parse_status])
            logger.info("状态事件网关已订阅 %s", settings.topic_parse_status)
        except Exception as exc:
            logger.error("状态事件网关启动失败：%s", exc)
            return

        try:
            while not self._stop_event.is_set():
                message = self._consumer.poll(1.0)
                if message is None:
                    continue
                if message.error():
                    if message.error().code() != KafkaError._PARTITION_EOF:
                        logger.warning("状态事件消费错误：%s", message.error())
                    continue
                try:
                    event = json.loads(message.value().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                hub.publish_threadsafe(event)
        finally:
            if self._consumer is not None:
                self._consumer.close()
            logger.info("状态事件网关已退出")

    def stop(self) -> None:
        self._stop_event.set()


status_consumer = StatusConsumer()
