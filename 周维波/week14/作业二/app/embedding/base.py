"""向量化适配层：文本(bge) + 图文跨模态(CLIP)，可切换本地模型 / 哈希向量。"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class BaseEmbedder(ABC):
    """统一编码接口。

    - text_dim：文本向量维度（bge-small-zh-v1.5 = 512）
    - clip_dim：CLIP 向量维度（jina-clip-v2 truncate=1024，clip-vit-base = 512）
    - supports_cross_modal：是否支持“以文搜图 / 以图搜图”（哈希后端不支持）
    """

    name: str = "base"
    text_dim: int = 512
    clip_dim: int = 512
    supports_cross_modal: bool = True

    @abstractmethod
    def encode_texts(self, texts: list[str]) -> np.ndarray:
        """文本检索向量，形状 (n, text_dim)，已 L2 归一化。"""

    @abstractmethod
    def encode_clip_texts(self, texts: list[str]) -> np.ndarray:
        """CLIP 文本向量（用于以文搜图），形状 (n, clip_dim)。"""

    @abstractmethod
    def encode_images(self, paths: list[str]) -> np.ndarray:
        """CLIP 图像向量，形状 (n, clip_dim)；单张失败返回全零行。"""
