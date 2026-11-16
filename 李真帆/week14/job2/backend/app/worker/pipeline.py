import logging

from app.database import get_conn
from app.services import embed_service, event_bus, mineru_service
from app.services.chunk_service import build_chunks
from app.utils import now_iso

logger = logging.getLogger(__name__)


class DocDeleted(Exception):
    """解析过程中文档被删除。"""


def _publish(row) -> None:
    event_bus.publish_doc_status(row)


def process(doc_id: str, file_path: str, file_name: str) -> None:
    _set_status(doc_id, "parsing")
    parsed = mineru_service.parse_pdf(doc_id, file_path)
    chunks = build_chunks(parsed.md_text)
    if not chunks:
        raise ValueError("解析后无有效内容")

    texts = [c.embed_text or c.heading or file_name for c in chunks]
    vectors = embed_service.embed_texts(texts)

    ts = now_iso()
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is None:
            raise DocDeleted(doc_id)
        for c, v in zip(chunks, vectors):
            conn.execute(
                "INSERT INTO chunks (doc_id, chunk_index, chunk_type, heading, content,"
                " image_path, embed_text, embedding, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_id, c.chunk_index, c.chunk_type, c.heading, c.content, c.image_path,
                 c.embed_text, embed_service.vec_to_blob(v), ts),
            )
        row0 = conn.execute("SELECT description FROM documents WHERE id=?", (doc_id,)).fetchone()
        conn.execute(
            "UPDATE documents SET status='completed', error_msg='', page_count=?, chunk_count=?,"
            " description=CASE WHEN ?<>'' THEN ? ELSE description END, updated_at=? WHERE id=?",
            (parsed.page_count, len(chunks), parsed.description, parsed.description, ts, doc_id),
        )
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    _publish(row)
    logger.info("doc %s completed: %d chunks, %d pages", doc_id, len(chunks), parsed.page_count)


def _set_status(doc_id: str, status: str) -> None:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE documents SET status=?, error_msg='', updated_at=? WHERE id=?",
            (status, now_iso(), doc_id),
        )
        if cur.rowcount == 0:
            raise DocDeleted(doc_id)
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    _publish(row)
