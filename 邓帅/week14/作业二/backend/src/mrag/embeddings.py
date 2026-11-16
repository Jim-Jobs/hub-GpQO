"""embedding 模型单例。

文本检索向量：bge-small-zh-v1.5（sentence-transformers，512 维）
多模态向量：OFA-Sys/chinese-clip-vit-base-patch16（transformers 原生 ChineseCLIP，512 维双塔）

选型说明：参考项目用 jinaai/jina-clip-v2（1024 维），但其 trust_remote_code 自定义
代码依赖 transformers 4.x 的 clip_loss，与 mineru 4.2 拉入的 transformers 5.x 不兼容
（ImportError: cannot import name 'clip_loss'）。改用原生支持的 ChineseCLIP：
无需 remote code、中文图文对齐效果好，代价是维度 512 而非 1024。
"""

import base64
import io
import os
from functools import lru_cache

import torch

# 必须在导入 HF 相关库之前设置，走国内镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from sentence_transformers import SentenceTransformer  # noqa: E402
from transformers import AutoModel, AutoProcessor  # noqa: E402

BGE_MODEL = "BAAI/bge-small-zh-v1.5"
CLIP_MODEL = "OFA-Sys/chinese-clip-vit-base-patch16"
BGE_DIM = 512
CLIP_DIM = 512


@lru_cache
def get_bge() -> SentenceTransformer:
    try:
        return SentenceTransformer(BGE_MODEL)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"bge-small-zh-v1.5 加载失败（首次运行需联网下载 ~100MB，走 HF_ENDPOINT 镜像）: {e}"
        ) from e


@lru_cache
def get_clip() -> tuple[AutoModel, AutoProcessor]:
    try:
        model = AutoModel.from_pretrained(CLIP_MODEL)
        processor = AutoProcessor.from_pretrained(CLIP_MODEL)
        return model, processor
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"{CLIP_MODEL} 加载失败（首次运行需联网下载 ~400MB，走 HF_ENDPOINT 镜像）: {e}"
        ) from e


def _normalize(t) -> list[float]:
    # transformers 5.x 的 ChineseCLIP get_*_features 返回 ModelOutput，特征在 pooler_output
    if not torch.is_tensor(t):
        t = t.pooler_output
    return torch.nn.functional.normalize(t, dim=-1).squeeze(0).tolist()


def encode_text_bge(text: str) -> list[float]:
    """文本 -> 512 维 bge 向量（归一化，配 COSINE 检索）。"""
    return get_bge().encode(text, normalize_embeddings=True).tolist()


def encode_text_clip(text: str) -> list[float]:
    """文本 -> ChineseCLIP 文本塔向量（归一化）。"""
    model, processor = get_clip()
    inputs = processor(text=[text], return_tensors="pt", padding=True)
    with torch.no_grad():
        feats = model.get_text_features(**inputs)
    return _normalize(feats)


def _encode_pil(pil_image) -> list[float]:
    model, processor = get_clip()
    inputs = processor(images=pil_image, return_tensors="pt")
    with torch.no_grad():
        feats = model.get_image_features(**inputs)
    return _normalize(feats)


def encode_image_clip(image_path: str) -> list[float] | None:
    """图片路径 -> CLIP 图像塔向量；文件不存在/损坏返回 None（调用方置零向量）。

    修复参考项目 bug：参考代码 dirname 后缺分隔符直接拼接导致路径必错、
    图像向量全零；本函数要求调用方传入已校验存在的绝对路径。
    """
    from PIL import Image

    if not os.path.isfile(image_path):
        return None
    try:
        with Image.open(image_path) as im:
            return _encode_pil(im.convert("RGB"))
    except Exception:  # noqa: BLE001 - 损坏图片按无图处理
        return None


def encode_image_b64(b64: str) -> list[float] | None:
    """base64 图片（前端上传的用户图）-> CLIP 图像塔向量；解码失败返回 None。"""
    from PIL import Image

    try:
        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as im:
            return _encode_pil(im.convert("RGB"))
    except Exception:  # noqa: BLE001
        return None


def zero_clip_vector() -> list[float]:
    return [0.0] * CLIP_DIM
