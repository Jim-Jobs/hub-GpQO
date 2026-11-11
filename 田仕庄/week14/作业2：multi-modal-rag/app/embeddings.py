"""向量编码：BGE 文本向量 + Chinese-CLIP 文本/图片向量。

mock 模式返回确定性的稀疏向量，便于无模型环境跑通链路。
真实模型用 transformers 直接加载，避免 sentence-transformers 的 datasets 依赖。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.config import get_settings
from app.ingest.chunker import Chunk

logger = logging.getLogger(__name__)

_bge_model = None
_clip_model = None


def _mock_vector(seed: str, dim: int) -> list[float]:
    vector = [0.0] * dim
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % dim
    vector[index] = 1.0
    return vector


def _load_bge():
    import torch
    from transformers import AutoModel, AutoTokenizer

    settings = get_settings()
    if not settings.bge_model_path:
        raise RuntimeError("BGE_MODEL_PATH is required when mock is disabled")
    tokenizer = AutoTokenizer.from_pretrained(settings.bge_model_path)
    model = AutoModel.from_pretrained(settings.bge_model_path)
    model.eval()
    logger.info("bge loaded: %s", settings.bge_model_path)
    return tokenizer, model, torch


def _load_clip():
    from transformers import ChineseCLIPModel, ChineseCLIPProcessor

    settings = get_settings()
    if not settings.clip_model_path:
        raise RuntimeError("CLIP_MODEL_PATH is required when mock is disabled")
    model = ChineseCLIPModel.from_pretrained(settings.clip_model_path)
    processor = ChineseCLIPProcessor.from_pretrained(
        settings.clip_model_path
    )
    model.eval()
    logger.info("chinese-clip loaded: %s", settings.clip_model_path)
    return processor, model


def _models():
    global _bge_model, _clip_model
    if _bge_model is None:
        _bge_model = _load_bge()
    if _clip_model is None:
        _clip_model = _load_clip()
    return _bge_model, _clip_model


def _check_dim(vector: list[float], expected: int, name: str) -> list[float]:
    if len(vector) != expected:
        raise RuntimeError(
            f"{name} dimension mismatch: got {len(vector)}, expected {expected}"
        )
    return vector


def _bge_encode(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    tokenizer, model, torch = _bge_model
    inputs = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        output = model(**inputs)
        vectors = output.last_hidden_state[:, 0, :]
        vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
    return vectors.tolist()


def _feature_tensor(output):
    """兼容 transformers 新版的 BaseModelOutputWithPooling。"""
    import torch

    if isinstance(output, torch.Tensor):
        return output
    for name in ("image_embeds", "text_embeds", "pooler_output"):
        value = getattr(output, name, None)
        if value is not None:
            return value
    raise RuntimeError(f"unsupported model output: {type(output)}")


def _clip_encode_texts(texts: list[str]) -> list[list[float]]:
    processor, model = _clip_model
    import torch

    inputs = processor(
        text=texts,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        vectors = _feature_tensor(model.get_text_features(**inputs))
        vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
    return vectors.tolist()


def _clip_encode_image(path: str) -> list[float]:
    processor, model = _clip_model
    import torch
    from PIL import Image

    with Image.open(path) as image:
        inputs = processor(
            images=image.convert("RGB"), return_tensors="pt"
        )
    with torch.no_grad():
        vector = _feature_tensor(model.get_image_features(**inputs))
        vector = torch.nn.functional.normalize(vector, p=2, dim=1)
    return vector[0].tolist()


def embed_texts(texts: list[str]) -> list[tuple[list[float], list[float]]]:
    settings = get_settings()
    if settings.mock_external:
        return [
            (
                _mock_vector(f"bge::{text}", settings.bge_dim),
                _mock_vector(f"clip::{text}", settings.clip_dim),
            )
            for text in texts
        ]

    _models()
    bge_vectors = _bge_encode(texts)
    clip_vectors = _clip_encode_texts(texts)
    return [
        (
            _check_dim(bge_vector, settings.bge_dim, "BGE"),
            _check_dim(clip_vector, settings.clip_dim, "CLIP"),
        )
        for bge_vector, clip_vector in zip(bge_vectors, clip_vectors)
    ]


def embed_image(path: str) -> list[float]:
    settings = get_settings()
    if settings.mock_external:
        return _mock_vector(f"image::{path}", settings.clip_dim)

    _models()
    vector = _clip_encode_image(path)
    return _check_dim(vector, settings.clip_dim, "CLIP image")


def embed_chunks(
    chunks: list[Chunk],
) -> list[tuple[list[float], list[float], list[float]]]:
    """返回 [(bge, clip_text, clip_image)]；无图 chunk 的 clip_image 为零向量。"""
    settings = get_settings()
    texts = [chunk.text for chunk in chunks]
    text_vectors = embed_texts(texts)

    image_cache: dict[str, list[float]] = {}
    results: list[tuple[list[float], list[float], list[float]]] = []
    for chunk, (bge_vector, clip_text) in zip(chunks, text_vectors):
        if chunk.image_path:
            if chunk.image_path not in image_cache:
                image_cache[chunk.image_path] = embed_image(chunk.image_path)
            clip_image = image_cache[chunk.image_path]
        else:
            clip_image = [0.0] * settings.clip_dim
        results.append((bge_vector, clip_text, clip_image))
    return results


def reset_models() -> None:
    """测试辅助：释放模型引用。"""
    global _bge_model, _clip_model
    _bge_model = None
    _clip_model = None
