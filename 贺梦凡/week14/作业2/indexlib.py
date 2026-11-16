"""把 MinerU 的输出切成带图片的页面片段，并按问题检索。"""

import json
import re
from pathlib import Path

STATUS_TEXT = {
    "pending": "待解析",
    "parsing": "解析中",
    "done": "解析完成",
    "failed": "解析失败",
}

_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, status)


def to_rel(path: Path, data_dir: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(data_dir.resolve()))
    except ValueError:
        return None


def _split_text(text: str, size: int = 1200) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def chunks_from_pages(pages: dict[int, dict], doc_id: str, filename: str) -> list[dict]:
    chunks = []
    for page in sorted(pages):
        bucket = pages[page]
        pieces = _split_text("\n".join(bucket["texts"]))
        images = bucket["images"]
        if not pieces and images:
            pieces = [f"第{page}页的图片"]
        for index, piece in enumerate(pieces):
            chunks.append(
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "page": page,
                    "text": piece,
                    "images": images if index == 0 else [],
                }
            )
    return chunks


def chunks_from_content_list(
    items: list[dict],
    base_dir: Path,
    data_dir: Path,
    doc_id: str,
    filename: str,
) -> list[dict]:
    pages: dict[int, dict] = {}
    for item in items:
        page = int(item.get("page_idx", 0)) + 1
        bucket = pages.setdefault(page, {"texts": [], "images": []})
        kind = item.get("type")
        if kind in {"text", "equation"} and item.get("text"):
            bucket["texts"].append(str(item["text"]))
        if kind == "table" and item.get("table_body"):
            bucket["texts"].append(str(item["table_body"]))
        for caption in item.get("image_caption") or []:
            if caption:
                bucket["texts"].append(str(caption))
        for caption in item.get("table_caption") or []:
            if caption:
                bucket["texts"].append(str(caption))
        img = item.get("img_path")
        if not img:
            continue
        path = Path(img)
        if not path.is_absolute():
            path = base_dir / path
        rel = to_rel(path, data_dir)
        if rel and path.is_file() and rel not in bucket["images"]:
            bucket["images"].append(rel)
    return chunks_from_pages(pages, doc_id, filename)


def chunks_from_markdown(
    markdown: str,
    md_path: Path,
    data_dir: Path,
    doc_id: str,
    filename: str,
) -> list[dict]:
    sections = re.split(r"\n(?=#{1,3} )", markdown)
    pages: dict[int, dict] = {}
    for index, section in enumerate(sections, start=1):
        bucket = {"texts": [], "images": []}
        text = _IMAGE_REF.sub("", section).strip()
        if text:
            bucket["texts"].append(text)
        for raw in _IMAGE_REF.findall(section):
            raw = raw.strip().strip("<>")
            path = Path(raw)
            if not path.is_absolute():
                path = (md_path.parent / path).resolve()
            rel = to_rel(path, data_dir)
            if rel and path.is_file() and rel not in bucket["images"]:
                bucket["images"].append(rel)
        if bucket["texts"] or bucket["images"]:
            pages[index] = bucket
    return chunks_from_pages(pages, doc_id, filename)


def load_content_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def tokens(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text.lower())
    found = set(re.findall(r"[a-z0-9]+", compact))
    han = re.sub(r"[^\u4e00-\u9fff]", "", compact)
    found.update(han[i : i + 2] for i in range(max(len(han) - 1, 0)))
    if len(han) == 1:
        found.add(han)
    return found


def retrieve(chunks: list[dict], query: str, limit: int = 4) -> list[dict]:
    query_tokens = tokens(query)
    if not query_tokens:
        return []
    wants_image = any(word in query for word in ("图", "表", "照片", "截图"))
    ranked = []
    for chunk in chunks:
        chunk_tokens = tokens(chunk.get("text") or "")
        if not chunk_tokens:
            overlap = 0.0
        else:
            overlap = len(query_tokens & chunk_tokens) / len(query_tokens)
        if wants_image and chunk.get("images"):
            overlap += 0.15
        if overlap > 0:
            ranked.append((overlap, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in ranked[:limit]]
