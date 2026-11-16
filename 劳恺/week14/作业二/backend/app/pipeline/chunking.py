"""``content_list`` → chunk + 绑图。

对应文档：``docs/02-数据模型.md`` §3。

**实测校准（2026-11-12，MinerU v4 精准解析 / ``model_version=vlm``，两个真实 PDF）**

官方文档没有公开 ``content_list`` 的字段级定义，以下是实测得到的口径，与 ``docs/02`` 的
假设有出入，**代码以实测为准**：

========================  ==========================================
实测观察                         对本模块的影响
========================  ==========================================
``type`` 实际取值          ``text`` / ``table`` / ``footer`` /
                            ``page_number``；另有 ``title``、
                            ``paragraph``（v2 格式）
没有独立的 ``image`` 块    图片以 ``table.img_path`` 的形式出现；
                            仍保留对 ``type=image`` 的容错分支
没有 ``equation`` 块       仍保留公式分支（``$...$`` 独立成块）
没有 ``text_format`` 字段  标题靠 ``text_level is not None`` 识别
``table_caption`` 是 list  必须 join，不能直接当字符串用
``table_body`` 是 HTML     转 Markdown，见 :mod:`app.pipeline.table_md`
========================  ==========================================

**绑定规则**（``docs/02`` §3.3）：图片不单独成块，绑定到相邻的文字块 —— 优先向前找
同页或前一页的块，其次向后找；都没有则丢弃并计入 stats。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.table_md import html_to_markdown_table

logger = logging.getLogger(__name__)

#: 有实际检索价值的块类型
TEXT_KINDS = frozenset({"text", "title", "paragraph"})
TABLE_KINDS = frozenset({"table"})
EQUATION_KINDS = frozenset({"equation", "interline_equation"})
IMAGE_KINDS = frozenset({"image", "figure"})
#: 纯噪声块：页脚、页码。既不入库也不参与切块
NOISE_KINDS = frozenset({"footer", "page_footer", "page_number", "header", "page_header"})

_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:[.\-/][A-Za-z0-9_]+)*")

#: 中文 1 字 ≈ 1.3 token（``docs/06`` §5.4：600 token ≈ 400~500 字）
CJK_TOKEN_WEIGHT = 1.3
#: 英文/数字按 4 字符 ≈ 1 token 估算
ASCII_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。

    没有加载 Qwen 分词器（会拖慢 Worker 启动、且切块目标值本来就是经验值），
    因此用可解释的启发式：CJK 字按 1.3 计，英文/数字按 4 字符 1 token。
    真实 token 数以模型为准；这里的值只用于控制块大小。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    ascii_chars = sum(len(match) for match in _ASCII_WORD_RE.findall(text))
    return int(cjk * CJK_TOKEN_WEIGHT + ascii_chars / ASCII_CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# 内部模型
# ---------------------------------------------------------------------------


@dataclass
class ParsedImage:
    """``content_list`` 中发现的图片（尚未落盘、尚未分配 image_id）。"""

    img_path: str
    """相对 ``work_dir`` 的路径，如 ``images/abc.jpg``。"""
    page_no: int
    """1 基页码。"""
    bbox: list[float] | None = None
    caption: str = ""
    #: 图片所属的**归一化块**下标；``-1`` 表示独立图片块（无宿主块，只能按页码就近绑定）
    block_index: int = -1


@dataclass
class Block:
    """归一化后的版面块。"""

    kind: str
    text: str
    page_no: int
    bbox: list[float] | None = None
    level: int | None = None
    """标题层级；非 ``None`` 表示这是标题。"""
    images: list[ParsedImage] = field(default_factory=list)


@dataclass
class ChunkDraft:
    """切块结果（尚未分配 ``chunk_id``、尚未向量化）。"""

    seq: int
    text: str
    page_start: int
    page_end: int
    block_types: list[str] = field(default_factory=list)
    bboxes: list[list[float]] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)
    block_indices: list[int] = field(default_factory=list)
    """本块覆盖的原始块下标 —— 图片据此精确归属到"它所在的那个 chunk"。"""


@dataclass
class NormalizedContent:
    """归一化结果：可参与切块的块 + 尚未找到宿主的独立图片块。"""

    blocks: list[Block] = field(default_factory=list)
    orphan_images: list[ParsedImage] = field(default_factory=list)


@dataclass
class ChunkingStats:
    """切块统计，写日志 + 回报给评测/前端。"""

    total_blocks: int = 0
    skipped_noise: int = 0
    skipped_empty: int = 0
    images_found: int = 0
    images_bound: int = 0
    images_dropped: int = 0
    equations: int = 0
    tables: int = 0
    chunks: int = 0


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


def _as_text(value: Any) -> str:
    """把 ``content_list`` 里可能是 str / list / None 的文本字段安全转成 str。

    实测 ``table_caption`` / ``table_footnote`` 是 ``list``，不能直接当字符串拼接。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_as_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    return str(value).strip()


def _pick(element: dict[str, Any], *keys: str) -> str:
    """字段容错取值：返回第一个非空字段（应对不同 MinerU 版本的字段名差异）。"""
    for key in keys:
        text = _as_text(element.get(key))
        if text:
            return text
    return ""


def _page_no(element: dict[str, Any]) -> int:
    """MinerU 的 ``page_idx`` 是 0 基，内部一律用 1 基。"""
    raw = element.get("page_idx", element.get("page", 0))
    try:
        return int(raw) + 1
    except (TypeError, ValueError):
        return 1


def _bbox(element: dict[str, Any]) -> list[float] | None:
    raw = element.get("bbox")
    if not isinstance(raw, list) or len(raw) < 4:
        return None
    try:
        return [float(value) for value in raw[:4]]
    except (TypeError, ValueError):
        return None


def _table_text(element: dict[str, Any]) -> str:
    """表格 → Markdown 表格；失败时退化为纯文本。"""
    body = _pick(element, "table_body", "text", "content")
    if not body:
        return ""
    if body.lstrip().lower().startswith("<table") or "<table" in body.lower():
        return html_to_markdown_table(body)
    return body


def normalize_content_list(
    content_list: list[dict[str, Any]], stats: ChunkingStats
) -> NormalizedContent:
    """``content_list`` → 有序的 :class:`Block` 列表 + 独立图片块。

    跳过页脚/页码等噪声块；把图片从各块中抽出来挂在 ``Block.images`` 上。
    """
    normalized = NormalizedContent()
    blocks = normalized.blocks
    for element in content_list:
        stats.total_blocks += 1
        kind = str(element.get("type") or "").strip().lower()

        if kind in NOISE_KINDS:
            stats.skipped_noise += 1
            continue

        page_no = _page_no(element)
        bbox = _bbox(element)
        images: list[ParsedImage] = []

        # 实测：图片多数以 table.img_path 出现；同时保留独立 image 块的分支
        img_path = _pick(element, "img_path", "image_path", "image_source.path")
        if not img_path and isinstance(element.get("image_source"), dict):
            img_path = _as_text(element["image_source"].get("path"))
        if img_path:
            images.append(
                ParsedImage(
                    img_path=img_path,
                    page_no=page_no,
                    bbox=bbox,
                    caption=_pick(element, "image_caption", "caption", "img_caption"),
                )
            )
            stats.images_found += 1

        if kind in IMAGE_KINDS:
            # 图片不单独成块：独立图片块没有宿主，交给按页码就近绑定
            if not images:
                stats.skipped_empty += 1
            normalized.orphan_images.extend(images)
            continue

        if kind in EQUATION_KINDS:
            raw = _pick(element, "text", "latex", "content")
            if not raw:
                stats.skipped_empty += 1
                continue
            stats.equations += 1
            text = raw if raw.startswith("$") else f"${raw}$"
            blocks.append(
                Block(kind="equation", text=text, page_no=page_no, bbox=bbox, images=images)
            )
            continue

        if kind in TABLE_KINDS:
            text = _table_text(element)
            caption = _as_text(element.get("table_caption"))
            footnote = _as_text(element.get("table_footnote"))
            if caption:
                text = f"{caption}\n{text}"
            if footnote:
                text = f"{text}\n{footnote}"
            if not text.strip():
                stats.skipped_empty += 1
                continue
            stats.tables += 1
            blocks.append(
                Block(kind="table", text=text, page_no=page_no, bbox=bbox, images=images)
            )
            continue

        # 其余一律当正文处理（text / title / paragraph / 未知类型降级）
        text = _pick(element, "text", "content", "paragraph_content")
        level_raw = element.get("text_level", element.get("level"))
        level: int | None
        try:
            level = int(level_raw) if level_raw is not None else None
        except (TypeError, ValueError):
            level = None
        is_title = kind in ("title", "paragraph_title") or level is not None
        block_kind = "title" if is_title else "text"
        if not text:
            # 某些版本把段落正文放在嵌套结构里
            text = _nested_text(element)
        if not text:
            stats.skipped_empty += 1
            continue
        blocks.append(
            Block(
                kind=block_kind,
                text=text,
                page_no=page_no,
                bbox=bbox,
                level=level,
                images=images,
            )
        )

    # 统一回填宿主块下标（图片绑定阶段据此精确定位）
    for block_index, block in enumerate(blocks):
        for image in block.images:
            image.block_index = block_index
    return normalized


def _nested_text(element: dict[str, Any]) -> str:
    """从 v2 风格的嵌套 content 里取纯文本。"""
    content = element.get("content")
    if isinstance(content, dict):
        for key in ("paragraph_content", "title_content"):
            if key in content:
                return _as_text(content[key])
    return ""


# ---------------------------------------------------------------------------
# 切块
# ---------------------------------------------------------------------------


def _tail_overlap(text: str, overlap_tokens: int) -> str:
    """取上一块尾部约 ``overlap_tokens`` 个 token 的文本，优先在段落/句子边界截断。"""
    if not text or overlap_tokens <= 0:
        return ""
    # 按 token 反推字符数：CJK 近似按 1 字 1 token 取上限，再多切一点保底
    budget = int(overlap_tokens * 1.2)
    tail = text[-budget:] if len(text) > budget else text
    # 尽量从一个完整段落的开头开始
    paragraph_break = tail.find("\n\n")
    if 0 <= paragraph_break < len(tail) // 2:
        return tail[paragraph_break:].strip()
    # 否则从句子边界开始
    sentence = re.search(r"[。！？；.!?;\n]", tail)
    if sentence and sentence.start() < len(tail) // 2:
        return tail[sentence.start() + 1 :].strip()
    return tail.strip()


class _ChunkBuilder:
    def __init__(
        self,
        target_tokens: int,
        overlap_tokens: int,
        min_tokens: int,
        max_tokens: int,
    ) -> None:
        self.target = target_tokens
        self.overlap = overlap_tokens
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.chunks: list[ChunkDraft] = []
        self._parts: list[str] = []
        self._tokens = 0
        self._pages: list[int] = []
        self._types: list[str] = []
        self._bboxes: list[list[float]] = []
        self._block_indices: list[int] = []

    # --- 累积状态 ---
    def _reset(self, seed: str = "") -> None:
        self._parts = [seed] if seed else []
        self._tokens = estimate_tokens(seed)
        self._pages = []
        self._types = []
        self._bboxes = []
        self._block_indices = []

    def _last_text(self) -> str:
        return "\n\n".join(self._parts)

    def flush(self) -> None:
        """把当前累积的块落盘为一个 chunk。"""
        if not self._parts:
            return
        text = self._last_text().strip()
        if not text:
            self._reset()
            return
        self.chunks.append(
            ChunkDraft(
                seq=len(self.chunks),
                text=text,
                page_start=min(self._pages) if self._pages else 0,
                page_end=max(self._pages) if self._pages else 0,
                block_types=list(dict.fromkeys(self._types)),
                bboxes=list(self._bboxes),
                block_indices=list(self._block_indices),
            )
        )
        self._reset()

    def add(self, block: Block, block_index: int) -> None:
        block_tokens = estimate_tokens(block.text)

        if block.kind == "equation":
            # 公式独立成块（docs/02 §3.1），但不参与正文合并
            self.flush()
            self._reset()
            self._parts = [block.text]
            self._tokens = block_tokens
            self._pages = [block.page_no]
            self._types = ["equation"]
            self._bboxes = [block.bbox] if block.bbox else []
            self._block_indices = [block_index]
            self.flush()
            self._reset()
            return

        # 超长块（如大表格）先按 max_tokens 硬切
        if block_tokens > self.max_tokens:
            pieces = _split_long_text(block.text, self.max_tokens)
            for piece in pieces:
                sub = Block(
                    kind=block.kind,
                    text=piece,
                    page_no=block.page_no,
                    bbox=block.bbox,
                    level=block.level,
                )
                self.add(sub, block_index)
            return

        if self._parts and self._tokens + block_tokens > self.max_tokens:
            overlap_text = _tail_overlap(self._last_text(), self.overlap)
            self.flush()
            self._reset(seed=overlap_text)

        if self._parts and self._tokens + block_tokens > self.target:
            overlap_text = _tail_overlap(self._last_text(), self.overlap)
            self.flush()
            self._reset(seed=overlap_text)

        self._parts.append(block.text)
        self._tokens += block_tokens
        self._pages.append(block.page_no)
        self._types.append(block.kind)
        if block.bbox:
            self._bboxes.append(block.bbox)
        self._block_indices.append(block_index)

    def finish(self) -> list[ChunkDraft]:
        self.flush()
        # 末块过短 → 并入前一块（docs/02 §3.2）。
        # 公式块例外：它按设计就是独立块，再短也不能并入正文。
        last = self.chunks[-1] if self.chunks else None
        if (
            len(self.chunks) >= 2
            and last is not None
            and last.block_types != ["equation"]
            and estimate_tokens(last.text) < self.min_tokens
        ):
            tail = self.chunks.pop()
            prev = self.chunks[-1]
            merged_tokens = estimate_tokens(prev.text) + estimate_tokens(tail.text)
            if merged_tokens <= self.max_tokens:
                prev.text = f"{prev.text}\n\n{tail.text}"
                prev.page_end = max(prev.page_end, tail.page_end)
                prev.block_types = list(dict.fromkeys([*prev.block_types, *tail.block_types]))
                prev.bboxes = [*prev.bboxes, *tail.bboxes]
                prev.block_indices = [*prev.block_indices, *tail.block_indices]
                self.chunks[-1] = prev
            else:
                self.chunks.append(tail)
                self.chunks[-1].seq = len(self.chunks) - 1
        # 重新编号，保证 seq 连续
        for index, chunk in enumerate(self.chunks):
            chunk.seq = index
        return self.chunks


def _split_long_text(text: str, max_tokens: int) -> list[str]:
    """把超长文本按段落/句子边界硬切成若干段。"""
    if estimate_tokens(text) <= max_tokens:
        return [text]
    # 目标切点按字符数反推（CJK 1 字 ≈ 1.3 token）
    budget = max(1, int(max_tokens / CJK_TOKEN_WEIGHT))
    pieces: list[str] = []
    remaining = text
    while estimate_tokens(remaining) > max_tokens:
        window = remaining[:budget]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = max(window.rfind("。"), window.rfind("\n"), window.rfind(". "))
            cut = cut + 1 if cut > 0 else budget
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def build_chunks(
    content_list: list[dict[str, Any]],
    *,
    target_tokens: int = 600,
    overlap_tokens: int = 100,
    min_tokens: int = 30,
    max_tokens: int = 900,
    stats: ChunkingStats | None = None,
) -> tuple[list[ChunkDraft], ChunkingStats]:
    """``content_list`` → chunk 列表（已绑定图片）。

    参数全部来自 ``Settings``，不硬编码。
    """
    stats = stats or ChunkingStats()
    normalized = normalize_content_list(content_list, stats)
    blocks = normalized.blocks

    builder = _ChunkBuilder(target_tokens, overlap_tokens, min_tokens, max_tokens)
    for index, block in enumerate(blocks):
        builder.add(block, index)
    chunks = builder.finish()

    # 绑定图片：先落在"它所在的那个 chunk"，其次向前找同页/前一页，再向后
    chunk_of_block: dict[int, int] = {}
    for chunk_index, chunk in enumerate(chunks):
        for block_index in chunk.block_indices:
            chunk_of_block.setdefault(block_index, chunk_index)

    candidates = [image for block in blocks for image in block.images]
    candidates.extend(normalized.orphan_images)
    for image in candidates:
        target = _find_chunk_for_image(image, chunks, chunk_of_block)
        if target is None:
            stats.images_dropped += 1
        else:
            target.images.append(image)
            stats.images_bound += 1

    # 去重（同一张图可能既来自 table.img_path 又来自图片块）
    seen: set[str] = set()
    for chunk in chunks:
        unique: list[ParsedImage] = []
        for image in chunk.images:
            if image.img_path in seen:
                continue
            seen.add(image.img_path)
            unique.append(image)
        chunk.images = unique

    stats.chunks = len(chunks)
    return chunks, stats


def _find_chunk_for_image(
    image: ParsedImage,
    chunks: list[ChunkDraft],
    chunk_of_block: dict[int, int],
) -> ChunkDraft | None:
    """按 docs/02 §3.3 的规则给图片找归属块。"""
    if not chunks:
        return None

    # 1) 图片所在的那个块（通常是表格）已经落进某 chunk
    if image.block_index >= 0:
        own = chunk_of_block.get(image.block_index)
        if own is not None:
            return chunks[own]

    # 2) 向前找：同页或前一页
    for chunk in reversed(chunks):
        if chunk.page_end <= image.page_no and image.page_no - chunk.page_end <= 1:
            return chunk

    # 3) 向后找
    for chunk in chunks:
        if chunk.page_start >= image.page_no:
            return chunk

    # 4) 实在没有相邻文字块 —— 丢弃（计入 stats）
    return None
