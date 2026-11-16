"""读取单个 PDF 的处理状态。"""

from backend.app.documents.application.ports import DocumentStore
from backend.app.documents.domain.models import DocumentRecord


def get_document_status(document_id: str, document_store: DocumentStore) -> DocumentRecord | None:
    """按 document_id 返回文档状态；未知 ID 返回 None。"""
    return document_store.get(document_id)
