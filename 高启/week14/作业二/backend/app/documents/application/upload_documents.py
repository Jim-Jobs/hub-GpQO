"""逐文件接收 PDF、建立状态并发布解析任务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol, Sequence
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.documents.application.ports import DocumentStore, IngestPublisher
from backend.app.documents.domain.models import DocumentRecord, DocumentStatus, IngestEvent
from backend.app.integrations.errors import DocumentStoreError, IngestPublishError


PDF_SIGNATURE = b"%PDF-"
READ_CHUNK_BYTES = 64 * 1024


class ReadableUpload(Protocol):
    """描述可按块异步读取的上传文件。"""

    filename: str | None

    async def read(self, size: int = -1) -> bytes:
        """读取至多指定字节数；文件结束时返回空字节串。"""


@dataclass(frozen=True)
class UploadDependencies:
    """上传用例使用的配置、文档状态存储和 Kafka 发布器。"""

    settings: Settings
    document_store: DocumentStore
    ingest_publisher: IngestPublisher


class UploadRejected(ValueError):
    """用户上传的文件名、格式或内容不符合 PDF 接收规则。"""


async def upload_documents(
    files: Sequence[ReadableUpload], dependencies: UploadDependencies
) -> list[DocumentRecord]:
    """逐个处理上传文件；单个文件失败不会中断同批其他文件。"""
    dependencies.settings.initialize_data_directories()
    records: list[DocumentRecord] = []
    for uploaded_file in files:
        record = await _upload_one(uploaded_file, dependencies)
        records.append(record)
    return records


async def _upload_one(uploaded_file: ReadableUpload, dependencies: UploadDependencies) -> DocumentRecord:
    """验证并保存单个 PDF，再持久化 pending 状态和 Kafka 事件。"""
    document_id = str(uuid4())
    file_name = _display_file_name(uploaded_file.filename)
    try:
        file_name = _validate_file_name(uploaded_file.filename)
        file_path = dependencies.settings.uploads_dir / f"{document_id}.pdf"
        await _write_pdf(uploaded_file, file_path, dependencies.settings.max_pdf_bytes)
    except (UploadRejected, OSError) as error:
        return _save_failure(document_id, file_name, str(error), dependencies.document_store)

    record = DocumentRecord(document_id, file_name, DocumentStatus.PENDING)
    try:
        dependencies.document_store.save(record)
    except DocumentStoreError as error:
        file_path.unlink(missing_ok=True)
        return _failed_record(document_id, file_name, str(error))

    event = IngestEvent(
        event_id=str(uuid4()),
        document_id=document_id,
        file_name=file_name,
        relative_path=f"uploads/{document_id}.pdf",
        created_at=datetime.now(timezone.utc),
    )
    try:
        dependencies.ingest_publisher.publish(event)
    except IngestPublishError as error:
        failed = _failed_record(document_id, file_name, str(error))
        return _save_failure_record(failed, dependencies.document_store)
    return record


def _validate_file_name(file_name: str | None) -> str:
    """只接受不带路径的 PDF 文件名，防止客户端路径进入存储逻辑。"""
    if not file_name or "\x00" in file_name:
        raise UploadRejected("文件名为空或包含非法字符")
    normalized_name = file_name.replace("\\", "/")
    if len(PurePosixPath(normalized_name).parts) != 1 or normalized_name in {".", ".."}:
        raise UploadRejected("文件名不能包含目录路径")
    if PurePosixPath(normalized_name).suffix.lower() != ".pdf":
        raise UploadRejected("只接受 .pdf 文件")
    return normalized_name


def _display_file_name(file_name: str | None) -> str:
    """为失败响应保留不含目录的文件名，避免回显客户端路径。"""
    if not file_name:
        return "未命名文件"
    return PurePosixPath(file_name.replace("\\", "/")).name or "未命名文件"


async def _write_pdf(uploaded_file: ReadableUpload, destination: Path, max_bytes: int) -> None:
    """分块验证 PDF 签名与大小，并以独占方式写入目标文件。"""
    total_bytes = 0
    signature_checked = False
    try:
        with destination.open("xb") as output:
            while chunk := await uploaded_file.read(READ_CHUNK_BYTES):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise UploadRejected(f"文件超过大小限制（{max_bytes} 字节）")
                if not signature_checked:
                    if not chunk.startswith(PDF_SIGNATURE):
                        raise UploadRejected("文件内容不是有效的 PDF")
                    signature_checked = True
                output.write(chunk)
            if not signature_checked:
                raise UploadRejected("PDF 文件为空")
    except (OSError, UploadRejected):
        destination.unlink(missing_ok=True)
        raise


def _failed_record(document_id: str, file_name: str, error: str) -> DocumentRecord:
    """生成明确标记为 failed 的文档状态，不伪装成待解析。"""
    return DocumentRecord(document_id, file_name, DocumentStatus.FAILED, error=error)


def _save_failure(
    document_id: str, file_name: str, error: str, document_store: DocumentStore
) -> DocumentRecord:
    """将单文件失败状态写入存储；存储故障时仍返回失败结果。"""
    record = _failed_record(document_id, file_name, error)
    return _save_failure_record(record, document_store)


def _save_failure_record(record: DocumentRecord, document_store: DocumentStore) -> DocumentRecord:
    """尽力持久化失败状态，并将结果返回给本次上传响应。"""
    try:
        document_store.save(record)
    except DocumentStoreError:
        # 存储不可用时，调用方仍能从当前 HTTP 响应看到本文件失败。
        pass
    return record
