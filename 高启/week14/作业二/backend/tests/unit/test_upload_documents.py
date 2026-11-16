"""多文件上传、状态初始化和 Kafka 任务发布的用例测试。"""

from pathlib import Path

from backend.app.core.config import Settings
from backend.app.documents.application.upload_documents import UploadDependencies, upload_documents
from backend.app.documents.domain.models import DocumentRecord, DocumentStatus, IngestEvent
from backend.app.integrations.errors import DocumentStoreError, IngestPublishError
from backend.tests.unit.support import MemoryDocumentStore, MemoryFile, MemoryIngestProducer, run_immediate


def _dependencies(
    tmp_path: Path, max_pdf_bytes: int = 52_428_800
) -> tuple[UploadDependencies, MemoryDocumentStore, MemoryIngestProducer]:
    """建立隔离在 pytest 临时目录内的上传用例依赖。"""
    settings = Settings.from_env(
        {"RAG_DATA_ROOT": str(tmp_path), "RAG_MAX_PDF_BYTES": str(max_pdf_bytes)}
    )
    store = MemoryDocumentStore()
    producer = MemoryIngestProducer()
    return UploadDependencies(settings, store, producer), store, producer


def test_uploads_multiple_files_with_duplicate_names_independently(tmp_path: Path) -> None:
    """相同文件名的 PDF 应有不同 ID、独立状态和独立 Kafka 任务。"""
    dependencies, store, producer = _dependencies(tmp_path)
    files = [MemoryFile("report.pdf", b"%PDF-1.7 first"), MemoryFile("report.pdf", b"%PDF-1.7 second")]

    records = run_immediate(upload_documents(files, dependencies))

    assert len(records) == 2
    assert records[0].document_id != records[1].document_id
    assert all(record.status is DocumentStatus.PENDING for record in records)
    assert len(store.records) == len(producer.events) == 2
    assert all(event.relative_path.startswith("uploads/") for event in producer.events)
    assert all("%PDF" not in event.to_json() for event in producer.events)
    assert (dependencies.settings.uploads_dir / f"{records[0].document_id}.pdf").read_bytes() == b"%PDF-1.7 first"


def test_bad_file_does_not_block_valid_file_in_same_batch(tmp_path: Path) -> None:
    """无效 PDF 返回失败状态，但同批次有效文件仍建立任务。"""
    dependencies, store, producer = _dependencies(tmp_path)
    files = [MemoryFile("broken.pdf", b"not a pdf"), MemoryFile("valid.PDF", b"%PDF-1.7 valid")]

    records = run_immediate(upload_documents(files, dependencies))

    assert records[0].status is DocumentStatus.FAILED
    assert records[0].error
    assert records[1].status is DocumentStatus.PENDING
    assert len(store.records) == 2
    assert [event.document_id for event in producer.events] == [records[1].document_id]


def test_rejects_path_traversal_without_writing_outside_upload_directory(tmp_path: Path) -> None:
    """带路径分隔符的客户端文件名应被拒绝，且不产生外部文件。"""
    dependencies, _, producer = _dependencies(tmp_path)
    outside_path = tmp_path.parent / "escape.pdf"

    records = run_immediate(upload_documents([MemoryFile("..\\escape.pdf", b"%PDF-1.7 data")], dependencies))

    assert records[0].status is DocumentStatus.FAILED
    assert not outside_path.exists()
    assert producer.events == []


def test_rejects_non_pdf_extension_without_publishing_task(tmp_path: Path) -> None:
    """仅凭 MIME 类型不能绕过 PDF 扩展名校验。"""
    dependencies, _, producer = _dependencies(tmp_path)

    records = run_immediate(upload_documents([MemoryFile("report.txt", b"%PDF-1.7 data")], dependencies))

    assert records[0].status is DocumentStatus.FAILED
    assert "只接受 .pdf" in records[0].error
    assert producer.events == []


def test_rejects_pdf_over_configured_size_limit(tmp_path: Path) -> None:
    """超出配置字节上限的 PDF 应被删除并标记失败。"""
    dependencies, _, producer = _dependencies(tmp_path, max_pdf_bytes=8)

    records = run_immediate(upload_documents([MemoryFile("large.pdf", b"%PDF-1.7 too large")], dependencies))

    assert records[0].status is DocumentStatus.FAILED
    assert "超过大小限制" in records[0].error
    assert not (dependencies.settings.uploads_dir / f"{records[0].document_id}.pdf").exists()
    assert producer.events == []


def test_kafka_publish_failure_marks_document_failed(tmp_path: Path) -> None:
    """Kafka 发布失败后不能把文件保留为待解析状态。"""
    dependencies, store, producer = _dependencies(tmp_path)

    def fail_publish(event: IngestEvent) -> None:
        """模拟 Kafka 暂时不可用。"""
        raise IngestPublishError("broker unavailable")

    producer.publish = fail_publish
    records = run_immediate(upload_documents([MemoryFile("report.pdf", b"%PDF-1.7 valid")], dependencies))

    assert records[0].status is DocumentStatus.FAILED
    assert store.get(records[0].document_id).status is DocumentStatus.FAILED


def test_state_store_failure_prevents_kafka_publish_and_cleans_uploaded_file(tmp_path: Path) -> None:
    """Redis 无法初始化状态时，不应发布任务或留下无状态文件。"""
    dependencies, _, producer = _dependencies(tmp_path)

    class FailingStore:
        """模拟文档状态存储不可用。"""

        def save(self, record: DocumentRecord) -> None:
            """每次状态写入都返回适配器错误。"""
            raise DocumentStoreError("redis offline")

        def get(self, document_id: str) -> DocumentRecord | None:
            """模拟不存在可读取的文档状态。"""
            return None

    dependencies = UploadDependencies(dependencies.settings, FailingStore(), producer)
    records = run_immediate(upload_documents([MemoryFile("report.pdf", b"%PDF-1.7 valid")], dependencies))

    assert records[0].status is DocumentStatus.FAILED
    assert producer.events == []
    assert not (dependencies.settings.uploads_dir / f"{records[0].document_id}.pdf").exists()
