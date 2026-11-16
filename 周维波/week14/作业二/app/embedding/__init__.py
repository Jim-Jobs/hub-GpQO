"""Embedding 工厂：进程内单例，避免 API/Worker 反复加载模型。"""

from __future__ import annotations

from ..config import settings
from .base import BaseEmbedder
from .hash_embedder import HashEmbedder
from .local_embedder import LocalEmbedder

_EMBEDDER: BaseEmbedder | None = None


def get_embedder() -> BaseEmbedder:
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER

    backend = settings.embed_backend
    if backend in {"hash", "dev", "mock"}:
        _EMBEDDER = HashEmbedder(settings.text_dim, settings.clip_dim)
    elif backend in {"local", "bge", "clip"}:
        _EMBEDDER = LocalEmbedder()
    else:
        raise ValueError(f"未知 EMBED_BACKEND={backend}，可选值：local / hash")
    return _EMBEDDER


__all__ = ["BaseEmbedder", "HashEmbedder", "LocalEmbedder", "get_embedder"]
