"""索引层：向量化 + Milvus 写入（delete → insert → flush）。"""

from __future__ import annotations

import logging

from app import embeddings, milvus
from app.config import get_settings
from app.errors import INDEX_FAILED
from app.ingest.chunker import Chunk
from app.ingest.parser import ParseError

logger = logging.getLogger(__name__)


def index_document(
    file_id: int,
    chunks: list[Chunk],
    *,
    file_name: str,
    file_path: str,
) -> int:
    settings = get_settings()
    if settings.mock_external:
        logger.info(
            "mock index: file_id=%s chunks=%s name=%s",
            file_id,
            len(chunks),
            file_name,
        )
        return len(chunks)

    try:
        milvus.ensure_collection()
        vectors = embeddings.embed_chunks(chunks)
    except Exception as exc:
        raise ParseError(INDEX_FAILED, f"向量化失败: {exc}") from exc

    records = []
    for chunk, (text_vector, clip_text, clip_image) in zip(chunks, vectors):
        records.append(
            {
                "text_vector": text_vector,
                "clip_text_vector": clip_text,
                "clip_image_vector": clip_image,
                "text": chunk.text,
                "db_id": file_id,
                "file_name": file_name,
                "file_path": file_path,
                "page": str(chunk.page) if chunk.page is not None else "",
            }
        )

    try:
        milvus.replace_document_vectors(file_id, records)
    except Exception as exc:
        raise ParseError(INDEX_FAILED, f"Milvus 写入失败: {exc}") from exc

    logger.info(
        "indexed: file_id=%s chunks=%s collection=%s",
        file_id,
        len(records),
        settings.milvus_collection,
    )
    return len(records)
