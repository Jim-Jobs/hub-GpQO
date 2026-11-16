"""读取 MinerU default ZIP 解压后的 Markdown 和结构化页面。"""

import json
from pathlib import Path
from typing import Any

from backend.app.documents.domain.models import ImageAsset, ParsedDocument, ParsedPage


class MineruOutputError(ValueError):
    """MinerU 结果缺失、格式错误或没有可用页面内容。"""


def read_result(
    document_id: str, output_dir: Path, assets: list[ImageAsset]
) -> ParsedDocument:
    """从 default ZIP 的标准文件读取页码、文本、图片 ID 和 Markdown。"""
    structured_path = _find_unique_file(output_dir, "structured_content.json")
    markdown_path = _find_unique_file(output_dir, "markdown.md", required=False)
    try:
        structured = json.loads(structured_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MineruOutputError("MinerU structured_content.json 无法读取") from error
    if not isinstance(structured, dict) or not isinstance(structured.get("pages"), list):
        raise MineruOutputError("MinerU 结构化结果缺少 pages 数组")

    assets_by_page: dict[int, list[str]] = {}
    for asset in assets:
        if asset.page_number is not None:
            assets_by_page.setdefault(asset.page_number, []).append(asset.asset_id)

    pages: list[ParsedPage] = []
    for item in structured["pages"]:
        if not isinstance(item, dict) or not isinstance(item.get("page_idx"), int):
            continue
        page_number = item["page_idx"] + 1
        blocks = item.get("blocks", [])
        if not isinstance(blocks, list):
            raise MineruOutputError(f"MinerU 第 {page_number} 页 blocks 不是数组")
        text = "\n".join(filter(None, (_block_text(block) for block in blocks)))
        pages.append(ParsedPage(page_number, text, assets_by_page.get(page_number, [])))

    if not pages or (not any(page.text.strip() for page in pages) and not assets):
        raise MineruOutputError("MinerU 解析结果为空")
    markdown = markdown_path.read_text(encoding="utf-8") if markdown_path else ""
    return ParsedDocument(document_id, markdown, pages, assets)


def _find_unique_file(output_dir: Path, file_name: str, *, required: bool = True) -> Path | None:
    """按标准文件名查找 ZIP 成员，拒绝多个候选造成的静默误读。"""
    matches = sorted(output_dir.rglob(file_name))
    if len(matches) > 1:
        raise MineruOutputError(f"MinerU 结果中存在多个 {file_name}")
    if not matches:
        if required:
            raise MineruOutputError(f"MinerU 结果缺少 {file_name}")
        return None
    return matches[0]


def _block_text(value: Any) -> str:
    """递归提取结构化块中的自然语言文本，忽略几何和解析元数据。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(filter(None, (_block_text(item) for item in value)))
    if isinstance(value, dict):
        parts = [
            _block_text(value[key])
            for key in ("content", "text", "latex", "html")
            if key in value
        ]
        return "".join(filter(None, parts))
    return ""
