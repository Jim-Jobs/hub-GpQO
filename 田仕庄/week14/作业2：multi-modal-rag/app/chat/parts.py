"""图文消息组装：占位符顺序、来源标签、图片预算。"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from app.config import get_settings
from app.chat.normalize import NormalizedImage
from app.schemas import SourceItem, UsedImage

MARKER = re.compile(r"!\[[^\]]*\]")
_MAX_CONTEXT = 4000

_image_cache: dict[str, str | None] = {}


def _processed_image_url(url: str) -> str | None:
    if url in _image_cache:
        return _image_cache[url]

    settings = get_settings()
    prefix = "/files/processed/"
    if not url.startswith(prefix):
        _image_cache[url] = None
        return None

    relative = url[len(prefix) :]
    path = Path(settings.processed_dir) / relative
    if not path.is_file():
        _image_cache[url] = None
        return None

    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix)
    if not mime:
        _image_cache[url] = None
        return None

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    data_url = f"data:{mime};base64,{encoded}"
    _image_cache[url] = data_url
    return data_url


def _image_part(data_url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url}}


def build_parts(
    question: str,
    user_images: list[NormalizedImage],
    sources: list[SourceItem],
    context: str,
    *,
    max_images: int,
) -> tuple[list[dict], list[UsedImage], int]:
    parts: list[dict] = []
    used: list[UsedImage] = []
    dropped = 0
    consumed = 0
    position = 0

    def add_user_image(image: NormalizedImage) -> None:
        nonlocal dropped
        if len(used) >= max_images:
            dropped += 1
            return
        label = f"图{len([item for item in used if item.source == 'user']) + 1}"
        parts.append({"type": "text", "text": f"\n[{label}｜用户上传]\n"})
        parts.append(_image_part(image.data_url))
        used.append(UsedImage(slot=len(used) + 1, label=label, source="user"))

    for match in MARKER.finditer(question):
        text = question[position : match.start()]
        if text:
            parts.append({"type": "text", "text": text})

        if consumed < len(user_images):
            add_user_image(user_images[consumed])
        else:
            parts.append({"type": "text", "text": match.group(0)})

        consumed += 1
        position = match.end()

    tail = question[position:]
    if tail:
        parts.append({"type": "text", "text": tail})

    while consumed < len(user_images):
        add_user_image(user_images[consumed])
        consumed += 1

    if context:
        parts.append(
            {
                "type": "text",
                "text": f"\n[检索资料]\n{context[:_MAX_CONTEXT]}",
            }
        )

    remaining = max_images - len(used)
    retrieved_index = 0
    for source in sources:
        for url in source.image_urls:
            if remaining <= 0:
                dropped += 1
                continue
            data_url = _processed_image_url(url)
            if not data_url:
                continue
            retrieved_index += 1
            label = f"检索图{retrieved_index}"
            page = f" 第{source.page}页" if source.page else ""
            parts.append(
                {
                    "type": "text",
                    "text": f"\n[{label}｜{source.file_name}{page}]\n",
                }
            )
            parts.append(_image_part(data_url))
            used.append(
                UsedImage(
                    slot=len(used) + 1,
                    label=label,
                    source="retrieved",
                    file_id=source.file_id,
                )
            )
            remaining -= 1

    return parts, used, dropped
