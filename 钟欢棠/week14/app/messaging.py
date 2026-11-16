"""Kafka 消息总线：生产者、消费者、Topic 初始化。

所有跨进程通信都走 Kafka：
    mmrag.pdf.tasks      网页 -> 解析 Worker
    mmrag.pdf.status     解析 Worker -> 网页
    mmrag.qa.requests    网页 -> 问答 Worker
    mmrag.qa.responses   问答 Worker -> 网页
    mmrag.events         事件流（前端 Kafka 面板）
状态与会话本身存 Redis，Kafka 负责「传递」。

APP_MODE=local 时用进程内 _LocalBus 替代 broker（topic / 消息格式 / 消费组 offset 语义不变），
方便在没有 Docker 的 Windows 本机预览完整流程；正式提交请用默认的 APP_MODE=kafka。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

from .config import settings

logger = logging.getLogger("mmrag.kafka")

_producer = None
_producer_lock = threading.Lock()


class KafkaBusError(RuntimeError):
    pass


class _LocalMessage:
    """本地消息，接口与 confluent_kafka.Message 保持一致的子集。"""

    def __init__(self, topic: str, value: bytes, key: bytes | None, offset: int) -> None:
        self._topic, self._value, self._key, self._offset = topic, value, key, offset

    def topic(self) -> str:
        return self._topic

    def value(self) -> bytes:
        return self._value

    def key(self) -> bytes | None:
        return self._key

    def offset(self) -> int:
        return self._offset

    def partition(self) -> int:
        return 0

    def error(self):
        return None


class _LocalBus:
    """无 Docker 时的单进程「迷你 Kafka」：Topic 保留消息，每个消费组独立 offset。

    仅用于本机预览，Topic 名、消息格式、消费组语义与真实 Kafka 版完全一致。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages: dict[str, list[_LocalMessage]] = {}
        self._cursors: dict[tuple[str, str], int] = {}
        self._rotation: dict[str, int] = {}

    def send(self, topic: str, payload: dict, key: str | None = None) -> None:
        with self._lock:
            messages = self._messages.setdefault(topic, [])
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            messages.append(_LocalMessage(topic, raw, key.encode("utf-8") if key else None, len(messages)))

    def poll(self, group_id: str, topics: list[str], timeout: float = 1.0):
        with self._lock:
            count = max(1, len(topics))
            start = self._rotation.get(group_id, 0) % count
            self._rotation[group_id] = (start + 1) % count
            ordered = topics[start:] + topics[:start]  # 轮询，避免某个 topic 饿死其它 topic
            for topic in ordered:
                cursor_key = (group_id, topic)
                cursor = self._cursors.setdefault(cursor_key, 0)
                messages = self._messages.get(topic, [])
                if cursor < len(messages):
                    self._cursors[cursor_key] = cursor + 1
                    return messages[cursor]
        time.sleep(min(timeout, 0.15))
        return None


_local_bus = _LocalBus()


def _local_consume_loop(group_id, topics, handler, stop, on_message=None) -> None:
    logger.info("[%s] 本地消息总线已订阅 %s", group_id, ", ".join(topics))
    while not stop.is_set():
        msg = _local_bus.poll(group_id, topics, 1.0)
        if msg is None:
            continue
        payload = decode(msg)
        if payload is None:
            continue
        try:
            handler(msg.topic(), payload)
            if on_message:
                on_message(msg.topic(), payload)
        except Exception:  # noqa: BLE001
            logger.exception("[%s] 处理消息失败 topic=%s", group_id, msg.topic())


def _producer_config() -> dict:
    return {
        "bootstrap.servers": settings.kafka_bootstrap,
        "client.id": "mmrag-producer",
        "message.timeout.ms": 20000,
        "enable.idempotence": False,
    }


def get_producer():
    """单例 Producer（线程安全，延迟导入以便无 Kafka 环境也能 import 本模块）。"""
    global _producer
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                try:
                    from confluent_kafka import Producer
                except ImportError as exc:
                    raise KafkaBusError("缺少依赖，请执行：pip install -r requirements.txt") from exc
                _producer = Producer(_producer_config())
    return _producer


def send(topic: str, payload: dict, key: str | None = None) -> None:
    """发送 JSON 消息并等待 broker 确认；失败时抛出异常而不是静默丢消息。"""
    if settings.local_mode:
        _local_bus.send(topic, payload, key)
        return
    producer = get_producer()
    errors: list[str] = []

    def on_delivery(err, msg) -> None:
        if err is not None:
            errors.append(str(err))

    value = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    producer.produce(topic, value=value, key=key, callback=on_delivery)
    producer.poll(0)
    pending = producer.flush(10)
    if errors:
        raise KafkaBusError(f"消息发送到 {topic} 失败：{'; '.join(errors)}")
    if pending:
        raise KafkaBusError(f"消息发送到 {topic} 超时（剩余 {pending} 条未确认）")


def ensure_topics() -> None:
    """显式创建 Topic（幂等），避免依赖 broker 的 auto-create。"""
    if settings.local_mode:
        logger.info("本地预览模式：使用进程内消息总线，跳过 Kafka Topic 创建")
        return
    try:
        from confluent_kafka.admin import AdminClient, NewTopic
    except ImportError as exc:
        raise KafkaBusError("缺少依赖，请执行：pip install -r requirements.txt") from exc

    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap, "client.id": "mmrag-admin"})
    try:
        admin.list_topics(timeout=5)  # 快速探活，Kafka 未启动时不要卡在 create_topics
    except Exception as exc:  # noqa: BLE001
        raise KafkaBusError(f"Kafka 不可达（{settings.kafka_bootstrap}）：{exc}") from exc
    topics = list(settings.topics.values())
    futures = admin.create_topics(
        [NewTopic(topic, num_partitions=1, replication_factor=1) for topic in topics],
        operation_timeout=15,
    )
    for topic, future in futures.items():
        try:
            future.result(timeout=15)
            logger.info("Topic 已创建：%s", topic)
        except Exception as exc:  # noqa: BLE001 - 已存在是正常情况
            if "TOPIC_ALREADY_EXISTS" not in str(exc).upper():
                raise KafkaBusError(f"创建 Topic {topic} 失败：{exc}") from exc


def check_broker(timeout: float = 3.0) -> bool:
    if settings.local_mode:
        return True
    try:
        from confluent_kafka.admin import AdminClient

        admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap, "client.id": "mmrag-health"})
        admin.list_topics(timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def make_consumer(group_id: str, topics: list[str]):
    try:
        from confluent_kafka import Consumer
    except ImportError as exc:
        raise KafkaBusError("缺少依赖，请执行：pip install -r requirements.txt") from exc

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": group_id,
            "client.id": f"{group_id}-client",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "session.timeout.ms": 20000,
        }
    )
    consumer.subscribe(topics)
    return consumer


def decode(msg) -> dict[str, Any] | None:
    try:
        payload = json.loads(msg.value().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (ValueError, AttributeError, UnicodeDecodeError):
        return None


def consume_loop(
    group_id: str,
    topics: list[str],
    handler: Callable[[str, dict[str, Any]], None],
    stop: threading.Event,
    on_message=None,
) -> None:
    """通用消费循环：断线自动重连，处理成功才提交 offset，异常不会中断消费。"""
    if settings.local_mode:
        _local_consume_loop(group_id, topics, handler, stop, on_message)
        return
    consumer = None
    while not stop.is_set():
        if consumer is None:
            try:
                consumer = make_consumer(group_id, topics)
                logger.info("[%s] 已订阅 %s", group_id, ", ".join(topics))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] 连接 Kafka 失败，3 秒后重试：%s", group_id, exc)
                stop.wait(3)
                continue
        try:
            msg = consumer.poll(1.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] poll 异常，重连中：%s", group_id, exc)
            try:
                consumer.close()
            except Exception:  # noqa: BLE001
                pass
            consumer = None
            continue
        if msg is None:
            continue
        if msg.error():
            logger.warning("[%s] Kafka 消息错误：%s", group_id, msg.error())
            continue
        payload = decode(msg)
        if payload is None:
            logger.warning("[%s] 忽略非 JSON 消息 topic=%s", group_id, msg.topic())
            consumer.commit(msg)
            continue
        try:
            handler(msg.topic(), payload)
            if on_message:
                on_message(msg.topic(), payload)
        except Exception:  # noqa: BLE001
            logger.exception("[%s] 处理消息失败 topic=%s payload=%s", group_id, msg.topic(), payload)
        finally:
            consumer.commit(msg)
    if consumer is not None:
        consumer.close()


def publish_event(source: str, action: str, detail: str = "", **extra) -> None:
    """向事件流 topic 发一条可读事件（前端「Kafka 事件流」面板消费）。"""
    payload = {"source": source, "action": action, "detail": detail, "ts": time.time(), **extra}
    try:
        send(settings.topics["events"], payload)
    except Exception as exc:  # noqa: BLE001 - 事件流失败不影响主流程
        logger.warning("事件发送失败：%s", exc)
