"""文档应用用例依赖的存储和消息发布接口。"""

from typing import Protocol

from backend.app.documents.domain.models import (
    CorpusProfile,
    DocumentRecord,
    DocumentSummary,
    IngestEvent,
)


class DocumentStore(Protocol):
    """描述文档状态存储必须提供的读写操作。"""

    def save(self, record: DocumentRecord) -> None:
        """保存一个文档状态记录。"""

    def get(self, document_id: str) -> DocumentRecord | None:
        """按文档 ID 读取状态，不存在时返回 None。"""

    def complete(self, record: DocumentRecord) -> None:
        """原子保存完成状态和由已完成文档生成的资料库概览。"""

    def list_completed_documents(self) -> list[DocumentRecord]:
        """返回所有 completed 文档，供问答检索限制资料库范围。"""

    def get_corpus_profile(self) -> CorpusProfile | None:
        """读取已完成文档摘要汇总的资料库主题概览。"""


class IngestPublisher(Protocol):
    """描述 PDF 任务发布器必须提供的操作。"""

    def publish(self, event: IngestEvent) -> None:
        """发布一条文档解析任务。"""


class DocumentProcessor(Protocol):
    """描述单文档解析、索引与摘要流程的可调用接口。"""

    def __call__(self, event: IngestEvent) -> DocumentSummary | None:
        """处理文档并返回完成时要持久化的标题和摘要。"""
