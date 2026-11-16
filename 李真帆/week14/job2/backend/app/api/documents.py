import logging
import shutil

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import settings
from app.database import get_conn
from app.schemas import DocumentListOut, DocumentOut
from app.services import event_bus, kafka_service
from app.utils import gen_id, now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_FILE_SIZE = 100 * 1024 * 1024
STATUSES = ("pending", "parsing", "completed", "failed")


def _save_upload(f: UploadFile, doc_id: str) -> tuple[str, int]:
    dest = settings.uploads_dir / f"{doc_id}.pdf"
    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = f.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, f"文件超过 100MB: {f.filename}")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    return str(dest), size


@router.post("", response_model=list[DocumentOut])
def upload_documents(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "未选择文件")
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"仅支持 PDF 文件: {f.filename}")

    settings.ensure_dirs()
    saved: list[DocumentOut] = []
    for f in files:
        doc_id = gen_id()
        file_path, size = _save_upload(f, doc_id)
        ts = now_iso()
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO documents (id, file_name, file_size, file_path, description, status,"
                " error_msg, page_count, chunk_count, updated_at, created_at)"
                " VALUES (?,?,?,?,?,'pending','',0,0,?,?)",
                (doc_id, f.filename, size, file_path, (f.filename or "pdf").rsplit(".", 1)[0], ts, ts),
            )
        try:
            kafka_service.send_parse_task(doc_id, file_path, f.filename or "")
        except Exception as e:
            logger.exception("kafka send failed, doc=%s", doc_id)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE documents SET status='failed', error_msg=?, updated_at=? WHERE id=?",
                    (f"投递队列失败: {e}", now_iso(), doc_id),
                )
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        saved.append(DocumentOut(**dict(row)))
        event_bus.publish_doc_status(row)
    return saved


@router.get("", response_model=DocumentListOut)
def list_documents(page: int = 1, size: int = 10, status: str | None = None):
    page = max(page, 1)
    size = min(max(size, 1), 50)
    where, args = "", []
    if status:
        if status not in STATUSES:
            raise HTTPException(400, "无效的状态筛选")
        where, args = "WHERE status = ?", [status]
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM documents {where}", args).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM documents {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*args, size, (page - 1) * size],
        ).fetchall()
    return DocumentListOut(total=total, items=[DocumentOut(**dict(r)) for r in rows])


@router.delete("/{doc_id}")
def delete_document(doc_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    (settings.uploads_dir / f"{doc_id}.pdf").unlink(missing_ok=True)
    shutil.rmtree(settings.parsed_dir / doc_id, ignore_errors=True)
    event_bus.publish_event("doc_deleted", {"id": doc_id})
    return {"ok": True}


@router.post("/{doc_id}/reparse", response_model=DocumentOut)
def reparse_document(doc_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        if row["status"] == "parsing":
            raise HTTPException(409, "文档正在解析中")
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute(
            "UPDATE documents SET status='pending', error_msg='', chunk_count=0, updated_at=? WHERE id=?",
            (now_iso(), doc_id),
        )
    shutil.rmtree(settings.parsed_dir / doc_id, ignore_errors=True)
    try:
        kafka_service.send_parse_task(doc_id, row["file_path"], row["file_name"])
    except Exception as e:
        logger.exception("kafka send failed, doc=%s", doc_id)
        with get_conn() as conn:
            conn.execute(
                "UPDATE documents SET status='failed', error_msg=?, updated_at=? WHERE id=?",
                (f"投递队列失败: {e}", now_iso(), doc_id),
            )
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    event_bus.publish_doc_status(row)
    return DocumentOut(**dict(row))
