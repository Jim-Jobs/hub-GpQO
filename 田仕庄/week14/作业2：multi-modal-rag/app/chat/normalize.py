"""提问图片归一化：校验 → EXIF 转正 → RGB → 长边 1280 → JPEG。"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from app.config import get_settings
from app.errors import (
    IMAGE_INVALID,
    IMAGE_TOO_LARGE,
    TOO_MANY_IMAGES,
    AppError,
)

MAX_SIDE = 1280
JPEG_QUALITY = 85
_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}

# 防解压炸弹：超大像素图直接拒绝
Image.MAX_IMAGE_PIXELS = 40_000_000


@dataclass
class NormalizedImage:
    slot: int
    path: Path
    data_url: str
    width: int
    height: int


def _normalize_one(
    raw: bytes, destination: Path, slot: int
) -> NormalizedImage:
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            probe.verify()
    except Exception as exc:
        raise AppError(IMAGE_INVALID, status_code=400) from exc

    if fmt not in _ALLOWED_FORMATS:
        raise AppError(
            IMAGE_INVALID,
            f"仅支持 PNG/JPEG/WEBP，收到 {fmt or '未知'}",
            400,
        )

    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(
                destination,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
            )
            width, height = image.size
    except Exception as exc:
        raise AppError(IMAGE_INVALID, status_code=400) from exc

    payload = destination.read_bytes()
    encoded = base64.b64encode(payload).decode("ascii")
    return NormalizedImage(
        slot=slot,
        path=destination,
        data_url=f"data:image/jpeg;base64,{encoded}",
        width=width,
        height=height,
    )


def normalize_images(
    images: list[tuple[str, bytes]],
    trace_id: str,
) -> list[NormalizedImage]:
    settings = get_settings()
    if len(images) > settings.max_question_images:
        raise AppError(
            TOO_MANY_IMAGES,
            f"最多上传 {settings.max_question_images} 张图片",
            400,
        )

    output_dir = settings.questions_dir / trace_id
    normalized: list[NormalizedImage] = []
    for index, (_filename, raw) in enumerate(images, start=1):
        if len(raw) > settings.max_question_image_bytes:
            raise AppError(
                IMAGE_TOO_LARGE,
                f"单张图片不能超过 "
                f"{settings.max_question_image_bytes // (1024 * 1024)}MB",
                400,
            )
        normalized.append(
            _normalize_one(raw, output_dir / f"img{index}.jpg", index)
        )
    return normalized


def cleanup_question_images(trace_id: str) -> None:
    """预留：按 trace_id 清理提问图片。"""
    settings = get_settings()
    directory = settings.questions_dir / trace_id
    if directory.is_dir():
        for item in directory.glob("*"):
            try:
                item.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
