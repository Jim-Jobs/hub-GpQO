"""Markdown 切片：按块聚合，保留图片引用与页码，供“文本检索 + 图像检索”共用。"""

from __future__ import annotations

import re
from dataclasses import dataclass

IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)(?:\s+\"[^\"]*\")?\)")
PAGE_MARKER_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")
SENTENCE_END_RE = re.compile(r"[。！？；!?;\n]")


@dataclass
class ChunkDraft:
    seq: int
    text: str
    page_no: int | None
    image_rel: str | None


def find_first_image(text: str) -> str | None:
    """取切片里的第一张图（用于生成图像向量，与参考实现一致）。"""
    match = IMAGE_RE.search(text)
    if not match:
        return None
    path = match.group(1).strip()
    if path.startswith("http://") or path.startswith("https://") or path.startswith("data:"):
        return None
    path = path.lstrip("./")
    marker = path.find("images/")
    if marker > 0:
        path = path[marker:]
    return path.replace("\\", "/")


def text_without_images(text: str) -> str:
    """文本向量不掺入图片 markdown（图片用 CLIP 单独编码）。"""
    cleaned = IMAGE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _split_pages(markdown: str) -> list[tuple[int | None, str]]:
    segments: list[tuple[int | None, str]] = []
    current_page: int | None = None
    cursor = 0
    for match in PAGE_MARKER_RE.finditer(markdown):
        segments.append((current_page, markdown[cursor : match.start()]))
        current_page = int(match.group(1))
        cursor = match.end()
    segments.append((current_page, markdown[cursor:]))
    return [(page, text) for page, text in segments if text.strip()]


def _split_blocks(segment: str) -> list[str]:
    """按空行切块，代码块内部的空行不切。"""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in segment.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if not in_fence and stripped == "":
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [text[i : i + max_chars].strip() for i in range(0, len(text), max_chars)]


def _split_long_block(block: str, max_chars: int) -> list[str]:
    """超长块按句子边界二次切分；单句仍然超长时按长度硬切。"""
    sentences = [part for part in re.split(r"(?<=[。！？；!?;\n])", block) if part]
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if buffer.strip():
                pieces.append(buffer.strip())
                buffer = ""
            pieces.extend(_hard_split(sentence, max_chars))
            continue
        if len(buffer) + len(sentence) > max_chars:
            pieces.append(buffer.strip())
            buffer = ""
        buffer += sentence
    if buffer.strip():
        pieces.append(buffer.strip())
    return [piece for piece in pieces if piece]


def split_markdown(markdown: str, max_chars: int = 800) -> list[ChunkDraft]:
    """把整篇 markdown 切成若干带页码、带图片引用的切片。"""
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[tuple[int | None, str]] = []
    for page_no, segment in _split_pages(markdown):
        for block in _split_blocks(segment):
            blocks.append((page_no, block))

    drafts: list[ChunkDraft] = []
    buffer: list[str] = []
    buffer_len = 0
    buffer_page: int | None = None

    def flush() -> None:
        nonlocal buffer, buffer_len, buffer_page
        text = "\n\n".join(buffer).strip()
        if text:
            drafts.append(
                ChunkDraft(
                    seq=len(drafts),
                    text=text,
                    page_no=buffer_page,
                    image_rel=find_first_image(text),
                )
            )
        buffer = []
        buffer_len = 0
        buffer_page = None

    for page_no, block in blocks:
        block = block.strip()
        if not block:
            continue

        if len(block) > max_chars:
            flush()
            for piece in _split_long_block(block, max_chars):
                drafts.append(
                    ChunkDraft(
                        seq=len(drafts),
                        text=piece,
                        page_no=page_no,
                        image_rel=find_first_image(piece),
                    )
                )
            continue

        # 跨页不合并：保证切片页码可精确引用
        if buffer and buffer_page is not None and page_no is not None and page_no != buffer_page:
            flush()
        if buffer and buffer_len + len(block) + 2 > max_chars:
            flush()
        if not buffer:
            buffer_page = page_no
        buffer.append(block)
        buffer_len += len(block) + 2

    flush()

    # 末尾过短的碎片合并进上一块，避免出现“孤立一行”的切片
    if len(drafts) >= 2:
        tail = drafts[-1]
        prev = drafts[-2]
        if (
            len(tail.text) < max(80, max_chars // 6)
            and prev.page_no == tail.page_no
            and len(prev.text) + len(tail.text) <= max_chars * 1.2
        ):
            merged_text = f"{prev.text}\n\n{tail.text}"
            drafts[-2] = ChunkDraft(
                seq=prev.seq,
                text=merged_text,
                page_no=prev.page_no,
                image_rel=prev.image_rel or tail.image_rel,
            )
            drafts.pop()

    for index, draft in enumerate(drafts):
        draft.seq = index
    return drafts
