"""Redis 文档状态与 Kafka 任务消息适配器测试。"""

from datetime import datetime, timezone

import pytest

from backend.app.documents.domain.models import DocumentRecord, DocumentStatus, IngestEvent
from backend.app.integrations.errors import DocumentStoreError, IngestPublishError
from backend.app.integrations.kafka_producer import KafkaIngestProducer
from backend.app.integrations.redis_document_store import RedisDocumentStore


class MemoryRedis:
    """模拟 Redis 的字符串 key/value 读写接口。"""

    def __init__(self) -> None:
        """初始化空 Redis key/value 表。"""
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        """保存给定 key 对应的字符串值。"""
        self.values[key] = value

    def get(self, key: str) -> str | None:
        """读取 key 的字符串值，不存在时返回 None。"""
        return self.values.get(key)


class AcknowledgedSend:
    """模拟 Kafka producer send 返回的 broker 确认对象。"""

    def get(self, timeout: int) -> None:
        """模拟在超时范围内成功收到 broker 确认。"""
        assert timeout == 30


class MemoryKafka:
    """收集 Kafka 生产者发送的 topic、key 和 value。"""

    def __init__(self) -> None:
        """初始化空消息列表。"""
        self.messages: list[tuple[str, bytes, bytes]] = []

    def send(self, topic: str, key: bytes, value: bytes) -> AcknowledgedSend:
        """记录消息并返回模拟的 broker 确认对象。"""
        self.messages.append((topic, key, value))
        return AcknowledgedSend()


def test_redis_document_store_round_trips_json_record() -> None:
    """验证文档状态按专用 key 写入并从 JSON 还原。"""
    redis = MemoryRedis()
    store = RedisDocumentStore(redis)
    record = DocumentRecord("doc-1", "report.pdf", DocumentStatus.PENDING)

    store.save(record)

    assert store.get("doc-1") == record
    assert "rag:document:doc-1" in redis.values


def test_redis_document_store_reads_utf8_bytes() -> None:
    """验证 Redis 默认返回的 UTF-8 字节串可以还原成状态记录。"""
    record = DocumentRecord("doc-1", "report.pdf", DocumentStatus.PENDING)

    class ByteRedis(MemoryRedis):
        """模拟 decode_responses=False 时 Redis 返回字节串。"""

        def get(self, key: str) -> bytes | None:
            """把保存的 JSON 编码成 UTF-8 字节串。"""
            payload = self.values.get(key)
            return payload.encode("utf-8") if payload is not None else None

    redis = ByteRedis()
    redis.set("rag:document:doc-1", record.to_json())

    assert RedisDocumentStore(redis).get("doc-1") == record


@pytest.mark.parametrize("payload", [b"\xff", "{invalid json", "{}"])
def test_invalid_redis_payload_uses_document_store_error(payload: bytes | str) -> None:
    """无效 UTF-8、JSON 或缺少字段都转换成统一的数据格式错误。"""

    class PayloadRedis:
        """为读取测试返回预设的原始 Redis 值。"""

        def get(self, key: str) -> bytes | str:
            """返回未经处理的测试载荷。"""
            return payload

    with pytest.raises(DocumentStoreError, match="格式无效"):
        RedisDocumentStore(PayloadRedis()).get("doc-1")


def test_kafka_producer_publishes_metadata_keyed_by_document_id() -> None:
    """验证 Kafka 消息使用 document_id 分区键且载荷不含 PDF 内容。"""
    kafka = MemoryKafka()
    producer = KafkaIngestProducer(kafka, "rag.pdf.ingest")
    event = IngestEvent("event-1", "doc-1", "report.pdf", "uploads/doc-1.pdf", datetime.now(timezone.utc))

    producer.publish(event)

    topic, key, payload = kafka.messages[0]
    assert topic == "rag.pdf.ingest"
    assert key == b"doc-1"
    assert b"%PDF" not in payload
    assert b"uploads/doc-1.pdf" in payload


def test_integration_failures_are_converted_to_domain_adapter_errors() -> None:
    """验证 Redis 和 Kafka 原始异常不会泄漏到应用用例。"""

    class FailingRedis:
        """模拟 Redis 连接失败。"""

        def set(self, key: str, value: str) -> None:
            """抛出模拟的存储异常。"""
            raise ConnectionError("offline")

    class FailingKafka:
        """模拟 Kafka broker 无法确认消息。"""

        def send(self, topic: str, key: bytes, value: bytes) -> AcknowledgedSend:
            """抛出模拟的发布异常。"""
            raise ConnectionError("offline")

    class FailingRedisRead:
        """模拟 Redis 读取命令失败。"""

        def get(self, key: str) -> None:
            """抛出模拟的读取异常。"""
            raise ConnectionError("offline")

    record = DocumentRecord("doc-1", "report.pdf", DocumentStatus.PENDING)
    event = IngestEvent("event-1", "doc-1", "report.pdf", "uploads/doc-1.pdf", datetime.now(timezone.utc))

    with pytest.raises(DocumentStoreError):
        RedisDocumentStore(FailingRedis()).save(record)
    with pytest.raises(DocumentStoreError, match="读取失败"):
        RedisDocumentStore(FailingRedisRead()).get("doc-1")
    with pytest.raises(IngestPublishError):
        KafkaIngestProducer(FailingKafka(), "rag.pdf.ingest").publish(event)
