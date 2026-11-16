"""组合 MinerU 解析、切块、向量写入和文档摘要步骤。"""

from collections.abc import Callable
from typing import Protocol

from backend.app.core.config import Settings
from backend.app.documents.application.process_document import RetryableProcessingError
from backend.app.documents.domain.models import DocumentSummary, IngestEvent, ParsedDocument
from backend.app.documents.indexing.chunk_builder import build_chunks
from backend.app.documents.indexing.embed_chunks import TextEmbeddingClient
from backend.app.documents.indexing.write_vectors import ChunkVectorStore, write_vectors
from backend.app.documents.parsing.mineru_parser import parse_document
from backend.app.integrations.embedding_client import OpenAIEmbeddingClient
from backend.app.integrations.errors import EmbeddingError, SummaryError, VectorStoreError
from backend.app.integrations.milvus_store import MilvusChunkStore
from backend.app.integrations.summary_client import OpenAIDocumentSummaryClient


class DocumentSummaryClient(Protocol):
    """描述文档标题和摘要生成器的最小接口。"""

    def summarize(self, parsed: ParsedDocument) -> DocumentSummary:
        """根据解析结果生成标题与摘要。"""


class DocumentIndexer:
    """作为 Kafka 处理回调，持有单进程共享的向量和模型客户端。"""

    def __init__(
        self,
        settings: Settings,
        *,
        embedding_client: TextEmbeddingClient | None = None,
        vector_store: ChunkVectorStore | None = None,
        summary_client: DocumentSummaryClient | None = None,
        parser: Callable[[IngestEvent, Settings], ParsedDocument] | None = None,
    ) -> None:
        """一次性创建共享依赖，随后将同一实例交给所有 consumer 线程。"""
        self._settings = settings
        self._embedding_client = (
            embedding_client
            if embedding_client is not None
            else OpenAIEmbeddingClient(settings.embedding_base_url or "", settings.embedding_model)
        )
        self._vector_store = (
            vector_store
            if vector_store is not None
            else MilvusChunkStore.from_path(settings.milvus_path)
        )
        self._summary_client = (
            summary_client
            if summary_client is not None
            else OpenAIDocumentSummaryClient(settings.chat_base_url or "", settings.chat_model)
        )
        self._parser = parser if parser is not None else parse_document

    def __call__(self, event: IngestEvent) -> DocumentSummary:
        """完成解析、向量持久化和摘要生成后返回可完成状态的数据。"""
        parsed = self._parser(event, self._settings)
        chunks = build_chunks(parsed)
        try:
            write_vectors(chunks, self._embedding_client, self._vector_store)
            return self._summary_client.summarize(parsed)
        except (EmbeddingError, SummaryError, VectorStoreError) as error:
            raise RetryableProcessingError(f"文档索引或摘要生成暂时失败：{error}") from error
