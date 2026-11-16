"""文档用例测试共用的轻量替身与协程执行辅助函数。"""

from backend.app.documents.domain.models import DocumentRecord, DocumentStatus, IngestEvent


class MemoryFile:
    """模拟具有文件名和异步分块读取能力的上传对象。"""

    def __init__(self, filename: str, content: bytes) -> None:
        """保存测试文件字节，并将读取位置设在文件开头。"""
        self.filename = filename
        self._content = content
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        """按指定长度返回下一段内容，文件结束时返回空字节串。"""
        if size < 0:
            size = len(self._content)
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class MemoryDocumentStore:
    """按 document_id 保存测试过程中的文档状态。"""

    def __init__(self) -> None:
        """创建空的文档状态表。"""
        self.records: dict[str, DocumentRecord] = {}

    def save(self, record: DocumentRecord) -> None:
        """新增或覆盖一条文档状态。"""
        self.records[record.document_id] = record

    def get(self, document_id: str) -> DocumentRecord | None:
        """读取文档状态；未知 ID 返回 None。"""
        return self.records.get(document_id)

    def complete(self, record: DocumentRecord) -> None:
        """在内存替身中保存终态，模拟 Redis 的完成操作。"""
        self.save(record)

    def list_completed_documents(self) -> list[DocumentRecord]:
        """返回内存状态中所有 completed 文档。"""
        return [
            record for record in self.records.values() if record.status is DocumentStatus.COMPLETED
        ]


class MemoryIngestProducer:
    """收集上传用例产生的任务事件。"""

    def __init__(self) -> None:
        """创建空的任务事件列表。"""
        self.events: list[IngestEvent] = []

    def publish(self, event: IngestEvent) -> None:
        """记录一条供测试断言的解析任务。"""
        self.events.append(event)


def run_immediate(coroutine):
    """运行只等待立即完成的测试协程，避开沙箱禁用的事件循环套接字。"""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    raise AssertionError("测试替身不应挂起等待外部 I/O")
