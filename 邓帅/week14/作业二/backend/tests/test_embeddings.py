"""embedding 单测：维度与归一化（需模型已缓存，本地标记不跳过——作业环境即部署环境）。

网络不可用且模型未缓存时这些用例会失败：按 design 属环境准备问题（任务 1.4）。
"""

import math

import pytest

from mrag.embeddings import (
    BGE_DIM,
    CLIP_DIM,
    encode_image_b64,
    encode_text_bge,
    encode_text_clip,
    zero_clip_vector,
)

pytestmark = pytest.mark.slow


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def test_bge_text_dim_and_normalized():
    v = encode_text_bge("这是一段测试文本")
    assert len(v) == BGE_DIM == 512
    assert abs(_norm(v) - 1.0) < 1e-3


def test_clip_text_dim_and_normalized():
    v = encode_text_clip("季度营收趋势图表")
    assert len(v) == CLIP_DIM == 512
    assert abs(_norm(v) - 1.0) < 1e-3


def test_clip_image_b64_roundtrip():
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    v = encode_image_b64(b64)
    assert v is not None
    assert len(v) == CLIP_DIM
    assert any(abs(x) > 1e-6 for x in v)  # 真实向量（参考项目的零向量 bug 在此把关）


def test_clip_image_b64_invalid_returns_none():
    assert encode_image_b64("不是合法的base64图片!!!") is None


def test_zero_vector():
    z = zero_clip_vector()
    assert len(z) == CLIP_DIM and all(x == 0.0 for x in z)


@pytest.mark.parametrize("fn,text", [(encode_text_bge, "bge"), (encode_text_clip, "clip")])
def test_deterministic(fn, text):
    a = fn("确定性检查" if text == "clip" else "确定性检查")
    b = fn("确定性检查")
    assert a == b
