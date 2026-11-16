"""图片落盘：把 MinerU 解压出来的图片转存到 ``data/images/{doc_id}/``。

对应文档：``docs/02-数据模型.md`` §3.3「落盘与 ID」。

```
图片文件：/data/images/{doc_id}/{image_id}.png   （统一转 PNG）
image_id：{doc_id}_p{page_no}_i{idx}             （idx 为该页内序号，从 1 开始）
path：    {doc_id}/{image_id}.png                （相对 image_dir）
url：     /static/images/{doc_id}/{image_id}.png （对外可访问路径）
```
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.pipeline.chunking import ParsedImage
from app.schemas.index import image_id_for

logger = logging.getLogger(__name__)


@dataclass
class StoredImage:
    """已落盘的图片。"""

    image_id: str
    page_no: int
    idx: int
    path: str
    url: str
    width: int | None = None
    height: int | None = None
    bbox: list[float] | None = None
    caption: str = ""
    source_path: str = ""
    """MinerU 产物里的原始相对路径 —— 用于把落盘结果映射回 chunk。"""


def _relative_path(doc_id: str, image_id: str) -> str:
    return f"{doc_id}/{image_id}.png"


def _public_url(doc_id: str, image_id: str) -> str:
    return f"/static/images/{doc_id}/{image_id}.png"


def store_images(
    images: list[ParsedImage],
    *,
    doc_id: str,
    work_dir: Path,
    image_root: Path,
) -> list[StoredImage]:
    """把图片逐张转成 PNG 落到 ``image_root/{doc_id}/``。

    以 ``(page_no, 页内序号)`` 生成 ``image_id``。源文件缺失时跳过并记 warn ——
    单张图失败不应让整份文档解析失败。
    """
    doc_dir = image_root / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    # 按页分组编号：idx 是"该页内第几张"，从 1 开始
    per_page_counter: dict[int, int] = {}
    stored: list[StoredImage] = []

    for image in images:
        source = (work_dir / image.img_path).resolve()
        if not source.is_file():
            logger.warning("图片文件不存在，跳过: %s", image.img_path)
            continue

        per_page_counter[image.page_no] = per_page_counter.get(image.page_no, 0) + 1
        idx = per_page_counter[image.page_no]
        image_id = image_id_for(doc_id, image.page_no, idx)
        target = doc_dir / f"{image_id}.png"

        try:
            with Image.open(source) as handle:
                width, height = handle.size
                # 统一转 PNG（表格截图多为 JPEG，转 PNG 便于后续无损处理）
                rgb = handle.convert("RGB") if handle.mode not in ("RGB", "RGBA") else handle
                rgb.save(target, format="PNG")
        except Exception:  # noqa: BLE001 —— 单张图片坏了不应中断整份文档
            logger.warning("图片转换失败，跳过: %s", image.img_path, exc_info=True)
            continue

        stored.append(
            StoredImage(
                image_id=image_id,
                page_no=image.page_no,
                idx=idx,
                path=_relative_path(doc_id, image_id),
                url=_public_url(doc_id, image_id),
                width=width,
                height=height,
                bbox=image.bbox,
                caption=image.caption,
                source_path=image.img_path,
            )
        )
    return stored


def delete_doc_images(doc_id: str, image_root: Path) -> None:
    """删除某文档的全部图片（重解析前调用）。"""
    import shutil

    doc_dir = image_root / doc_id
    if doc_dir.is_dir():
        shutil.rmtree(doc_dir, ignore_errors=True)
