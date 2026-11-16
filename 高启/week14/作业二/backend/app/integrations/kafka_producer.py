"""Kafka 文档任务生产者适配器。"""

from kafka import KafkaProducer

from backend.app.documents.domain.models import IngestEvent
from backend.app.integrations.errors import IngestPublishError


class KafkaIngestProducer:
    """发布只含任务元数据的 PDF 解析消息。"""

    def __init__(self, producer: KafkaProducer, topic: str) -> None:
        """接收 Kafka producer 实例和文档任务主题名。"""
        self._producer = producer
        self._topic = topic

    def publish(self, event: IngestEvent) -> None:
        """按 document_id 分区键发送事件，并等待 broker 确认。"""
        try:
            future = self._producer.send(
                self._topic,
                key=event.document_id.encode("utf-8"),
                value=event.to_json().encode("utf-8"),
            )
            future.get(timeout=30)
        except Exception as error:
            raise IngestPublishError("Kafka 文档任务发布失败") from error
