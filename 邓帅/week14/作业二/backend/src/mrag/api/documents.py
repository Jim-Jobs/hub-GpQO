"""文档管理路由：上传 / 列表 / 删除（级联）/ 重试。"""

import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from kafka import KafkaProducer
from sqlalchemy import select

from mrag import zilliz_store
from mrag.config import PROCESSED_DIR, UPLOADS_DIR, get_settings
from mrag.models import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    FileRow,
    init_db,
)
from mrag.worker.ingest import processed_dir_for

log = logging.getLogger("mrag.api.documents")
router = APIRouter(prefix="/api/documents")

Session = init_db()


def _get_producer(app) -> KafkaProducer:
    """懒创建 KafkaProducer：Kafka 暂不可达时不阻塞 API 启动（spec：上传时才报 503）。"""
    if getattr(app.state, "kafka", None) is None:
        app.state.kafka = KafkaProducer(
            bootstrap_servers=get_settings().kafka_bootstrap,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            max_block_ms=8000,  # 不可达时 8s 快速失败，而非默认 60s
        )
    return app.state.kafka


def _send_kafka(request: Request, row: FileRow) -> None:
    """发引用消息；失败抛异常（调用方记录 error，保留 pending 供重试）。"""
    producer = _get_producer(request.app)
    payload = {
        "file_id": row.id,
        "file_name": row.filename,
        "file_path": row.file_path,
        "out_dir": str(processed_dir_for(row.filename)),
    }
    producer.send("rag-data", value=payload).get(timeout=10)
    producer.flush()


def _reset_to_pending(session, row: FileRow) -> None:
    row.status = STATUS_PENDING
    row.error = ""
    session.commit()


@router.post("")
def upload_documents(request: Request, files: list[UploadFile]):
    if not files:
        raise HTTPException(400, "未收到文件")
    results, errors = [], []
    with Session() as session:
        for f in files:
            if not (f.filename or "").lower().endswith(".pdf"):
                errors.append({"filename": f.filename, "error": "仅支持 PDF"})
                continue
            dest = UPLOADS_DIR / f"{uuid.uuid4().hex}.pdf"
            dest.write_bytes(f.file.read())
            row = FileRow(filename=f.filename, file_path=str(dest), status=STATUS_PENDING)
            session.add(row)
            session.commit()
            session.refresh(row)
            try:
                _send_kafka(request, row)
            except Exception as e:  # noqa: BLE001 - Kafka 不可达：保留 pending 供重试
                log.warning("Kafka 发送失败 file_id=%s: %s", row.id, e)
                row.error = f"Kafka 不可达: {e}"
                session.commit()
                errors.append({"filename": f.filename, "error": row.error})
            results.append(row.as_dict())
    status = 503 if errors and not results else (207 if errors else 200)
    return {"documents": results, "errors": errors, "status": status}


@router.get("")
def list_documents():
    with Session() as session:
        rows = session.scalars(select(FileRow).order_by(FileRow.id.desc())).all()
        return {"documents": [r.as_dict() for r in rows]}


@router.delete("/{file_id}")
def delete_document(file_id: int):
    with Session() as session:
        row = session.get(FileRow, file_id)
        if row is None:
            raise HTTPException(404, "文档不存在")
        stem_dir = processed_dir_for(row.filename)
        session.delete(row)
        session.commit()
    # 级联清理（尽力而为，单项失败不影响其余）
    for target in (Path(row.file_path), stem_dir):
        if target and Path(target).exists():
            shutil.rmtree(target, ignore_errors=True) if Path(target).is_dir() else Path(target).unlink(
                missing_ok=True
            )
    try:
        zilliz_store.delete_by_db_id(file_id)
    except Exception as e:  # noqa: BLE001
        log.warning("向量清理失败 file_id=%s: %s", file_id, e)
    return {"deleted": file_id}


@router.post("/{file_id}/retry")
def retry_document(file_id: int, request: Request):
    with Session() as session:
        row = session.get(FileRow, file_id)
        if row is None:
            raise HTTPException(404, "文档不存在")
        if row.status not in (STATUS_FAILED, STATUS_DONE, STATUS_PENDING):
            raise HTTPException(409, f"当前状态 {row.status} 不可重试（解析中请等待）")
        if not Path(row.file_path).is_file():
            row.status = STATUS_FAILED
            row.error = "本地文件已丢失，无法重试"
            session.commit()
            raise HTTPException(410, "本地文件已丢失，无法重试")
        _reset_to_pending(session, row)
    try:
        _send_kafka(request, row)
    except Exception as e:  # noqa: BLE001
        with Session() as session:
            r = session.get(FileRow, file_id)
            r.error = f"Kafka 不可达: {e}"
            session.commit()
        raise HTTPException(503, f"Kafka 不可达: {e}") from e
    with Session() as session:
        return session.get(FileRow, file_id).as_dict()


_ = (STATUS_DONE, STATUS_FAILED, PROCESSED_DIR)  # 保留导入引用（文档/诊断用）
