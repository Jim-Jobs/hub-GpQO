"""Kafka 生产者 / 消费者封装。

对应文档：``docs/03-Kafka设计.md``。

**两个不能改的语义**

1. ``pdf.upload`` 的 message **key = doc_id** —— 保证同一份文档的消息落同一分区、串行执行。
2. 消费者 ``enable_auto_commit=False``，**写 ES 成功之后**才提交 offset。
   反过来会在写 ES 前崩溃时丢任务。

kafka-python 是同步库，这里用 ``asyncio.to_thread`` 包装，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import KafkaError, TopicAlreadyExistsError

from app.config import Settings

logger = logging.getLogger(__name__)


class KafkaUnavailableError(RuntimeError):
    """Kafka 不可用。上传接口应据此返回 503。"""


def parse_api_version(raw: str) -> tuple[int, ...]:
    """``"3.9.0"`` → ``(3, 9, 0)``。"""
    parts: list[int] = []
    for chunk in raw.strip().split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) or (3, 9, 0)


def _serialize(value: Any) -> bytes:
    """JSON 序列化。

    ``ensure_ascii=False``：中文文件名不转义，日志与排查都更直观
    （``docs/03`` §3.1）。
    """
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _deserialize(raw: bytes | None) -> Any:
    return json.loads(raw.decode("utf-8")) if raw else None


class KafkaProducerClient:
    """生产者。线程安全，整个进程共用一个。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            value_serializer=_serialize,
            key_serializer=lambda key: key.encode("utf-8") if key else None,
            acks="all",  # 等所有 ISR 确认，配合幂等消费保证不丢
            retries=5,
            linger_ms=10,
            max_request_size=10 * 1024 * 1024,
            request_timeout_ms=30000,
            api_version=parse_api_version(settings.kafka_api_version),
        )

    async def _send(self, topic: str, value: dict, key: str | None) -> None:
        def _do_send() -> None:
            future = self._producer.send(topic, value=value, key=key)
            future.get(timeout=30)

        try:
            await asyncio.to_thread(_do_send)
        except KafkaError as exc:
            raise KafkaUnavailableError(f"Kafka 不可用: {exc}") from exc

    async def send_upload(self, message: dict[str, Any]) -> None:
        """投递解析任务。key = doc_id（同文档串行）。"""
        await self._send(self._settings.kafka_topic_upload, message, message.get("doc_id"))

    async def send_status(self, event: dict[str, Any]) -> None:
        """投递状态变更事件。"""
        await self._send(self._settings.kafka_topic_status, event, event.get("doc_id"))

    async def send_dlq(self, message: dict[str, Any]) -> None:
        """投递死信。"""
        await self._send(self._settings.kafka_topic_dlq, message, message.get("doc_id"))

    async def flush(self) -> None:
        await asyncio.to_thread(self._producer.flush, 30)

    async def close(self) -> None:
        await asyncio.to_thread(self._producer.close, 10)


class KafkaConsumerClient:
    """消费者外壳。真正的 ``poll`` 必须在**独立线程**里跑（阻塞调用）。"""

    def __init__(
        self,
        settings: Settings,
        *,
        topic: str,
        group_id: str,
    ) -> None:
        self._settings = settings
        self._consumer = KafkaConsumer(
            topic,
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            group_id=group_id,
            enable_auto_commit=False,  # 关键：手动提交
            auto_offset_reset="earliest",  # 首次启动从头消费，不丢任务
            max_poll_records=1,
            value_deserializer=_deserialize,
            key_deserializer=lambda raw: raw.decode("utf-8") if raw else None,
            consumer_timeout_ms=-1,
            api_version=parse_api_version(settings.kafka_api_version),
        )

    def poll_blocking(self, timeout_ms: int = 1000) -> list[Any]:
        """阻塞轮询一批消息。**只能在 ``to_thread`` 里调用。**"""
        records = self._consumer.poll(timeout_ms=timeout_ms, max_records=1)
        messages: list[Any] = []
        for batch in records.values():
            messages.extend(batch)
        return messages

    def commit(self) -> None:
        """提交 offset。**必须在业务处理成功之后调用。**"""
        self._consumer.commit()

    def close(self) -> None:
        self._consumer.close()


async def poll_once(consumer: KafkaConsumerClient, timeout_ms: int = 1000) -> list[Any]:
    """异步包装的 poll。"""
    return await asyncio.to_thread(consumer.poll_blocking, timeout_ms)


async def commit_offset(consumer: KafkaConsumerClient) -> None:
    await asyncio.to_thread(consumer.commit)


def ensure_topics(settings: Settings) -> list[str]:
    """创建缺失的 topic，返回本次新建的列表。

    分区数：``pdf.upload`` = ``KAFKA_PARTITIONS``（= worker 并行度上限）；
    其余 1 个分区。

    **尽力而为**：kafka-python 3.x 的 ``KafkaAdminClient`` 在部分 broker 上会
    版本探测超时。失败时只记警告并返回空列表 —— broker 的 ``auto.create.topics``
    或运维用 ``kafka-topics.sh`` 预建都能兜住，不应因此阻塞 Worker 启动。
    """
    admin = KafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        request_timeout_ms=10000,
        api_version=parse_api_version(settings.kafka_api_version),
    )
    wanted = [
        NewTopic(
            name=settings.kafka_topic_upload,
            num_partitions=settings.kafka_partitions,
            replication_factor=1,
        ),
        NewTopic(
            name=settings.kafka_topic_status, num_partitions=1, replication_factor=1
        ),
        NewTopic(name=settings.kafka_topic_dlq, num_partitions=1, replication_factor=1),
    ]
    created: list[str] = []
    try:
        existing = set(admin.list_topics())
        to_create = [topic for topic in wanted if topic.name not in existing]
        if not to_create:
            return []
        try:
            admin.create_topics(new_topics=to_create, validate_only=False)
            created = [topic.name for topic in to_create]
        except TopicAlreadyExistsError:
            pass
    except Exception as exc:  # noqa: BLE001 —— 建 topic 失败不应阻塞启动
        logger.warning("自动创建 Kafka topic 失败（%s），请确认 topic 已由运维预建", exc)
        return []
    finally:
        with suppress(Exception):
            admin.close()
    return created
