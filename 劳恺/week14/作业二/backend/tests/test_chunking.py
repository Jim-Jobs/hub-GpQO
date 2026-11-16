"""切块单测。

用例里的 ``content_list`` 元素**按 2026-11-12 实测的真实结构**构造：
``table_caption`` 是 list、没有 ``text_format``、``page_idx`` 0 基、表格是 HTML。
"""

from __future__ import annotations

from app.pipeline.chunking import (
    ChunkingStats,
    build_chunks,
    estimate_tokens,
    normalize_content_list,
)


def _text(text: str, page_idx: int = 0, level: int | None = None) -> dict:
    element: dict = {"type": "text", "text": text, "bbox": [0, 0, 100, 100], "page_idx": page_idx}
    if level is not None:
        element["text_level"] = level
    return element


def _table(body: str = "<table><tr><td>季度</td><td>营收</td></tr></table>", **kwargs) -> dict:
    element = {
        "type": "table",
        "table_body": body,
        "table_caption": [],
        "table_footnote": [],
        "bbox": [0, 0, 100, 100],
        "page_idx": 0,
    }
    element.update(kwargs)
    return element


def test_page_idx_is_zero_based_but_page_no_is_one_based() -> None:
    stats = ChunkingStats()
    _normalized = normalize_content_list([_text("第一页内容", page_idx=0)], stats)
    blocks = _normalized.blocks
    assert blocks[0].page_no == 1


def test_footer_and_page_number_are_skipped() -> None:
    stats = ChunkingStats()
    elements = [
        _text("正文"),
        {"type": "footer", "text": "• Agents SDK", "page_idx": 0},
        {"type": "page_number", "text": "API", "page_idx": 0},
    ]
    _normalized = normalize_content_list(elements, stats)
    blocks = _normalized.blocks
    assert [block.text for block in blocks] == ["正文"]
    assert stats.skipped_noise == 2


def test_table_caption_list_is_joined() -> None:
    """实测 table_caption 是 list（可能是空 list），不能直接当字符串拼。"""
    stats = ChunkingStats()
    element = _table(table_caption=["表 1 分季度营收", "单位：亿元"])
    _normalized = normalize_content_list([element], stats)
    blocks = _normalized.blocks
    assert "表 1 分季度营收 单位：亿元" in blocks[0].text


def test_table_html_becomes_markdown() -> None:
    stats = ChunkingStats()
    body = "<table><tr><td>季度</td><td>营收</td></tr><tr><td>2024Q3</td><td>42.3</td></tr></table>"
    _normalized = normalize_content_list([_table(body=body)], stats)
    blocks = _normalized.blocks
    text = blocks[0].text
    assert "| 季度 | 营收 |" in text
    assert "| 2024Q3 | 42.3 |" in text
    assert "<table>" not in text


def test_table_img_path_is_collected() -> None:
    """实测：图片以 table.img_path 出现，必须被收进待绑定列表。"""
    stats = ChunkingStats()
    element = _table(img_path="images/abc.jpg")
    _normalized = normalize_content_list([element], stats)
    blocks = _normalized.blocks
    assert len(blocks[0].images) == 1
    assert blocks[0].images[0].img_path == "images/abc.jpg"
    assert stats.images_found == 1


def test_missing_text_format_does_not_break() -> None:
    """实测没有 text_format 字段，代码不能依赖它。"""
    stats = ChunkingStats()
    _normalized = normalize_content_list([_text("## 标题", level=2)], stats)
    blocks = _normalized.blocks
    assert blocks[0].kind == "title"
    assert blocks[0].level == 2


def test_equation_becomes_its_own_chunk() -> None:
    stats = ChunkingStats()
    elements = [
        _text("前文" * 40),
        {"type": "equation", "text": "E = mc^2", "page_idx": 0},
        _text("后文" * 40),
    ]
    chunks, _ = build_chunks(elements, stats=stats)
    equation_chunks = [chunk for chunk in chunks if chunk.block_types == ["equation"]]
    assert len(equation_chunks) == 1
    assert equation_chunks[0].text == "$E = mc^2$"


def test_short_equation_chunk_is_not_merged() -> None:
    """公式块即使很短也必须独立 —— 碎块合并不能吃掉它。"""
    elements = [
        _text("正文内容"),
        {"type": "equation", "text": "y=ax+b", "page_idx": 0},
    ]
    chunks, _ = build_chunks(elements, target_tokens=600, min_tokens=30)
    assert len(chunks) == 2
    assert chunks[-1].block_types == ["equation"]
    assert chunks[-1].text == "$y=ax+b$"


def test_image_binds_to_its_own_table_chunk() -> None:
    """表格自带的渲染图必须绑到表格所在的那个 chunk。"""
    elements = [_table(img_path="images/t1.jpg")]
    chunks, stats = build_chunks(elements)
    assert len(chunks) == 1
    assert [image.img_path for image in chunks[0].images] == ["images/t1.jpg"]
    assert stats.images_bound == 1
    assert stats.images_dropped == 0


def test_image_binds_backward_to_previous_page_chunk() -> None:
    """图片后面没有文字块时，向前找同页或前一页的块。"""
    elements = [
        _text("第二页正文" * 30, page_idx=1),
        {"type": "image", "img_path": "images/pic.jpg", "page_idx": 2},
    ]
    chunks, stats = build_chunks(elements)
    bound = [chunk for chunk in chunks if chunk.images]
    assert bound, "图片应绑定到相邻块"
    assert bound[0].page_end == 2
    assert bound[0].images[0].img_path == "images/pic.jpg"
    assert stats.images_bound == 1


def test_image_without_adjacent_chunk_is_dropped() -> None:
    """既无向前也无向后可绑定的块 → 丢弃并计入 stats，不能静默丢失。"""
    elements = [{"type": "image", "img_path": "images/lonely.jpg", "page_idx": 0}]
    chunks, stats = build_chunks(elements)
    assert chunks == []
    assert stats.images_dropped == 1
    assert stats.images_bound == 0


def test_empty_content_list() -> None:
    chunks, stats = build_chunks([])
    assert chunks == []
    assert stats.chunks == 0


def test_long_text_is_split_under_max_tokens() -> None:
    long_text = "这是一个很长的段落。" * 300
    chunks, _ = build_chunks([_text(long_text)], target_tokens=600, max_tokens=900)
    assert len(chunks) > 1
    for chunk in chunks:
        assert estimate_tokens(chunk.text) <= 900 * 1.05


def test_chunk_seq_is_contiguous() -> None:
    elements = [_text("段落内容" * 200, page_idx=index) for index in range(5)]
    chunks, _ = build_chunks(elements)
    assert [chunk.seq for chunk in chunks] == list(range(len(chunks)))


def test_overlap_between_consecutive_chunks() -> None:
    """相邻块之间应有重叠，避免答案正好落在切口上。"""
    paragraphs = [f"第{i}段的内容比较长需要占一些位置。" * 12 for i in range(6)]
    elements = [_text(paragraph) for paragraph in paragraphs]
    chunks, _ = build_chunks(elements, target_tokens=200, overlap_tokens=50, max_tokens=300)
    assert len(chunks) > 1
    first_tail = chunks[0].text[-20:]
    assert any(first_tail in chunk.text for chunk in chunks[1:]), "重叠内容应出现在后续块中"


def test_unknown_block_type_degrades_to_text() -> None:
    stats = ChunkingStats()
    elements = [{"type": "some_new_type", "text": "未知类型内容", "page_idx": 0}]
    _normalized = normalize_content_list(elements, stats)
    blocks = _normalized.blocks
    assert blocks[0].kind == "text"
    assert blocks[0].text == "未知类型内容"


def test_estimate_tokens_chinese_vs_ascii() -> None:
    assert estimate_tokens("中文十个字") > 0
    assert estimate_tokens("") == 0
    # 中文密度高于英文
    assert estimate_tokens("中文中文中文中文") > estimate_tokens("abcdefgh") / 4
