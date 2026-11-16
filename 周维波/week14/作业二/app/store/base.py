"""向量库适配层：sqlite(本地) / Milvus(生产)，两者接口一致。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import numpy as np

VectorField = Literal["text", "clip_text", "clip_image"]


@dataclass
class VectorRecord:
    chunk_id: str
    doc_id: str
    text_vector: np.ndarray
    clip_text_vector: np.ndarray
    clip_image_vector: np.ndarray | None = None


@dataclass
class SearchHit:
    chunk_id: str
    score: float


class VectorStore(ABC):
    name: str = "base"

    @abstractmethod
    def upsert(self, records: list[VectorRecord]) -> int:
        """写入/覆盖向量，返回写入条数。"""

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """删除某篇文档的全部向量。"""

    @abstractmethod
    def search(
        self,
        vector: np.ndarray,
        field: VectorField,
        limit: int,
        doc_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        """按余弦相似度检索，可按 documents 过滤。"""

    def count(self) -> int:
        return 0
