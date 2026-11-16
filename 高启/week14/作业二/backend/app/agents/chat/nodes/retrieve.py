"""将范围内检索词向量化，并限定在已完成文档范围内搜索。"""

from backend.app.agents.chat.models import ChatVectorStore, RetrievedChunk
from backend.app.documents.application.ports import DocumentStore
from backend.app.documents.domain.models import DocumentStatus
from backend.app.documents.indexing.embed_chunks import TextEmbeddingClient, embed_query


RETRIEVAL_LIMIT = 5


def retrieve_chunks(
    query: str,
    embedding_client: TextEmbeddingClient,
    vector_store: ChatVectorStore,
    document_store: DocumentStore,
    limit: int = RETRIEVAL_LIMIT,
) -> list[RetrievedChunk]:
    """只把 Redis 中 completed 的文档 ID 交给 Milvus，返回 Top-K 来源块。"""
    if limit < 1:
        raise ValueError("检索条数 limit 必须大于 0")

    completed_records = document_store.list_completed_documents()
    completed_by_id = {
        record.document_id: record
        for record in completed_records
        if record.status is DocumentStatus.COMPLETED
    }
    if not completed_by_id:
        return []
    query_vector = embed_query(query, embedding_client)
    hits = vector_store.search_chunks(query_vector, list(completed_by_id), limit)
    return [
        RetrievedChunk(hit.chunk, hit.score, completed_by_id[hit.chunk.document_id].file_name)
        for hit in hits
        if hit.chunk.document_id in completed_by_id
    ]
