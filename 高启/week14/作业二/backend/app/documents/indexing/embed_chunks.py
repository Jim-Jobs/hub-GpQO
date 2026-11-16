"""为文本块请求向量并校验 BGE-M3 输出数据。"""

import math
from typing import Protocol

from backend.app.documents.domain.models import ChunkRecord, VectorRecord
from backend.app.integrations.errors import EmbeddingError


EMBEDDING_DIMENSION = 1024


class TextEmbeddingClient(Protocol):
    """描述文本 embedding 适配器的最小接口。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """按输入顺序返回每条文本的向量。"""


def embed_chunks(
    chunks: list[ChunkRecord], embedding_client: TextEmbeddingClient
) -> list[VectorRecord]:
    """批量向量化文本块，并拒绝数量或 1024 维度不匹配的响应。"""
    if not chunks:
        return []
    try:
        vectors = embedding_client.embed_texts([chunk.text for chunk in chunks])
    except EmbeddingError:
        raise
    except Exception as error:
        raise EmbeddingError("请求 BGE-M3 embedding 失败") from error
    if len(vectors) != len(chunks):
        raise EmbeddingError("BGE-M3 返回的向量数量与文本块数量不一致")

    records: list[VectorRecord] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as error:
            raise EmbeddingError("BGE-M3 返回了无效向量数据") from error
        if len(values) != EMBEDDING_DIMENSION:
            raise EmbeddingError(
                f"BGE-M3 向量维度错误：预期 {EMBEDDING_DIMENSION}，实际 {len(values)}"
            )
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingError("BGE-M3 向量包含非有限数值")
        records.append(VectorRecord(chunk, values))
    return records


def embed_query(text: str, embedding_client: TextEmbeddingClient) -> list[float]:
    """向量化单条问答检索词并校验 BGE-M3 数量、维数和数值。"""
    if not text.strip():
        raise EmbeddingError("检索词不能为空")
    try:
        vectors = embedding_client.embed_texts([text])
    except EmbeddingError:
        raise
    except Exception as error:
        raise EmbeddingError("请求 BGE-M3 检索向量失败") from error
    if len(vectors) != 1:
        raise EmbeddingError("BGE-M3 返回的检索向量数量不是 1")
    return _validate_vector(vectors[0])


def _validate_vector(vector: list[float]) -> list[float]:
    """统一校验 1024 维向量并转换为有限浮点数。"""
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError) as error:
        raise EmbeddingError("BGE-M3 返回了无效向量数据") from error
    if len(values) != EMBEDDING_DIMENSION:
        raise EmbeddingError(
            f"BGE-M3 向量维度错误：预期 {EMBEDDING_DIMENSION}，实际 {len(values)}"
        )
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingError("BGE-M3 向量包含非有限数值")
    return values
