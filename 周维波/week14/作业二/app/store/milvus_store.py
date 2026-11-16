"""Milvus 向量库（对应参考实现的 rag_data_new collection）。

一个 collection 里放三种向量：
  text_vector        bge 文本向量（以文搜文）
  clip_text_vector   CLIP 文本向量（以文搜图时编码 query）
  clip_image_vector  CLIP 图像向量（以文搜图 / 以图搜图）
检索时按 anns_field 选其一，过滤条件按 doc_id。
"""

from __future__ import annotations

import numpy as np

from ..config import settings
from .base import SearchHit, VectorField, VectorRecord, VectorStore

_FIELD_MAP: dict[str, str] = {
    "text": "text_vector",
    "clip_text": "clip_text_vector",
    "clip_image": "clip_image_vector",
}


class MilvusVectorStore(VectorStore):
    name = "milvus"

    def __init__(self) -> None:
        from pymilvus import MilvusClient  # 惰性导入：不用 Milvus 的机器不需要装

        if not settings.milvus_uri:
            raise ValueError("VECTOR_STORE=milvus 时必须配置 MILVUS_URI")
        self.client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token or None)
        self.collection = settings.milvus_collection
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        if self.client.has_collection(self.collection):
            self.client.load_collection(self.collection)
            return

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("text_vector", DataType.FLOAT_VECTOR, dim=settings.text_dim)
        schema.add_field("clip_text_vector", DataType.FLOAT_VECTOR, dim=settings.clip_dim)
        schema.add_field("clip_image_vector", DataType.FLOAT_VECTOR, dim=settings.clip_dim)

        index_params = self.client.prepare_index_params()
        for field in ("text_vector", "clip_text_vector", "clip_image_vector"):
            index_params.add_index(
                field_name=field, index_type="AUTOINDEX", metric_type="COSINE"
            )

        self.client.create_collection(
            collection_name=self.collection, schema=schema, index_params=index_params
        )
        self.client.load_collection(self.collection)

    def upsert(self, records: list[VectorRecord]) -> int:
        if not records:
            return 0
        rows = []
        for record in records:
            row = {
                "chunk_id": record.chunk_id,
                "doc_id": record.doc_id,
                "text_vector": np.asarray(record.text_vector, dtype=np.float32).tolist(),
                "clip_text_vector": np.asarray(
                    record.clip_text_vector, dtype=np.float32
                ).tolist(),
                "clip_image_vector": np.asarray(
                    record.clip_image_vector
                    if record.clip_image_vector is not None
                    else np.zeros(settings.clip_dim, dtype=np.float32),
                    dtype=np.float32,
                ).tolist(),
            }
            rows.append(row)
        self.client.upsert(collection_name=self.collection, data=rows)
        return len(rows)

    def delete_document(self, doc_id: str) -> None:
        self.client.delete(
            collection_name=self.collection, filter=f'doc_id == "{doc_id}"'
        )

    def search(
        self,
        vector: np.ndarray,
        field: VectorField,
        limit: int,
        doc_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        anns_field = _FIELD_MAP[field]
        expr = ""
        if doc_ids:
            quoted = ", ".join(f'"{doc_id}"' for doc_id in doc_ids)
            expr = f"doc_id in [{quoted}]"

        results = self.client.search(
            collection_name=self.collection,
            data=[np.asarray(vector, dtype=np.float32).tolist()],
            anns_field=anns_field,
            limit=max(1, limit),
            filter=expr,
            output_fields=["chunk_id", "doc_id"],
        )
        hits: list[SearchHit] = []
        for item in results[0] if results else []:
            entity = item.get("entity", item)
            chunk_id = entity.get("chunk_id") or item.get("id")
            hits.append(SearchHit(chunk_id=str(chunk_id), score=float(item.get("distance", 0.0))))
        return hits

    def count(self) -> int:
        try:
            stats = self.client.get_collection_stats(self.collection)
            return int(stats.get("row_count", 0))
        except Exception:
            return 0
