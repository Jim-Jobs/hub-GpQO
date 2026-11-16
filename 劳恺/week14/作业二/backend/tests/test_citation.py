"""引用协议单测。

重点覆盖 docs/06 §6.2 的核心难点：**标记跨 token 断裂**。
"""

from __future__ import annotations

from app.llm.citation import CitationParser, ImageMeta

IMAGES = [
    ImageMeta(image_id="d_a_p7_i1", index=1, url="/static/a.png", page_no=7),
    ImageMeta(image_id="d_a_p9_i2", index=2, url="/static/b.png", page_no=9),
]


def _feed_all(parser: CitationParser, text: str, size: int = 3) -> str:
    """按固定大小切碎喂入，模拟逐 token 输出。"""
    out = []
    for start in range(0, len(text), size):
        safe, events = parser.feed(text[start : start + size])
        out.append(safe)
        parser.citations  # noqa: B018 —— 触发去重保序逻辑
    safe, _ = parser.flush()
    out.append(safe)
    return "".join(out)


def test_complete_marker_is_replaced() -> None:
    parser = CitationParser(IMAGES)
    safe, events = parser.feed("看图 [[cite:d_a_p7_i1]] 结束")
    assert safe == "看图 【图1】 结束"
    assert len(events) == 1
    assert events[0].image_id == "d_a_p7_i1"
    assert events[0].text == "【图1】"


def test_marker_split_across_tokens_is_not_leaked() -> None:
    """核心难点：标记跨 token 断裂时，前端不能看到半截标记。"""
    text = "答案在图里[[cite:d_a_p7_i1]]，就是这样。"
    parser = CitationParser(IMAGES)
    output = _feed_all(parser, text, size=3)
    assert "[[cite:" not in output
    assert "[[cite:d_a_p7_i1]]" not in output
    assert "【图1】" in output
    assert output == "答案在图里【图1】，就是这样。"


def test_incomplete_marker_at_stream_end_is_kept_raw() -> None:
    """流结束时半截标记原样保留（可读降级），不丢内容。"""
    parser = CitationParser(IMAGES)
    safe, _ = parser.feed("正文[[cite:d_a_p")
    tail, _ = parser.flush()
    assert "正文" in safe + tail
    assert "[[cite:d_a_p" in safe + tail


def test_unknown_image_id_keeps_raw_text() -> None:
    """标记里的 id 不在送模列表 → 原样保留，不产生 citation 事件。"""
    parser = CitationParser(IMAGES)
    safe, events = parser.feed("见 [[cite:d_unknown_i9]] 图")
    assert safe == "见 [[cite:d_unknown_i9]] 图"
    assert events == []


def test_malformed_marker_is_treated_as_text() -> None:
    """模型输出了乱格式（如 [图1]）→ 当普通文本输出，不报错。"""
    parser = CitationParser(IMAGES)
    safe, events = parser.feed("结论是 [图1] 这样")
    assert "[图1]" in safe
    assert events == []


def test_bracket_in_normal_text_is_released_immediately() -> None:
    """正文里的普通括号不能被扣住，否则会卡住输出。"""
    parser = CitationParser(IMAGES)
    safe, _ = parser.feed("数组 a[0] 的值")
    assert safe == "数组 a[0] 的值"


def test_duplicate_markers_are_deduped_but_events_repeat() -> None:
    """重复引用同一张图：citations 去重，但 citation 事件仍会发。"""
    parser = CitationParser(IMAGES)
    _safe, first = parser.feed("[[cite:d_a_p7_i1]]")
    _safe2, second = parser.feed(" 又见 [[cite:d_a_p7_i1]]")
    assert len(first) == 1
    assert len(second) == 1
    assert len(parser.citations) == 1


def test_citation_order_is_preserved() -> None:
    parser = CitationParser(IMAGES)
    parser.feed("[[cite:d_a_p9_i2]]")
    parser.feed("[[cite:d_a_p7_i1]]")
    assert [item.image_id for item in parser.citations] == ["d_a_p9_i2", "d_a_p7_i1"]
    assert [item.index for item in parser.citations] == [2, 1]


def test_user_images_come_first_in_indexing() -> None:
    """用户图在前 → 【图1】【图2】 对应用户图。"""
    images = [
        ImageMeta(image_id="user_1", index=1),
        ImageMeta(image_id="user_2", index=2),
        ImageMeta(image_id="d_a_p7_i1", index=3),
    ]
    parser = CitationParser(images)
    safe, events = parser.feed("[[cite:d_a_p7_i1]]")
    assert safe == "【图3】"
    assert events[0].text == "【图3】"


def test_no_marker_means_no_citations() -> None:
    parser = CitationParser(IMAGES)
    parser.feed("纯文字回答，没有任何标记")
    parser.flush()
    assert parser.citations == []
