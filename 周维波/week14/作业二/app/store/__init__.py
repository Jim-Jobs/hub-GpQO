"""向量库工厂：VECTOR_STORE=sqlite|milvus（进程内单例）。"""

from __future__ import annotations

from ..config import settings
from .base import SearchHit, VectorField, VectorRecord, VectorStore
from .sqlite_store import SqliteVectorStore

_STORE: VectorStore | None = None


def get_store() -> VectorStore:
    global _STORE
    if _STORE is not None:
        return _STORE

    backend = settings.vector_store
    if backend in {"sqlite", "local"}:
        _STORE = SqliteVectorStore()
    elif backend == "milvus":
        from .milvus_store import MilvusVectorStore

        _STORE = MilvusVectorStore()
    else:
        raise ValueError(f"未知 VECTOR_STORE={backend}，可选值：sqlite / milvus")
    return _STORE


__all__ = [
    "SearchHit",
    "SqliteVectorStore",
    "VectorField",
    "VectorRecord",
    "VectorStore",
    "get_store",
]
