"""将 MinerU 导出图片映射为受控的文档图片资源。"""

import hashlib
import mimetypes
import re
from pathlib import Path

from backend.app.documents.domain.models import ImageAsset


PAGE_INDEX_PATTERN = re.compile(r"page_(\d+)_")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ASSET_ID_PATTERN = re.compile(r"[0-9a-f]{24}")


def register_assets(document_id: str, output_dir: Path) -> list[ImageAsset]:
    """扫描图片目录并生成稳定 ID；返回路径均相对当前文档目录。"""
    document_root = output_dir.resolve()
    image_root = document_root / "images"
    assets: list[ImageAsset] = []
    seen_ids: set[str] = set()
    if not image_root.is_dir():
        return assets

    for image_path in sorted(image_root.rglob("*")):
        if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        resolved_path = image_path.resolve()
        if not resolved_path.is_relative_to(document_root):
            raise ValueError("MinerU 图片路径超出文档解析目录")
        relative_path = resolved_path.relative_to(document_root).as_posix()
        digest = hashlib.sha256(f"{document_id}:{relative_path}".encode("utf-8")).hexdigest()
        asset_id = digest[:24]
        if asset_id in seen_ids:
            raise ValueError("MinerU 图片资源 ID 重复")
        seen_ids.add(asset_id)
        page_number = _page_number(image_path.name)
        assets.append(ImageAsset(asset_id, document_id, relative_path, page_number))
    return assets


def load_asset_bytes(
    parsed_root: Path, document_id: str, asset_id: str
) -> tuple[bytes, str] | None:
    """按受控 document_id/asset_id 读取已登记图片，拒绝越界或未知资源。"""
    if (
        not document_id
        or document_id in {".", ".."}
        or "/" in document_id
        or "\\" in document_id
        or not ASSET_ID_PATTERN.fullmatch(asset_id)
    ):
        return None
    root = parsed_root.resolve()
    document_dir = (root / document_id).resolve()
    if document_dir.parent != root:
        return None
    asset = next(
        (item for item in register_assets(document_id, document_dir) if item.asset_id == asset_id),
        None,
    )
    if asset is None:
        return None
    image_path = (document_dir / Path(*asset.relative_path.split("/"))).resolve()
    if not image_path.is_relative_to(document_dir):
        return None
    media_type, _ = mimetypes.guess_type(image_path.name)
    if not media_type or not media_type.startswith("image/"):
        return None
    try:
        return image_path.read_bytes(), media_type
    except OSError:
        return None


def _page_number(file_name: str) -> int | None:
    """MinerU ZIP 文件名使用从零开始的 page_idx，这里转换成用户页码。"""
    match = PAGE_INDEX_PATTERN.search(file_name)
    return int(match.group(1)) + 1 if match else None
