"""嵌入客户端 —— 三后端 + 图片路降级。

对应文档：``docs/06-RAG与多模态设计.md`` §2.2。

| 后端 | 文本 | 图片 |
|---|---|---|
| ``openai`` | ✅ ``/v1/embeddings`` | ⚠️ **降级**：用 caption 文本的向量顶替 |
| ``local``  | ✅ 本地模型 | ✅ **同模型同空间**（真多模态召回） |
| ``mock``   | 确定性伪向量 | 确定性伪向量 |

> ⚠️ 默认 ``openai`` 下，第三路「图片向量召回」实为 caption 文本召回，与 BM25 高度重叠，
> 收益有限。要拿到完整多模态召回收益必须切到 ``local``（Qwen3-VL-Embedding-0.6B）。
> 评测报告中必须如实说明这一点。
"""

from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingClient(Protocol):
    """嵌入接口。"""

    @property
    def supports_image_embedding(self) -> bool:
        """是否能把图片直接编码成向量（``local`` 后端为真）。"""
        ...

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量列表，与输入一一对应。"""
        ...

    async def embed_image(self, image_path: Path) -> list[float]:
        """图片 → 向量。仅 ``local`` 后端支持。"""
        ...

    async def health(self) -> bool:
        """连通性自检。"""
        ...


def _pseudo_vector(seed: str, dim: int) -> list[float]:
    """确定性伪向量：同样的输入永远得到同样的向量，便于离线开发与单测。"""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    raw = (digest * (dim // len(digest) + 1))[:dim]
    vector = [byte / 255.0 for byte in raw]
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class MockEmbeddingClient:
    """桩实现：哈希 → 确定性伪向量。无网络、无模型。"""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    @property
    def supports_image_embedding(self) -> bool:
        return True

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_pseudo_vector(text, self._dim) for text in texts]

    async def embed_image(self, image_path: Path) -> list[float]:
        return _pseudo_vector(f"image:{image_path.name}", self._dim)

    async def health(self) -> bool:
        return True


class OpenAIEmbeddingClient:
    """调用任意 OpenAI 兼容的 ``/v1/embeddings`` 服务。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dim = settings.embedding_dim
        self._client = httpx.AsyncClient(
            base_url=settings.embedding_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
            timeout=60.0,
        )

    @property
    def supports_image_embedding(self) -> bool:
        # 文本 embedding 服务不认识图片 —— 图片路必须降级为 caption 文本
        return False

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings", json={"model": self._settings.embedding_model, "input": texts}
        )
        response.raise_for_status()
        payload = response.json()
        # 按 index 排序，保证与输入顺序一致
        data = sorted(payload["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in data]

    async def embed_image(self, image_path: Path) -> list[float]:
        raise NotImplementedError(
            "openai 嵌入后端不支持图片编码；图片路请改用 caption 文本向量，"
            "或把 EMBEDDING_BACKEND 切到 local"
        )

    async def health(self) -> bool:
        try:
            response = await self._client.get("/models", timeout=5.0)
            return response.status_code < 500
        except Exception:  # noqa: BLE001 —— 健康检查不应因任何异常抛出
            logger.warning("embedding 服务探测失败", exc_info=True)
            return False

    async def close(self) -> None:
        await self._client.aclose()


class LocalEmbeddingClient:
    """本地 ``sentence-transformers`` 加载嵌入模型。

    这是唯一能拿到**真实图文统一空间**的后端 —— 但前提是模型本身带视觉塔
    （``Qwen3-VL-Embedding-0.6B``）。

    ⚠️ **纯文本模型不能编码图片**。实测（2026-11-12）本机准备的是
    ``Qwen3-Embedding-0.6B``（``model_type=qwen3``，**无** ``vision_config``），
    它只能编码文本。因此这里在**运行时检测**模型有没有视觉塔：没有就
    :attr:`supports_image_embedding` 返回 False，调用方自动降级为 caption 文本向量
    —— 而不是假装支持图片编码。

    要拿完整的第三路召回收益，请改用 ``Qwen3-VL-Embedding-0.6B``。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dim = settings.embedding_dim
        self._model = None
        self._has_vision: bool | None = None

    @property
    def supports_image_embedding(self) -> bool:
        """模型加载后才知道有没有视觉塔；没加载时先按"不支持"处理（安全降级）。"""
        return bool(self._has_vision)

    def _ensure_model(self):  # noqa: ANN202 —— 返回第三方类型，暂不标注
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover —— 依赖缺失路径
                raise RuntimeError(
                    "local 嵌入后端需要额外安装：pip install -e '.[local]'"
                ) from exc
            self._model = SentenceTransformer(
                self._settings.embedding_local_model_path, trust_remote_code=True
            )
            self._has_vision = self._detect_vision(self._model)
            logger.info(
                "已加载本地嵌入模型 %s（视觉塔：%s）",
                self._settings.embedding_local_model_path,
                "有" if self._has_vision else "无 → 图片向量降级为 caption 文本向量",
            )
        return self._model

    @staticmethod
    def _detect_vision(model: Any) -> bool:
        """检测模型是否带视觉塔。"""
        config = getattr(model, "config", None)
        if config is None:
            return False
        # sentence-transformers 会把子模块的配置收进 ``config`` 里
        for attr in ("vision_config", "vision_tower"):
            if getattr(config, attr, None) is not None:
                return True
        # 兜底：看模块清单里有没有视觉相关的模块
        modules = getattr(model, "modules", None) or []
        return any("vision" in type(module).__name__.lower() for module in modules)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import asyncio

        model = self._ensure_model()
        vectors = await asyncio.to_thread(
            model.encode, texts, normalize_embeddings=True
        )
        return [list(map(float, vector)) for vector in vectors]

    async def embed_image(self, image_path: Path) -> list[float]:
        import asyncio

        from PIL import Image

        model = self._ensure_model()
        if not self._has_vision:
            raise NotImplementedError(
                "当前嵌入模型没有视觉塔（纯文本模型），无法编码图片。"
                "请改用 Qwen3-VL-Embedding-0.6B，或让调用方降级为 caption 文本向量"
            )

        def _encode() -> list[float]:
            with Image.open(image_path) as image:
                vector = model.encode([image], normalize_embeddings=True)[0]
            return list(map(float, vector))

        return await asyncio.to_thread(_encode)

    async def health(self) -> bool:
        try:
            self._ensure_model()
            return True
        except Exception:  # noqa: BLE001 —— 健康检查不应因任何异常抛出
            logger.warning("本地嵌入模型加载失败", exc_info=True)
            return False


def build_embedding_client(settings: Settings) -> EmbeddingClient:
    """按 ``EMBEDDING_BACKEND`` 选择实现。"""
    backend = settings.embedding_backend.lower()
    if backend == "mock":
        return MockEmbeddingClient(dim=settings.embedding_dim)
    if backend == "local":
        return LocalEmbeddingClient(settings)
    if backend == "openai":
        return OpenAIEmbeddingClient(settings)
    raise ValueError(
        f"未知的 EMBEDDING_BACKEND: {settings.embedding_backend}（可选 openai|local|mock）"
    )
