"""文档接口：批量上传（投递 Kafka）、状态查询、删除、重新解析。"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..db import session_scope
from ..kafka_bus import publish_parse_request
from ..models import Chunk, Document
from ..schemas import document_payload
from ..status import STATUS_FAILED, STATUS_PENDING, STAGE_QUEUED
from ..store import get_store

router = APIRouter(prefix="/api/documents", tags=["documents"])
logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {".pdf"}


def _save_upload(upload: UploadFile, document_id: str, suffix: str) -> tuple[Path, int, str]:
    target = settings.upload_dir / f"{document_id}{suffix}"
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as sink:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            sink.write(chunk)
    return target, size, digest.hexdigest()


def _list_documents() -> list[dict]:
    with session_scope() as session:
        rows = list(session.execute(select(Document).order_by(Document.created_at.desc())).scalars())
        return [document_payload(row) for row in rows]


@router.get("")
def list_documents() -> dict:
    documents = _list_documents()
    return {
        "documents": documents,
        "total": len(documents),
        "completed": sum(1 for item in documents if item["status"] == "completed"),
        "parsing": sum(1 for item in documents if item["status"] == "parsing"),
        "pending": sum(1 for item in documents if item["status"] == "pending"),
        "failed": sum(1 for item in documents if item["status"] == "failed"),
    }


@router.post("", status_code=201)
async def upload_documents(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")
    if len(files) > settings.max_files_per_upload:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多上传 {settings.max_files_per_upload} 个文件",
        )

    created: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        original_name = Path(upload.filename or "document.pdf").name
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            errors.append({"filename": original_name, "error": "仅支持 PDF 文件"})
            continue
        if upload.size and upload.size > settings.max_upload_mb * 1024 * 1024:
            errors.append(
                {
                    "filename": original_name,
                    "error": f"文件超过 {settings.max_upload_mb}MB 上限",
                }
            )
            continue

        with session_scope() as session:
            document = Document(
                filename=original_name,
                stored_path="",
                status=STATUS_PENDING,
                stage=STAGE_QUEUED,
                progress=0,
                file_type=suffix.lstrip("."),
            )
            session.add(document)
            session.flush()
            document_id = document.id

        try:
            target, size, sha256 = await run_in_threadpool(
                _save_upload, upload, document_id, suffix
            )
        except OSError as exc:
            errors.append({"filename": original_name, "error": f"保存文件失败：{exc}"})
            continue

        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is not None:
                document.stored_path = str(target)
                document.size_bytes = size
                document.sha256 = sha256
            payload = document_payload(document) if document is not None else None

        # 关键一步：把“解析任务”交给 Kafka，Web 请求立即返回（解析耗时 1 分钟级）
        try:
            publish_parse_request(document_id, original_name, str(target))
        except Exception as exc:
            logger.error("投递解析任务失败：%s", exc)
            with session_scope() as session:
                document = session.get(Document, document_id)
                if document is not None:
                    document.status = STATUS_FAILED
                    document.stage = "投递失败"
                    document.error = f"Kafka 投递失败：{exc}"
                    payload = document_payload(document)
            errors.append(
                {
                    "filename": original_name,
                    "error": f"Kafka 不可用，解析任务未投递：{exc}",
                }
            )

        if payload:
            created.append(payload)

    return {"documents": created, "errors": errors}


@router.get("/{document_id}")
def get_document(document_id: str) -> dict:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        return document_payload(document)


@router.post("/{document_id}/reparse")
def reparse_document(document_id: str) -> dict:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        if not Path(document.stored_path).is_file():
            raise HTTPException(status_code=410, detail="原始文件已丢失，无法重新解析")

        document.status = STATUS_PENDING
        document.stage = STAGE_QUEUED
        document.progress = 0
        document.error = None
        document.attempts = 0
        session.flush()
        payload = document_payload(document)
        stored_path = document.stored_path
        filename = document.filename

    try:
        publish_parse_request(document_id, filename, stored_path, attempt=1)
    except Exception as exc:
        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is not None:
                document.status = STATUS_FAILED
                document.stage = "投递失败"
                document.error = f"Kafka 投递失败：{exc}"
        raise HTTPException(status_code=503, detail=f"Kafka 不可用：{exc}") from exc

    return {"document": payload, "message": "已重新投递解析任务"}


@router.delete("/{document_id}")
def delete_document(document_id: str) -> dict:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        stored_path = Path(document.stored_path) if document.stored_path else None
        parsed_dir = Path(document.parsed_dir) if document.parsed_dir else None
        session.execute(delete(Chunk).where(Chunk.doc_id == document_id))
        session.delete(document)

    try:
        get_store().delete_document(document_id)
    except Exception as exc:
        logger.warning("删除向量失败（%s）：%s", document_id, exc)

    # 只删自己管理的目录，避免误删用户文件
    if parsed_dir is not None:
        try:
            parsed_dir.resolve().relative_to(settings.parsed_dir.resolve())
            if parsed_dir.is_dir():
                shutil.rmtree(parsed_dir, ignore_errors=True)
        except ValueError:
            logger.warning("跳过异常解析目录清理：%s", parsed_dir)

    if stored_path is not None:
        try:
            stored_path.resolve().relative_to(settings.upload_dir.resolve())
            stored_path.unlink(missing_ok=True)
        except ValueError:
            logger.warning("跳过异常上传路径清理：%s", stored_path)

    return {"id": document_id, "deleted": True, "deleted_at": datetime.now(timezone.utc).isoformat()}
