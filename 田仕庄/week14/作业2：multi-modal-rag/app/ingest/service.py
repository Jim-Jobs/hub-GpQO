"""上传与文档生命周期：落盘、去重、入队、状态查询、重解析、删除。"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import func

from app import errors
from app.config import get_settings
from app.db import Session, utcnow
from app.models import File
from app.ingest import state
from app.ingest.queue import enqueue_parse_task
from app.logging_setup import trace_id_var
from app.schemas import (
    DeleteDocumentResponse,
    DocumentItem,
    DocumentListResponse,
    LegacyDocumentItem,
    LegacyDocumentListResponse,
    ReparseResponse,
    StatusBatchResponse,
    StatusItem,
    UploadBatchResponse,
    UploadItemResult,
)

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_ALLOWED_MIME = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
}


class UploadRejected(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or errors.ERROR_TEXT.get(code, "上传失败")
        super().__init__(self.message)


def _sanitize_filename(name: str | None) -> str:
    raw = (name or "").replace("\\", "/").split("/")[-1]
    raw = "".join(
        ch for ch in raw if ch.isprintable() and ch not in '<>:"|?*'
    ).strip().strip(".")
    return raw or "unnamed.pdf"


def _new_batch_id() -> str:
    return f"b_{utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}"


def _save_stream(
    stream: BinaryIO, dest: Path, max_bytes: int
) -> tuple[str, int, bytes]:
    """流式落盘并计算 sha256，避免大文件整块进内存。"""
    digest = hashlib.sha256()
    total = 0
    first = b""
    with dest.open("wb") as out:
        while True:
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            if not first:
                first = chunk[:8]
            total += len(chunk)
            if total > max_bytes:
                raise UploadRejected(
                    errors.FILE_TOO_LARGE,
                    f"文件超过 {max_bytes // (1024 * 1024)}MB 上限",
                )
            digest.update(chunk)
            out.write(chunk)
    if total == 0:
        raise UploadRejected(errors.EMPTY_FILE)
    return digest.hexdigest(), total, first


def _find_existing(knowledge_base_id: str, sha256: str) -> tuple[int, str] | None:
    with Session() as session:
        row = (
            session.query(File.id, File.status)
            .filter(
                File.knowledge_base_id == knowledge_base_id,
                File.sha256 == sha256,
            )
            .order_by(File.id.desc())
            .first()
        )
    return (row[0], row[1]) if row else None


def build_task_payload(row: File) -> dict:
    trace_id = trace_id_var.get()
    if trace_id == "-":
        trace_id = f"t_{uuid.uuid4().hex[:12]}"
    return {
        "version": 1,
        "trace_id": trace_id,
        "id": row.id,
        "file_name": row.filename,
        "file_path": row.filepath,
        "knowledge_base_id": row.knowledge_base_id,
        "attempt": row.attempts or 0,
    }


def _enqueue_file(file_id: int) -> tuple[bool, str | None]:
    with Session() as session:
        row = session.get(File, file_id)
        if row is None:
            return False, errors.FILE_NOT_FOUND
        payload = build_task_payload(row)

    try:
        enqueue_parse_task(payload)
    except Exception as exc:
        logger.warning("enqueue failed for file %s: %s", file_id, exc)
        state.mark_failed(
            file_id,
            code=errors.ENQUEUE_FAILED,
            message=str(exc)[:500],
        )
        return False, errors.ENQUEUE_FAILED

    with Session() as session:
        row = session.get(File, file_id)
        if row is not None:
            row.kafka_sent = True
            row.updated_at = utcnow()
            session.commit()
    return True, None


def upload_batch(
    knowledge_base_id: str,
    files: list[tuple[str, BinaryIO, str | None]],
) -> UploadBatchResponse:
    settings = get_settings()
    settings.ensure_dirs()
    batch_id = _new_batch_id()

    items: list[UploadItemResult] = []
    queued: list[int] = []

    for filename, stream, content_type in files:
        display = _sanitize_filename(filename)

        if not display.lower().endswith(".pdf"):
            items.append(
                UploadItemResult(
                    filename=display,
                    status="REJECTED",
                    status_text="已拒绝",
                    action="rejected",
                    error_code=errors.UNSUPPORTED_TYPE,
                    error_message=errors.ERROR_TEXT[errors.UNSUPPORTED_TYPE],
                )
            )
            continue

        if content_type and content_type.lower() not in _ALLOWED_MIME:
            items.append(
                UploadItemResult(
                    filename=display,
                    status="REJECTED",
                    status_text="已拒绝",
                    action="rejected",
                    error_code=errors.UNSUPPORTED_TYPE,
                    error_message="Content-Type 不是 PDF",
                )
            )
            continue

        dest = settings.uploads_dir / f"{uuid.uuid4().hex}.pdf"
        try:
            sha256, size, first = _save_stream(
                stream, dest, settings.max_upload_bytes
            )
            if not first.startswith(b"%PDF-"):
                raise UploadRejected(errors.INVALID_PDF)
        except UploadRejected as exc:
            _safe_remove(dest)
            items.append(
                UploadItemResult(
                    filename=display,
                    status="REJECTED",
                    status_text="已拒绝",
                    action="rejected",
                    error_code=exc.code,
                    error_message=exc.message,
                )
            )
            continue
        except Exception as exc:  # pragma: no cover - 磁盘异常等
            _safe_remove(dest)
            logger.exception("save upload failed: %s", display)
            items.append(
                UploadItemResult(
                    filename=display,
                    status="REJECTED",
                    status_text="已拒绝",
                    action="rejected",
                    error_code=errors.UPLOAD_FAILED,
                    error_message=str(exc)[:500],
                )
            )
            continue

        existing = _find_existing(knowledge_base_id, sha256)
        if existing is not None:
            existing_id, _ = existing
            _safe_remove(dest)
            items.append(
                UploadItemResult(
                    filename=display,
                    file_id=existing_id,
                    status="DUPLICATE",
                    status_text="文件已存在",
                    action="duplicate",
                    error_code=errors.DUPLICATE,
                    error_message=errors.ERROR_TEXT[errors.DUPLICATE],
                    existing_file_id=existing_id,
                )
            )
            continue

        with Session() as session:
            row = File(
                filename=display,
                filepath=str(dest),
                status=state.PENDING,
                knowledge_base_id=knowledge_base_id,
                batch_id=batch_id,
                sha256=sha256,
                size_bytes=size,
                kafka_sent=False,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(row)
            session.flush()
            file_id = row.id
            session.commit()

        queued.append(file_id)
        items.append(
            UploadItemResult(
                filename=display,
                file_id=file_id,
                status=state.PENDING,
                status_text=state.status_text(state.PENDING),
                action="queued",
            )
        )

    # 批量插入后统一投递，producer 复用
    for file_id in queued:
        ok, code = _enqueue_file(file_id)
        if ok:
            continue
        for item in items:
            if item.file_id == file_id:
                item.status = state.FAILED
                item.status_text = state.status_text(state.FAILED)
                item.error_code = code
                item.error_message = errors.ERROR_TEXT.get(code or "", "入队失败")
                break

    return UploadBatchResponse(
        batch_id=batch_id,
        knowledge_base_id=knowledge_base_id,
        accepted=len(queued),
        rejected=sum(1 for item in items if item.action == "rejected"),
        duplicates=sum(1 for item in items if item.action == "duplicate"),
        items=items,
    )


def _to_item(row: File) -> DocumentItem:
    start = row.started_at or row.created_at
    end = row.finished_at or utcnow()
    elapsed = (end - start).total_seconds() if start is not None else None
    return DocumentItem(
        file_id=row.id,
        filename=row.filename,
        status=row.status,
        status_text=state.status_text(row.status),
        knowledge_base_id=row.knowledge_base_id,
        batch_id=row.batch_id,
        filepath=row.filepath,
        size_bytes=row.size_bytes,
        page_count=row.page_count,
        chunk_count=row.chunk_count,
        error_code=row.error_code,
        error_message=row.error_message,
        attempts=row.attempts or 0,
        kafka_sent=bool(row.kafka_sent),
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        elapsed_sec=elapsed,
    )


def list_documents(
    knowledge_base_id: str,
    *,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> DocumentListResponse:
    with Session() as session:
        query = session.query(File).filter(
            File.knowledge_base_id == knowledge_base_id
        )
        if status:
            query = query.filter(File.status == status)
        total = query.count()
        rows = (
            query.order_by(File.id.desc())
            .offset(max(0, offset))
            .limit(limit)
            .all()
        )
    return DocumentListResponse(
        knowledge_base_id=knowledge_base_id,
        total=total,
        items=[_to_item(row) for row in rows],
    )


def list_legacy(knowledge_base_id: str) -> LegacyDocumentListResponse:
    with Session() as session:
        rows = (
            session.query(File)
            .filter(File.knowledge_base_id == knowledge_base_id)
            .order_by(File.id.desc())
            .all()
        )
    return LegacyDocumentListResponse(
        knowledge_base_id=knowledge_base_id,
        documents=[
            LegacyDocumentItem(
                file_id=row.id,
                filename=row.filename,
                filestate=state.status_text(row.status),
                status=row.status,
                knowledge_base_id=row.knowledge_base_id,
                filepath=row.filepath,
            )
            for row in rows
        ],
    )


def get_statuses(file_ids: list[int]) -> StatusBatchResponse:
    items: list[StatusItem] = []
    if file_ids:
        with Session() as session:
            rows = session.query(File).filter(File.id.in_(file_ids)).all()
        by_id = {row.id: row for row in rows}
        for file_id in file_ids:
            row = by_id.get(file_id)
            if row is None:
                continue
            items.append(
                StatusItem(
                    file_id=row.id,
                    status=row.status,
                    status_text=state.status_text(row.status),
                    updated_at=row.updated_at,
                )
            )
    return StatusBatchResponse(server_time=utcnow(), items=items)


def reparse_document(file_id: int) -> ReparseResponse:
    with Session() as session:
        row = session.get(File, file_id)
        if row is None:
            raise errors.AppError(errors.FILE_NOT_FOUND, status_code=404)
        if row.status not in (state.DONE, state.FAILED):
            raise errors.AppError(errors.INVALID_STATE, status_code=409)

    if not state.reset_for_reparse(file_id):
        raise errors.AppError(errors.INVALID_STATE, status_code=409)

    ok, _ = _enqueue_file(file_id)
    with Session() as session:
        row = session.get(File, file_id)
        status = row.status if row else state.FAILED

    return ReparseResponse(
        file_id=file_id,
        status=status,
        status_text=state.status_text(status),
        message="已重新加入解析队列" if ok else "重新入队失败，请稍后重试",
    )


def _safe_remove(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:  # pragma: no cover
        logger.warning("remove file failed: %s", path, exc_info=True)


def _remove_processed_tree(filepath: str) -> None:
    settings = get_settings()
    stem = Path(filepath).stem
    for root in {settings.processed_dir, settings.data_dir / "processed"}:
        target = Path(root) / stem
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


def delete_document(file_id: int) -> DeleteDocumentResponse:
    with Session() as session:
        row = session.get(File, file_id)
        if row is None:
            return DeleteDocumentResponse(file_id=file_id, status="not_found")
        filepath = row.filepath
        session.delete(row)
        session.commit()

    if filepath:
        _safe_remove(Path(filepath))
        _remove_processed_tree(filepath)

    logger.info("document deleted: file_id=%s", file_id)
    return DeleteDocumentResponse(file_id=file_id, status="deleted")


def get_knowledge_base_of(file_id: int) -> str | None:
    with Session() as session:
        row = session.get(File, file_id)
        return row.knowledge_base_id if row else None


def list_done_file_ids(knowledge_base_id: str) -> list[int]:
    with Session() as session:
        rows = (
            session.query(File.id)
            .filter(
                File.knowledge_base_id == knowledge_base_id,
                File.status == state.DONE,
            )
            .all()
        )
    return [row[0] for row in rows]


def count_by_status() -> dict[str, int]:
    with Session() as session:
        rows = (
            session.query(File.status, func.count(File.id))
            .group_by(File.status)
            .all()
        )
    return {status: count for status, count in rows}
