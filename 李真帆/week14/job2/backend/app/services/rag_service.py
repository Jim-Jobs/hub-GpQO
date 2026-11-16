import logging
import threading

import numpy as np

from app.config import settings
from app.database import get_conn
from app.services.embed_service import blob_to_vec, embed_texts

logger = logging.getLogger(__name__)


class VectorIndex:
    """内存向量矩阵缓存。每次检索前用 COUNT+MAX(id) 廉价探测 chunks 是否变化，变化则全量重建。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._ids: list[int] = []
        self._fingerprint: tuple[int, int] = (-1, -1)
        self._unit: np.ndarray | None = None

    def _fingerprint_now(self) -> tuple[int, int]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(MAX(id), 0) AS m FROM chunks WHERE embedding IS NOT NULL"
            ).fetchone()
        return row["c"], row["m"]

    def _rebuild(self) -> None:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL ORDER BY id"
            ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            mat = np.vstack([blob_to_vec(r["embedding"]) for r in rows]).astype(np.float32)
            self._unit = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10)
            self._ids = ids
        else:
            self._unit = None
            self._ids = []
        self._fingerprint = self._fingerprint_now()
        logger.info("向量索引已重建: %d chunks", len(self._ids))

    def search(self, query_vec: list[float], top_k: int) -> list[tuple[int, float]]:
        with self._lock:
            if self._fingerprint != self._fingerprint_now():
                self._rebuild()
            if self._unit is None or not self._ids:
                return []
            q = np.asarray(query_vec, dtype=np.float32)
            if q.shape[0] != self._unit.shape[1]:
                logger.error(
                    "查询向量维度 %d 与索引维度 %d 不一致（可能切换过 embedding 模型），"
                    "请运行 python scripts/rebuild_vectors.py", q.shape[0], self._unit.shape[1],
                )
                return []
            q = q / (np.linalg.norm(q) + 1e-10)
            scores = self._unit @ q
        order = np.argsort(-scores)[:top_k]
        return [(self._ids[i], float(scores[i])) for i in order]


index = VectorIndex()


def retrieve(question: str) -> list[dict]:
    """相似度检索 top-k 切片（带文档名），供相关性判断与上下文组装。"""
    if not question.strip():
        return []
    vec = embed_texts([question])[0]
    hits = index.search(vec, settings.top_k)
    if not hits:
        return []
    with get_conn() as conn:
        marks = ",".join("?" * len(hits))
        rows = {
            r["id"]: r
            for r in conn.execute(
                f"SELECT c.id, c.doc_id, c.heading, c.chunk_type, c.content,"
                f" d.file_name AS doc_name FROM chunks c"
                f" JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({marks})",
                [h[0] for h in hits],
            ).fetchall()
        }
    out = []
    for chunk_id, score in hits:
        r = rows.get(chunk_id)
        if r is not None:
            out.append({
                "doc_id": r["doc_id"],
                "doc_name": r["doc_name"],
                "heading": r["heading"],
                "chunk_type": r["chunk_type"],
                "content": r["content"],
                "score": round(score, 4),
            })
    return out


def build_context(refs: list[dict]) -> str:
    parts = []
    for i, r in enumerate(refs, 1):
        parts.append(f"【片段{i}】来源: 《{r['doc_name']}》 {r['heading']}\n{r['content']}")
    return "\n\n".join(parts)
