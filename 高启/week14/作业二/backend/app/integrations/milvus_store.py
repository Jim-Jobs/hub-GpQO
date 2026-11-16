"""Milvus Lite 文本向量 collection 与幂等 upsert 适配器。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock, RLock
from typing import Any

from backend.app.documents.domain.models import ChunkRecord, ChunkSearchHit, VectorRecord
from backend.app.documents.indexing.embed_chunks import EMBEDDING_DIMENSION
from backend.app.integrations.errors import VectorStoreError


COLLECTION_NAME = "rag_chunks"
VECTOR_WRITE_BATCH_SIZE = 256
_stores: dict[Path, MilvusChunkStore] = {}
_stores_lock = RLock()


class MilvusChunkStore:
    """持有共享 Milvus client，并串行化同 collection 的所有写入。"""

    def __init__(self, client: Any, collection_name: str = COLLECTION_NAME) -> None:
        """保存共享客户端；同一 store 实例应交给所有消费线程共同使用。"""
        self._client = client
        self._collection_name = collection_name
        self._write_lock = Lock()

    @classmethod
    def from_path(cls, path: Path, collection_name: str = COLLECTION_NAME) -> MilvusChunkStore:
        """按数据库路径复用进程内唯一 client，并创建或载入固定 schema。"""
        resolved_path = path.resolve()
        with _stores_lock:
            if resolved_path in _stores:
                return _stores[resolved_path]
            try:
                from pymilvus import DataType, MilvusClient

                resolved_path.parent.mkdir(parents=True, exist_ok=True)
                client = MilvusClient(uri=str(resolved_path))
                _ensure_collection(client, collection_name, DataType)
                store = cls(client, collection_name)
            except Exception as error:
                raise VectorStoreError("Milvus Lite 初始化失败") from error
            _stores[resolved_path] = store
            return store

    def upsert_chunks(self, records: list[VectorRecord]) -> None:
        """使用 chunk_id 主键覆盖重复任务记录，写入在共享锁内串行执行。"""
        if not records:
            return
        rows = [
            {
                "chunk_id": record.chunk.chunk_id,
                "document_id": record.chunk.document_id,
                "page_number": record.chunk.page_number,
                "chunk_index": record.chunk.chunk_index,
                "text": record.chunk.text,
                "asset_ids": record.chunk.asset_ids,
                "embedding": record.embedding,
            }
            for record in records
        ]
        try:
            with self._write_lock:
                for start in range(0, len(rows), VECTOR_WRITE_BATCH_SIZE):
                    batch = rows[start : start + VECTOR_WRITE_BATCH_SIZE]
                    self._client.upsert(collection_name=self._collection_name, data=batch)
        except Exception as error:
            raise VectorStoreError("Milvus 文档向量写入失败") from error

    def search_chunks(
        self,
        embedding: list[float],
        document_ids: list[str],
        limit: int,
    ) -> list[ChunkSearchHit]:
        """只搜索指定 completed 文档并还原文本、来源页和相似度。"""
        if not document_ids or limit < 1:
            return []
        try:
            results = self._client.search(
                collection_name=self._collection_name,
                data=[embedding],
                anns_field="embedding",
                filter="document_id in {document_ids}",
                filter_params={"document_ids": document_ids},
                limit=limit,
                output_fields=[
                    "document_id",
                    "page_number",
                    "chunk_index",
                    "text",
                    "asset_ids",
                ],
                search_params={"metric_type": "COSINE", "params": {}},
            )
            return [_search_hit(item) for item in (results[0] if results else [])]
        except Exception as error:
            raise VectorStoreError("Milvus 文档向量检索失败") from error


def _ensure_collection(client: Any, name: str, data_type: Any) -> None:
    """创建文本、来源字段和 1024 维 cosine 向量索引，或载入已有 collection。"""
    if not client.has_collection(collection_name=name):
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            field_name="chunk_id",
            datatype=data_type.VARCHAR,
            is_primary=True,
            max_length=64,
        )
        schema.add_field(field_name="document_id", datatype=data_type.VARCHAR, max_length=256)
        schema.add_field(field_name="page_number", datatype=data_type.INT64)
        schema.add_field(field_name="chunk_index", datatype=data_type.INT64)
        schema.add_field(field_name="text", datatype=data_type.VARCHAR, max_length=8192)
        schema.add_field(
            field_name="asset_ids",
            datatype=data_type.ARRAY,
            element_type=data_type.VARCHAR,
            max_capacity=4096,
            max_length=256,
        )
        schema.add_field(
            field_name="embedding",
            datatype=data_type.FLOAT_VECTOR,
            dim=EMBEDDING_DIMENSION,
        )
        client.create_collection(collection_name=name, schema=schema)
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE"
        )
        client.create_index(collection_name=name, index_params=index_params)
    client.load_collection(collection_name=name)


def _search_hit(value: dict[str, Any]) -> ChunkSearchHit:
    """把 Milvus search 命中还原为业务块和 COSINE 分数。"""
    entity = value.get("entity") or {}
    chunk = ChunkRecord(
        chunk_id=str(value["id"]),
        document_id=str(entity["document_id"]),
        page_number=int(entity["page_number"]),
        chunk_index=int(entity["chunk_index"]),
        text=str(entity["text"]),
        asset_ids=list(entity.get("asset_ids") or []),
    )
    return ChunkSearchHit(chunk, float(value["distance"]))
