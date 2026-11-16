"""在单个后端进程中创建并停止同组 Kafka 消费线程。"""

from __future__ import annotations

import logging
from threading import Event, Thread
from typing import Callable

from kafka import KafkaConsumer

from backend.app.core.config import ConfigurationError, Settings
from backend.app.documents.application.ports import DocumentStore
from backend.app.documents.application.process_document import DocumentProcessor
from backend.app.workers.kafka_consumers import consume_loop


logger = logging.getLogger(__name__)
ConsumerFactory = Callable[..., KafkaConsumer]


class ConsumerManager:
    """管理同一进程内多个独立 Kafka consumer 线程。"""

    def __init__(
        self,
        settings: Settings,
        document_store: DocumentStore,
        process_document: DocumentProcessor,
        consumer_factory: ConsumerFactory = KafkaConsumer,
    ) -> None:
        """保存共享配置和业务依赖；consumer 实例在线程中单独创建。"""
        self._settings = settings
        self._document_store = document_store
        self._process_document = process_document
        self._consumer_factory = consumer_factory
        self._stop_event = Event()
        self._threads: list[Thread] = []

    def start(self) -> None:
        """按配置启动 consumer 线程，每个线程创建自己的 KafkaConsumer。"""
        if self._threads:
            raise RuntimeError("ConsumerManager 不能重复启动")
        if not self._settings.kafka_bootstrap_servers:
            raise ConfigurationError("缺少必要服务配置：RAG_KAFKA_BOOTSTRAP_SERVERS")

        for index in range(self._settings.kafka_consumers):
            thread = Thread(
                target=self._run_consumer,
                args=(index + 1,),
                name=f"pdf-consumer-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 5) -> None:
        """通知所有线程退出，并等待 consumer 在所属线程关闭。"""
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=timeout)

    def _run_consumer(self, index: int) -> None:
        """在线程内创建 consumer 并运行消费循环，确保线程独占实例。"""
        consumer = None
        try:
            consumer = self._consumer_factory(
                self._settings.kafka_topic,
                bootstrap_servers=self._settings.kafka_bootstrap_servers,
                group_id=self._settings.kafka_group_id,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
            )
            logger.info("Kafka 文档消费者 %s 已启动", index)
            consume_loop(
                consumer,
                self._process_document,
                self._document_store,
                self._settings,
                self._stop_event,
            )
        except Exception:
            logger.exception("Kafka 文档消费者 %s 异常退出", index)
        finally:
            if consumer is not None:
                consumer.close()


def start_consumers(
    settings: Settings,
    document_store: DocumentStore,
    process_document: DocumentProcessor,
    consumer_factory: ConsumerFactory = KafkaConsumer,
) -> ConsumerManager:
    """创建并启动 consumer manager，返回对象供应用关闭时停止。"""
    manager = ConsumerManager(settings, document_store, process_document, consumer_factory)
    manager.start()
    return manager
