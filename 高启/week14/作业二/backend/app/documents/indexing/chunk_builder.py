"""按页生成有稳定来源信息的检索文本块。"""

import hashlib
import re

from backend.app.documents.domain.models import ChunkRecord, ParsedDocument


CHUNK_TARGET_CHARS = 800
CHUNK_OVERLAP_CHARS = 120
_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")
_SENTENCE_BOUNDARY = re.compile(
    r"[。！？!?；;](?:[\"”’')\]]*)|\.(?:[\"”’')\]]*)\s+"
)


def build_chunks(
    parsed: ParsedDocument,
    target_chars: int = CHUNK_TARGET_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[ChunkRecord]:
    """按页切分正文，优先在段落或句子后截断并保留页级图片 ID。"""
    if target_chars < 1 or not 0 <= overlap_chars < target_chars:
        raise ValueError("切块长度必须为正数，且重叠长度小于切块长度")

    chunks: list[ChunkRecord] = []
    for page in parsed.pages:
        page_text = page.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not page_text:
            continue
        for index, text in enumerate(_split_page(page_text, target_chars, overlap_chars)):
            digest = hashlib.sha256(
                f"{parsed.document_id}\x1f{page.page_number}\x1f{index}\x1f{text}".encode("utf-8")
            ).hexdigest()
            chunks.append(
                ChunkRecord(
                    chunk_id=digest,
                    document_id=parsed.document_id,
                    page_number=page.page_number,
                    chunk_index=index,
                    text=text,
                    asset_ids=list(page.asset_ids),
                )
            )
    return chunks


def _split_page(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    """在页内滑动切块；超长段落仍有固定上限，避免超过 Milvus 字段长度。"""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(start + target_chars, len(text))
        end = _preferred_boundary(text, start, hard_end) if hard_end < len(text) else hard_end
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        # ponytail: 页内字符滑窗足以保留可检索上下文；需要 token 感知时再引入分词器。
        start = max(start + 1, end - overlap_chars)
    return chunks


def _preferred_boundary(text: str, start: int, hard_end: int) -> int:
    """在目标窗口后半段优先选择段落边界，再选择句子边界。"""
    minimum_end = start + (hard_end - start) // 2
    window = text[start:hard_end]
    paragraph_ends = [match.end() for match in _PARAGRAPH_BOUNDARY.finditer(window)]
    if paragraph_ends and start + paragraph_ends[-1] >= minimum_end:
        return start + paragraph_ends[-1]
    sentence_ends = [match.end() for match in _SENTENCE_BOUNDARY.finditer(window)]
    if sentence_ends and start + sentence_ends[-1] >= minimum_end:
        return start + sentence_ends[-1]
    return hard_end
