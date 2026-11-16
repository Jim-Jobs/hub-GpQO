"""执行单个文档任务并持久化处理状态。"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from time import sleep
from typing import Callable

from backend.app.core.config import Settings
from backend.app.documents.application.ports import DocumentProcessor, DocumentStore
from backend.app.documents.domain.models import (
    DocumentRecord,
    DocumentStatus,
    IngestEvent,
)


class RetryableProcessingError(RuntimeError):
    """表示外部服务暂时失败，可在退避后重试。"""


class PermanentProcessingError(RuntimeError):
    """表示文件或任务输入错误，重试也不会成功。"""


@dataclass(frozen=True)
class ProcessingOutcome:
    """记录消费器处理结果、总尝试次数和最终错误摘要。"""

    status: DocumentStatus
    attempts: int
    error: str | None = None


def handle_ingest_event(
    event: IngestEvent,
    process_document: DocumentProcessor,
    document_store: DocumentStore,
    settings: Settings,
    sleep: Callable[[float], None] = sleep,
) -> ProcessingOutcome:
    """标记 parsing，按配置重试处理，并在完成或终止失败后持久化终态。"""
    record = document_store.get(event.document_id)
    if record is not None and record.status is DocumentStatus.COMPLETED:
        # 上次可能已写入结果但尚未提交 offset；完成记录可安全直接确认重投。
        return ProcessingOutcome(DocumentStatus.COMPLETED, attempts=0)
    if record is None:
        record = DocumentRecord(event.document_id, event.file_name, DocumentStatus.PENDING)
    parsing_record = replace(record, status=DocumentStatus.PARSING, error=None)
    document_store.save(parsing_record)

    for attempt in range(1, settings.processing_max_attempts + 1):
        try:
            summary = process_document(event)
        except PermanentProcessingError as error:
            return _mark_failed(parsing_record, attempt, error, document_store)
        except RetryableProcessingError as error:
            if attempt == settings.processing_max_attempts:
                return _mark_failed(parsing_record, attempt, error, document_store)
            sleep(_delay_for_attempt(settings.processing_retry_delays, attempt))
        else:
            completed = replace(
                parsing_record,
                status=DocumentStatus.COMPLETED,
                progress=100,
                title=summary.title if summary else parsing_record.title,
                summary=summary.summary if summary else parsing_record.summary,
                parsed_at=datetime.now(timezone.utc),
            )
            document_store.complete(completed)
            return ProcessingOutcome(DocumentStatus.COMPLETED, attempts=attempt)

    raise AssertionError("处理循环应在成功或失败分支返回")


def _delay_for_attempt(delays: tuple[int, ...], attempt: int) -> int:
    """取对应重试序号的退避时间；列表较短时沿用最后一个值。"""
    return delays[min(attempt - 1, len(delays) - 1)]


def _mark_failed(
    record: DocumentRecord,
    attempts: int,
    error: Exception,
    document_store: DocumentStore,
) -> ProcessingOutcome:
    """先写入 failed 状态，再返回可安全提交 offset 的结果。"""
    failed = replace(record, status=DocumentStatus.FAILED, error=str(error))
    document_store.save(failed)
    return ProcessingOutcome(DocumentStatus.FAILED, attempts=attempts, error=str(error))
