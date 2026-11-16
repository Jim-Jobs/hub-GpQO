"""对外 JSON 结构（FastAPI 直接返回 dict，这里统一序列化口径）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Chunk, Document
from .status import status_label


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def document_payload(doc: Document, chunk_count: int | None = None) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "status_label": status_label(doc.status),
        "stage": doc.stage,
        "progress": doc.progress,
        "error": doc.error,
        "attempts": doc.attempts,
        "size_bytes": doc.size_bytes,
        "file_type": doc.file_type,
        "chunk_count": doc.chunk_count if chunk_count is None else chunk_count,
        "image_count": doc.image_count,
        "elapsed_ms": doc.elapsed_ms,
        "created_at": isoformat(doc.created_at),
        "updated_at": isoformat(doc.updated_at),
        "has_parsed_dir": bool(doc.parsed_dir),
    }


def chunk_count_by_doc(session: Session, doc_id: str) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.doc_id == doc_id)
        ).scalar_one()
    )
