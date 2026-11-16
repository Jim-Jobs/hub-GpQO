"""图片 / 资源地址工具：把解析产物里的相对图片路径，改写成后端可访问的 URL。"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from ..config import settings

MD_IMAGE_LINK_RE = re.compile(r"(!\[[^\]]*\]\(\s*<?)([^)\s>]+)")

_REMOTE_PREFIXES = ("http://", "https://", "data:", "blob:")


def image_url(doc_id: str, rel_path: str) -> str:
    clean = rel_path.replace("\\", "/").lstrip("./")
    return f"{settings.public_base_url}/api/assets/{doc_id}/{quote(clean, safe='/')}"


def absolutize_images(markdown: str, doc_id: str) -> str:
    """把 `![](images/xx.png)` 改写成 `![](http://host/api/assets/<doc>/images/xx.png)`。"""

    def _replace(match: re.Match[str]) -> str:
        prefix, raw_path = match.group(1), match.group(2)
        if raw_path.startswith(_REMOTE_PREFIXES):
            return match.group(0)
        return f"{prefix}{image_url(doc_id, raw_path)}"

    return MD_IMAGE_LINK_RE.sub(_replace, markdown)


def resolve_asset(parsed_dir: str | None, rel_path: str) -> Path | None:
    """解析图片真实路径，并阻止目录穿越。"""
    if not parsed_dir:
        return None
    root = Path(parsed_dir).resolve()
    candidate = (root / rel_path.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None
