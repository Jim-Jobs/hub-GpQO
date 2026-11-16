"""本地向量库：向量以 float32 BLOB 存 SQLite，检索时 numpy 暴力余弦。

适用：单机开发、演示、几百到几万切片的规模；无需额外中间件。
生产环境切 VECTOR_STORE=milvus 即可，检索接口完全一致。
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import delete, select

from ..db import session_scope
from ..models import ChunkVector
from .base import SearchHit, VectorRecord, VectorField, VectorStore


def _to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    vector = np.frombuffer(blob, dtype=np.float32)
    return vector if vector.size else None


class SqliteVectorStore(VectorStore):
    name = "sqlite"

    def upsert(self, records: list[VectorRecord]) -> int:
        if not records:
            return 0
        with session_scope() as session:
            for record in records:
                session.merge(
                    ChunkVector(
                        chunk_id=record.chunk_id,
                        doc_id=record.doc_id,
                        text_vector=_to_blob(record.text_vector),
                        clip_text_vector=_to_blob(record.clip_text_vector),
                        clip_image_vector=(
                            _to_blob(record.clip_image_vector)
                            if record.clip_image_vector is not None
                            else None
                        ),
                    )
                )
        return len(records)

    def delete_document(self, doc_id: str) -> None:
        with session_scope() as session:
            session.execute(delete(ChunkVector).where(ChunkVector.doc_id == doc_id))

    def search(
        self,
        vector: np.ndarray,
        field: VectorField,
        limit: int,
        doc_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        column = {
            "text": ChunkVector.text_vector,
            "clip_text": ChunkVector.clip_text_vector,
            "clip_image": ChunkVector.clip_image_vector,
        }[field]

        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        if field == "clip_image":
            query = query

        stmt = select(ChunkVector.chunk_id, ChunkVector.doc_id, column)
        if doc_ids:
            stmt = stmt.where(ChunkVector.doc_id.in_(doc_ids))

        with session_scope() as session:
            rows = session.execute(stmt).all()

        if not rows:
            return []

        ids: list[str] = []
        matrix_rows: list[np.ndarray] = []
        for chunk_id, _doc_id, blob in rows:
            candidate = _from_blob(blob)
            if candidate is None or candidate.size != query.size:
                continue
            ids.append(chunk_id)
            matrix_rows.append(candidate)

        if not matrix_rows:
            return []

        matrix = np.vstack(matrix_rows)
        # 向量写入时已归一化，点积即余弦相似度；再除一次范数兜底
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0
        query_norm = float(np.linalg.norm(query)) or 1.0
        scores = (matrix @ query) / (norms * query_norm)

        top_n = max(1, min(limit, len(scores)))
        top_indices = np.argsort(-scores)[:top_n]
        return [
            SearchHit(chunk_id=ids[int(index)], score=float(scores[int(index)]))
            for index in top_indices
            if scores[int(index)] > 0
        ]

    def count(self) -> int:
        with session_scope() as session:
            return len(session.execute(select(ChunkVector.chunk_id)).all())
