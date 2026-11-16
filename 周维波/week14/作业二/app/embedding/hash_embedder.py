"""确定性哈希向量：零模型依赖，供本地开发 / CI 跑通全链路。

注意：它只在“同模态内”有粗略相似度（字面重合），
不具备 CLIP 的图文对齐能力，所以 supports_cross_modal=False，
检索时退化为“文本检索 + 取命中切片附带的图片”。
"""

from __future__ import annotations

import hashlib
import re

import numpy as np
from PIL import Image

from .base import BaseEmbedder

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _bucket(token: str, dim: int) -> int:
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % dim


def _tokenize(text: str) -> list[str]:
    text = " ".join(text.split()).lower()
    if not text:
        return []
    tokens = _WORD_RE.findall(text)
    cjk = _CJK_RE.findall(text)
    tokens.extend(f"{cjk[i]}{cjk[i + 1]}" for i in range(len(cjk) - 1))
    return tokens


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class HashEmbedder(BaseEmbedder):
    name = "hash"
    supports_cross_modal = False

    def __init__(self, text_dim: int = 512, clip_dim: int = 512) -> None:
        self.text_dim = text_dim
        self.clip_dim = clip_dim

    def _encode_tokens(self, texts: list[str], dim: int) -> np.ndarray:
        matrix = np.zeros((len(texts), dim), dtype=np.float32)
        for row, text in enumerate(texts):
            counts: dict[int, float] = {}
            for token in _tokenize(text):
                index = _bucket(token, dim)
                counts[index] = counts.get(index, 0.0) + 1.0
            for index, value in counts.items():
                matrix[row, index] = value
        return _l2_normalize(matrix)

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        return self._encode_tokens(texts, self.text_dim)

    def encode_clip_texts(self, texts: list[str]) -> np.ndarray:
        return self._encode_tokens(texts, self.clip_dim)

    def encode_images(self, paths: list[str]) -> np.ndarray:
        """缩略图灰度直方图 -> 向量，对“同一张图/相似版式”有一定区分度。"""
        matrix = np.zeros((len(paths), self.clip_dim), dtype=np.float32)
        for row, path in enumerate(paths):
            try:
                with Image.open(path) as image:
                    thumb = image.convert("L").resize((12, 12))
                    pixels = np.asarray(thumb, dtype=np.float32).reshape(-1) / 255.0
            except Exception:
                continue
            for index, value in enumerate(pixels):
                bucket = (index * 7) % self.clip_dim
                matrix[row, bucket] += float(value)
        return _l2_normalize(matrix)
