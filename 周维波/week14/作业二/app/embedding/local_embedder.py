"""本地模型向量化：bge-small-zh-v1.5（文本）+ jina-clip-v2（图文）。

和参考实现一致：
    bge_model = SentenceTransformer('.../bge-small-zh-v1.5')
    clip_model = SentenceTransformer('.../jina-clip-v2', trust_remote_code=True, truncate_dim=1024)
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ..config import settings
from .base import BaseEmbedder

os.environ.setdefault("HF_ENDPOINT", os.getenv("HF_ENDPOINT", "https://hf-mirror.com"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class LocalEmbedder(BaseEmbedder):
    name = "local"
    supports_cross_modal = True

    def __init__(self) -> None:
        self.text_dim = settings.text_dim
        self.clip_dim = settings.clip_dim
        self._text_model = None
        self._clip_model = None

    # ---- 惰性加载：API 进程只检索时也要等首次请求才加载模型 ----
    @property
    def text_model(self):
        if self._text_model is None:
            from sentence_transformers import SentenceTransformer

            self._text_model = SentenceTransformer(
                settings.text_embed_model, device=settings.embed_device
            )
            dim = self._text_model.get_sentence_embedding_dimension()
            if dim and int(dim) != self.text_dim:
                raise ValueError(
                    f"TEXT_EMBED_MODEL={settings.text_embed_model} 维度为 {dim}，"
                    f"与 TEXT_DIM={self.text_dim} 不一致，请对齐配置"
                )
        return self._text_model

    @property
    def clip_model(self):
        if self._clip_model is None:
            from sentence_transformers import SentenceTransformer

            self._clip_model = SentenceTransformer(
                settings.clip_model,
                device=settings.embed_device,
                trust_remote_code=True,
                truncate_dim=self.clip_dim,
            )
        return self._clip_model

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.text_dim), dtype=np.float32)
        vectors = self.text_model.encode(
            texts,
            batch_size=settings.embed_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def encode_clip_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.clip_dim), dtype=np.float32)
        vectors = self.clip_model.encode(
            texts,
            batch_size=settings.embed_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def encode_images(self, paths: list[str]) -> np.ndarray:
        matrix = np.zeros((len(paths), self.clip_dim), dtype=np.float32)
        valid_paths = [str(Path(path)) for path in paths if Path(path).exists()]
        if not valid_paths:
            return matrix
        try:
            vectors = self.clip_model.encode(
                valid_paths,
                batch_size=settings.embed_batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            vectors = np.asarray(vectors, dtype=np.float32)
        except Exception:
            # 单批失败时退化为逐张编码，避免一张坏图毁掉整篇文档
            rows: list[np.ndarray] = []
            for path in valid_paths:
                try:
                    rows.append(
                        np.asarray(
                            self.clip_model.encode(
                                str(path), normalize_embeddings=True, convert_to_numpy=True
                            ),
                            dtype=np.float32,
                        )
                    )
                except Exception:
                    rows.append(np.zeros(self.clip_dim, dtype=np.float32))
            vectors = np.vstack(rows) if rows else np.zeros((0, self.clip_dim), dtype=np.float32)

        valid_index = 0
        for row, path in enumerate(paths):
            if not Path(path).exists():
                continue
            if valid_index < len(vectors):
                matrix[row] = vectors[valid_index]
                valid_index += 1
        return matrix
