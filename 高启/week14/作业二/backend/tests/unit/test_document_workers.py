"""Kafka 文档消费者的重试、状态持久化和线程隔离测试。"""

import threading
import time
from datetime import datetime, timezone

from kafka.structs import TopicPartition

from backend.app.core.config import Settings
from backend.app.documents.application.process_document import (
    ProcessingOutcome,
    PermanentProcessingError,
    RetryableProcessingError,
    handle_ingest_event,
)
from backend.app.documents.domain.models import DocumentRecord, DocumentStatus, IngestEvent
from backend.app.workers.consumer_manager import start_consumers
from backend.app.workers.kafka_consumers import consume_loop
from backend.tests.unit.support import MemoryDocumentStore


def _event(document_id: str = "doc-1") -> IngestEvent:
    """建立带有固定文件元数据的测试任务事件。"""
    return IngestEvent("event-1", document_id, "report.pdf", f"uploads/{document_id}.pdf", datetime.now(timezone.utc))


def _settings(tmp_path) -> Settings:
    """为 worker 测试提供临时数据根目录和默认重试策略。"""
    return Settings.from_env(
        {"RAG_DATA_ROOT": str(tmp_path), "RAG_KAFKA_BOOTSTRAP_SERVERS": "localhost:9092"}
    )


def test_success_persists_parsing_and_completed_states(tmp_path) -> None:
    """成功处理前写入 parsing，处理完成后写入 completed。"""
    store = MemoryDocumentStore()
    observed_statuses = []

    def process(event: IngestEvent) -> None:
        """记录处理器执行时 Redis 中的文档状态。"""
        observed_statuses.append(store.get(event.document_id).status)

    outcome = handle_ingest_event(_event(), process, store, _settings(tmp_path), sleep=lambda _: None)

    assert outcome == ProcessingOutcome(DocumentStatus.COMPLETED, attempts=1)
    assert observed_statuses == [DocumentStatus.PARSING]
    assert store.get("doc-1").status is DocumentStatus.COMPLETED


def test_redelivered_completed_document_is_not_processed_again(tmp_path) -> None:
    """Redis 已是 completed 时，Kafka 重投直接返回成功供 offset 确认。"""
    store = MemoryDocumentStore()
    store.save(DocumentRecord("doc-1", "report.pdf", DocumentStatus.COMPLETED, progress=100))
    processor_calls = []

    def process(event: IngestEvent) -> None:
        """记录处理器调用次数。"""
        processor_calls.append(event.document_id)

    outcome = handle_ingest_event(_event(), process, store, _settings(tmp_path), sleep=lambda _: None)

    assert outcome.status is DocumentStatus.COMPLETED
    assert outcome.attempts == 0
    assert processor_calls == []


def test_retryable_error_uses_configured_backoff_then_succeeds(tmp_path) -> None:
    """可重试错误按 1/2 秒退避，并在处理成功后完成状态。"""
    store = MemoryDocumentStore()
    sleep_calls = []
    attempts = 0

    def process(event: IngestEvent) -> None:
        """前两次模拟服务故障，第三次成功。"""
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableProcessingError("temporary service failure")

    outcome = handle_ingest_event(
        _event(), process, store, _settings(tmp_path), sleep=sleep_calls.append
    )

    assert outcome.status is DocumentStatus.COMPLETED
    assert outcome.attempts == 3
    assert sleep_calls == [1, 2]


def test_retryable_error_exhaustion_persists_failed_before_return(tmp_path) -> None:
    """四次总尝试均失败时写入 failed 状态并返回最终错误。"""
    store = MemoryDocumentStore()
    sleep_calls = []

    def process(event: IngestEvent) -> None:
        """每次调用都模拟可重试的外部服务错误。"""
        raise RetryableProcessingError("mineru unavailable")

    outcome = handle_ingest_event(
        _event(), process, store, _settings(tmp_path), sleep=sleep_calls.append
    )

    assert outcome.status is DocumentStatus.FAILED
    assert outcome.attempts == 4
    assert outcome.error == "mineru unavailable"
    assert sleep_calls == [1, 2, 4]
    assert store.get("doc-1").status is DocumentStatus.FAILED


def test_permanent_input_error_is_not_retried(tmp_path) -> None:
    """永久输入错误立即持久化失败，不执行退避重试。"""
    store = MemoryDocumentStore()
    sleep_calls = []

    def process(event: IngestEvent) -> None:
        """模拟不可恢复的 PDF 内容错误。"""
        raise PermanentProcessingError("invalid PDF payload")

    outcome = handle_ingest_event(
        _event(), process, store, _settings(tmp_path), sleep=sleep_calls.append
    )

    assert outcome.status is DocumentStatus.FAILED
    assert outcome.attempts == 1
    assert sleep_calls == []
    assert store.get("doc-1").error == "invalid PDF payload"


def test_unexpected_processing_error_seeks_message_without_offset_commit(tmp_path) -> None:
    """未分类故障不提交 offset，并将消费位置退回当前消息。"""
    stop_event = threading.Event()
    message = FakeKafkaMessage(_event(), partition=0)

    class FailingConsumer:
        """投递单条消息并记录 worker 的 seek 操作。"""

        def __init__(self) -> None:
            """初始化待投递消息和回退结果。"""
            self.delivered = False
            self.seek_result = None

        def poll(self, timeout_ms: int) -> dict:
            """首次返回测试消息，之后返回空结果。"""
            if self.delivered:
                return {}
            self.delivered = True
            return {message.topic_partition: [message]}

        def commit(self, offsets: dict) -> None:
            """若 worker 错误提交 offset，则让测试失败。"""
            raise AssertionError("unexpected failure must remain uncommitted")

        def seek(self, partition: TopicPartition, offset: int) -> None:
            """记录消息回退位置并停止消费循环。"""
            self.seek_result = (partition, offset)
            stop_event.set()

        def close(self) -> None:
            """满足 consumer 最小接口。"""

    consumer = FailingConsumer()
    store = MemoryDocumentStore()

    def process(event: IngestEvent) -> None:
        """抛出未分类异常，模拟代码故障或未预期错误。"""
        raise RuntimeError("unexpected failure")

    consume_loop(consumer, process, store, _settings(tmp_path), stop_event)

    assert consumer.seek_result == (message.topic_partition, message.offset)
    assert store.get("doc-1").status is DocumentStatus.PARSING


def test_invalid_kafka_payload_is_logged_and_committed_to_skip_poison_message(tmp_path) -> None:
    """缺少必需事件字段的消息无法重试，应提交该条以免分区卡住。"""
    stop_event = threading.Event()
    message = FakeKafkaMessage(_event(), partition=0)
    message.value = b"{}"

    class PoisonMessageConsumer:
        """只返回一条无效事件并在提交后停止。"""

        def __init__(self) -> None:
            """初始化消息游标和提交记录。"""
            self.delivered = False
            self.committed_offsets = []

        def poll(self, timeout_ms: int) -> dict:
            """首次返回无效消息。"""
            if self.delivered:
                return {}
            self.delivered = True
            return {message.topic_partition: [message]}

        def commit(self, offsets: dict) -> None:
            """保存 poison message 的 offset 并结束消费循环。"""
            self.committed_offsets.append(offsets)
            stop_event.set()

        def seek(self, partition: TopicPartition, offset: int) -> None:
            """无效输入应被提交跳过，不应回退重试。"""
            raise AssertionError("invalid event must not be retried")

        def close(self) -> None:
            """满足 consumer 最小接口。"""

    consumer = PoisonMessageConsumer()
    processor_calls = []

    def process(event: IngestEvent) -> None:
        """记录调用次数，确认无效载荷未进入处理器。"""
        processor_calls.append(event)

    consume_loop(consumer, process, MemoryDocumentStore(), _settings(tmp_path), stop_event)

    assert consumer.committed_offsets[0][message.topic_partition].offset == 1
    assert processor_calls == []


class FakeKafkaMessage:
    """保存 Kafka consumer poll 返回的最小消息字段。"""

    def __init__(self, event: IngestEvent, partition: int) -> None:
        """将事件编码为 Kafka value 并关联分区号。"""
        self.topic = "rag.pdf.ingest"
        self.partition = partition
        self.offset = 0
        self.value = event.to_json().encode("utf-8")

    @property
    def topic_partition(self) -> TopicPartition:
        """返回消息所属 Kafka topic partition。"""
        return TopicPartition(self.topic, self.partition)


class FakeKafkaConsumer:
    """为单个 worker 线程模拟独立 Kafka consumer。"""

    def __init__(self, message: FakeKafkaMessage, options: dict, committed: threading.Event) -> None:
        """记录创建线程、订阅选项和一条待消费消息。"""
        self.message = message
        self.options = options
        self.committed = committed
        self.owner_thread = threading.get_ident()
        self.delivered = False
        self.operation_threads: list[int] = []
        self.offset_commits: list[dict] = []

    def poll(self, timeout_ms: int) -> dict:
        """首次返回一条消息，之后短暂等待并返回空结果。"""
        self.operation_threads.append(threading.get_ident())
        if not self.delivered:
            self.delivered = True
            return {self.message.topic_partition: [self.message]}
        time.sleep(timeout_ms / 1000)
        return {}

    def commit(self, offsets: dict) -> None:
        """记录手动 offset 提交值及其执行线程。"""
        self.operation_threads.append(threading.get_ident())
        self.offset_commits.append(offsets)
        if self.options["on_commit"]():
            self.committed.set()

    def seek(self, partition: TopicPartition, offset: int) -> None:
        """记录失败消息的 offset 回退位置。"""
        self.operation_threads.append(threading.get_ident())

    def close(self) -> None:
        """记录 consumer 关闭所在线程。"""
        self.operation_threads.append(threading.get_ident())


def test_two_consumers_share_group_but_are_owned_by_separate_threads(tmp_path) -> None:
    """两个同组 consumer 在线程内独立创建并行处理不同文档。"""
    store = MemoryDocumentStore()
    processing_barrier = threading.Barrier(2)
    committed = threading.Event()
    consumers: list[FakeKafkaConsumer] = []
    factory_lock = threading.Lock()
    commit_lock = threading.Lock()
    commit_count = 0

    def factory(topic: str, **options) -> FakeKafkaConsumer:
        """在调用线程内创建不同消息对应的独立 consumer。"""
        nonlocal commit_count
        with factory_lock:
            partition = len(consumers)
            document_id = f"doc-{partition}"
            message = FakeKafkaMessage(_event(document_id), partition)

            def confirm_commit_order() -> bool:
                """确认本条记录已持久化 completed 后再计数提交。"""
                nonlocal commit_count
                assert store.get(document_id).status is DocumentStatus.COMPLETED
                with commit_lock:
                    commit_count += 1
                    return commit_count == 2

            consumer = FakeKafkaConsumer(
                message,
                {**options, "on_commit": confirm_commit_order},
                committed,
            )
            consumers.append(consumer)
            return consumer

    def process(event: IngestEvent) -> None:
        """用双线程屏障证明两条任务可同时进入处理器。"""
        processing_barrier.wait(timeout=3)

    manager = start_consumers(_settings(tmp_path), store, process, consumer_factory=factory)
    assert committed.wait(timeout=5)
    manager.stop()

    assert len(consumers) == 2
    assert {consumer.options["group_id"] for consumer in consumers} == {"rag-pdf-workers"}
    assert all(consumer.options["enable_auto_commit"] is False for consumer in consumers)
    assert len({consumer.owner_thread for consumer in consumers}) == 2
    assert all(set(consumer.operation_threads) == {consumer.owner_thread} for consumer in consumers)
    assert all(consumer.offset_commits[0][consumer.message.topic_partition].offset == 1 for consumer in consumers)
    assert store.get("doc-0").status is DocumentStatus.COMPLETED
    assert store.get("doc-1").status is DocumentStatus.COMPLETED
