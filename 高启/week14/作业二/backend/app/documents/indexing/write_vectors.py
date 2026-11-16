"""把文本块向量化后写入 Milvus Lite。"""

from typing import Protocol

from backend.app.documents.domain.models import ChunkRecord, VectorRecord
from backend.app.documents.indexing.embed_chunks import TextEmbeddingClient, embed_chunks


class ChunkVectorStore(Protocol):
    """描述检索向量存储必须提供的 upsert 操作。"""

    def upsert_chunks(self, records: list[VectorRecord]) -> None:
        """按稳定 chunk_id 新增或覆盖向量记录。"""


def write_vectors(
    chunks: list[ChunkRecord],
    embedding_client: TextEmbeddingClient,
    vector_store: ChunkVectorStore,
) -> list[VectorRecord]:
    """将文本块转成向量并 upsert；空文档不调用 embedding 或 Milvus。"""
    records = embed_chunks(chunks, embedding_client)
    if records:
        vector_store.upsert_chunks(records)
    return records
