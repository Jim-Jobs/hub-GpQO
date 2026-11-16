"""图片预处理：压缩 + base64 data URL。

对应文档：``docs/06-RAG与多模态设计.md`` §4.3。

发给 vLLM 前统一处理：

```
Pillow 打开 → 缩放到最长边 ≤1600px → 转 JPEG(quality=85) → base64
```

* 控制请求体大小（base64 比原始字节大约 33%）；
* 控制 visual token 数（与 vLLM ``--max_pixels`` 配合）；
* PNG 转 JPEG 大幅减小图表类图片的体积。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image


def compress_to_data_url(
    image_path: Path, *, max_edge: int = 1600, quality: int = 85
) -> str:
    """图片 → ``data:image/jpeg;base64,...``。

    带透明通道的图先铺白底（JPEG 不支持 alpha，否则透明区会变黑）。
    """
    with Image.open(image_path) as handle:
        image = handle.convert("RGBA") if handle.mode == "RGBA" else handle.convert("RGB")
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])
            image = background

        width, height = image.size
        longest = max(width, height)
        if longest > max_edge:
            scale = max_edge / longest
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def data_url_from_bytes(data: bytes, mime: str = "image/jpeg") -> str:
    """原始字节 → data URL（用户上传的 base64 图可能已经带前缀，用这个兜底）。"""
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def strip_data_url_prefix(data: str) -> str:
    """去掉 ``data:image/png;base64,`` 前缀，只留 base64 体。"""
    return data.split(",", 1)[1] if data.startswith("data:") and "," in data else data


def looks_like_image(raw: bytes) -> bool:
    """粗判字节流是不是图片（看魔数）。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if raw.startswith(b"\xff\xd8\xff"):  # JPEG
        return True
    if raw.startswith(b"GIF8"):
        return True
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return True
    return raw.startswith(b"BM")  # BMP


def decode_image_data(data: str, filename: str = "") -> bytes:
    """校验并解码用户上传的图片 base64。

    前端可能传来占位串（如 Swagger 默认的 ``"string"``）或非图片的 base64。
    带着坏数据去调外部 API 只会得到一个语焉不详的 400，
    不如在这里明确报"图片数据无效"。

    失败抛 :class:`ValueError`（调用方转成 400）。
    """
    import base64

    payload = strip_data_url_prefix(data)
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception as exc:  # noqa: BLE001 —— 任何解码失败都是"无效图片"
        raise ValueError(f"图片数据不是合法的 base64（{filename or '未命名'}）") from exc
    if not raw:
        raise ValueError(f"图片内容为空（{filename or '未命名'}）")
    if not looks_like_image(raw):
        raise ValueError(f"图片内容不是有效的图片格式（{filename or '未命名'}）")
    return raw
