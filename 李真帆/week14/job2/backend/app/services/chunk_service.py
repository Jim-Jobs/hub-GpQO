import logging
import re
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
IMG_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$")
IMG_URL_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


@dataclass
class Chunk:
    chunk_index: int
    chunk_type: str  # text | table | image
    heading: str
    content: str
    image_path: str
    embed_text: str


def build_chunks(md_text: str) -> list[Chunk]:
    """按 markdown 结构切片：文本按标题聚合到目标长度（带重叠），表格独立，图片独立（用上下文文本做向量）。"""
    blocks = _split_blocks(md_text)
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_heading = ""
    buf_len = 0
    overlap = ""

    def flush():
        nonlocal buf, buf_len, overlap
        content = "\n\n".join(buf).strip()
        if content:
            chunks.append(Chunk(
                chunk_index=0, chunk_type="text", heading=buf_heading,
                content=content, image_path="",
                embed_text=f"{buf_heading}\n{overlap}{content}".strip(),
            ))
            overlap = content[-settings.chunk_overlap:] if len(content) > settings.chunk_overlap else ""
        else:
            overlap = ""
        buf, buf_len = [], 0

    for idx, (btype, heading, text) in enumerate(blocks):
        if btype == "heading":
            continue
        if btype == "image":
            flush()
            overlap = ""
            chunks.append(Chunk(
                chunk_index=0, chunk_type="image", heading=heading,
                content=text, image_path=_first_image_url(text),
                embed_text=f"{heading}\n{_image_context(blocks, idx)}".strip(),
            ))
            continue
        if btype == "table":
            flush()
            overlap = ""
            chunks.append(Chunk(
                chunk_index=0, chunk_type="table", heading=heading,
                content=text, image_path="",
                embed_text=f"{heading}\n{text}".strip(),
            ))
            continue
        if buf and buf_heading != heading:
            flush()
        if not buf:
            buf_heading = heading
        buf.append(text)
        buf_len += len(text)
        if buf_len >= settings.chunk_size:
            flush()
    flush()

    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _split_blocks(md_text: str) -> list[tuple[str, str, str]]:
    """切为 (type, heading, text) 块序列，type ∈ heading|text|table|image|code。"""
    lines = md_text.splitlines()
    blocks: list[tuple[str, str, str]] = []
    heading = ""
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        m = HEADING_RE.match(ln)
        if m:
            heading = m.group(2).strip()
            blocks.append(("heading", heading, ln))
            i += 1
            continue
        if not ln.strip():
            i += 1
            continue
        if ln.strip().startswith("```"):
            j = i + 1
            while j < n and not lines[j].strip().startswith("```"):
                j += 1
            blocks.append(("text", heading, "\n".join(lines[i : j + 1])))
            i = j + 1
            continue
        if IMG_ONLY_RE.match(ln):
            blocks.append(("image", heading, ln.strip()))
            i += 1
            continue
        if ln.lstrip().startswith("|"):
            j = i
            while j < n and lines[j].lstrip().startswith("|"):
                j += 1
            table = "\n".join(lines[i:j]).strip()
            rows = table.splitlines()
            is_table = len(rows) >= 2 and TABLE_SEP_RE.match(rows[1]) is not None
            blocks.append(("table" if is_table else "text", heading, table))
            i = j
            continue
        j = i
        buf: list[str] = []
        while j < n:
            cur = lines[j]
            if (
                not cur.strip()
                or HEADING_RE.match(cur)
                or cur.strip().startswith("```")
                or cur.lstrip().startswith("|")
                or IMG_ONLY_RE.match(cur)
            ):
                break
            buf.append(cur)
            j += 1
        blocks.append(("text", heading, "\n".join(buf).strip()))
        i = j
    return blocks


def _first_image_url(md: str) -> str:
    m = IMG_URL_RE.search(md)
    return m.group(1) if m else ""


def _image_context(blocks: list[tuple[str, str, str]], idx: int, limit: int = 200) -> str:
    parts = []
    for j in range(max(0, idx - 2), min(len(blocks), idx + 3)):
        if blocks[j][0] in ("text", "table"):
            parts.append(blocks[j][2][:limit])
    return " ".join(parts)[: limit * 2]
