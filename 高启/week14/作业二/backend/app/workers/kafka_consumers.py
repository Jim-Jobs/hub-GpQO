"""在 consumer 所属线程中拉取、处理并提交 Kafka 消息。"""

from __future__ import annotations

import logging
from threading import Event
from typing import Protocol

from kafka.structs import OffsetAndMetadata, TopicPartition

from backend.app.core.config import Settings
from backend.app.documents.application.ports import DocumentStore
from backend.app.documents.application.process_document import DocumentProcessor, handle_ingest_event
from backend.app.documents.domain.models import IngestEvent


logger = logging.getLogger(__name__)


class KafkaConsumerPort(Protocol):
    """描述 worker 使用的最小 Kafka consumer 接口。"""

    def poll(self, timeout_ms: int) -> dict:
        """拉取当前已分配分区的新消息。"""

    def commit(self, offsets: dict) -> None:
        """提交指定分区的下一个待读取 offset。"""

    def seek(self, partition: TopicPartition, offset: int) -> None:
        """将指定分区位置回退到尚未成功处理的消息。"""

    def close(self) -> None:
        """释放 consumer 的网络和协调器资源。"""


def consume_loop(
    consumer: KafkaConsumerPort,
    process_document: DocumentProcessor,
    document_store: DocumentStore,
    settings: Settings,
    stop_event: Event,
) -> None:
    """逐消息处理；只有终态已写入 Redis 后才提交对应 offset。"""
    while not stop_event.is_set():
        try:
            polled_messages = consumer.poll(timeout_ms=500)
        except Exception:
            logger.exception("Kafka poll 失败；稍后继续尝试")
            stop_event.wait(1)
            continue

        for partition, messages in polled_messages.items():
            for message in messages:
                if stop_event.is_set():
                    return
                if not _process_message(
                    consumer, partition, message, process_document, document_store, settings, stop_event
                ):
                    # 失败消息已 seek 回原 offset；当前批次后续消息不能越过它提交。
                    break


def _process_message(
    consumer: KafkaConsumerPort,
    partition: TopicPartition,
    message,
    process_document: DocumentProcessor,
    document_store: DocumentStore,
    settings: Settings,
    stop_event: Event,
) -> bool:
    """处理一条记录并精确提交其 offset；未知异常则保留消息待重投。"""
    try:
        event = _decode_event(message.value)
    except (KeyError, UnicodeDecodeError, ValueError, TypeError) as error:
        # 无法解码的 poison message 没有可用 document_id，记录后跳过以免分区永久阻塞。
        logger.error("丢弃无效 Kafka 任务消息：%s", error)
        return _commit_or_rewind(consumer, partition, message, stop_event)

    try:
        handle_ingest_event(event, process_document, document_store, settings)
        return _commit_or_rewind(consumer, partition, message, stop_event)
    except Exception:
        logger.exception("文档任务未能持久化终态；保留 offset 供 Kafka 重投")
        _rewind_message(consumer, partition, message, stop_event)
        return False


def _decode_event(value: bytes | str) -> IngestEvent:
    """将 Kafka bytes/string 消息载荷解析为文档任务事件。"""
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        raise TypeError("Kafka 任务载荷必须是字符串或字节串")
    return IngestEvent.from_json(value)


def _commit_message(consumer: KafkaConsumerPort, partition: TopicPartition, message) -> None:
    """只提交当前消息之后的 offset，避免跳过同批次未处理消息。"""
    next_offset = OffsetAndMetadata(message.offset + 1, metadata="", leader_epoch=-1)
    consumer.commit({partition: next_offset})


def _commit_or_rewind(
    consumer: KafkaConsumerPort, partition: TopicPartition, message, stop_event: Event
) -> bool:
    """提交当前消息；提交失败时回退位置并保留消息重试。"""
    try:
        _commit_message(consumer, partition, message)
        return True
    except Exception:
        logger.exception("Kafka offset 提交失败；将消息位置回退以便重试")
        _rewind_message(consumer, partition, message, stop_event)
        return False


def _rewind_message(
    consumer: KafkaConsumerPort, partition: TopicPartition, message, stop_event: Event
) -> None:
    """把消费位置恢复到失败消息，避免后续消息越过它被确认。"""
    consumer.seek(partition, message.offset)
    stop_event.wait(1)
