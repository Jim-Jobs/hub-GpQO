"""Prompt 组装与 RRF 融合的单测。"""

from __future__ import annotations

from app.llm.prompts import PromptChunk, PromptImage, build_chat_prompt
from app.retrieval.hybrid_search import rrf_fuse


def _types(content: list[dict]) -> list[str]:
    return [item["type"] for item in content]


def _texts(content: list[dict]) -> list[str]:
    return [item["text"] for item in content if item["type"] == "text"]


def test_user_images_come_before_question() -> None:
    user = PromptImage(image_id="user_1", data_url="data:image/jpeg;base64,AAA", is_user=True)
    built = build_chat_prompt("问题是什么？", [], [user])
    content = built.messages[-1]["content"]
    assert content[0]["type"] == "image_url"
    assert content[1]["type"] == "text"
    assert "问题是什么？" in content[1]["text"]


def test_image_is_adjacent_to_its_own_text() -> None:
    """**图片必须紧贴它所属的文字块** —— 这是关联准确率的关键。"""
    image = PromptImage(
        image_id="d_a_p7_i1",
        data_url="data:image/jpeg;base64,BBB",
        page_no=7,
        caption="柱状图",
    )
    chunk = PromptChunk(
        chunk_id="d_a_c0007",
        text="2024 年前三季度营收分别为 28.1 / 33.5 / 42.3 亿元",
        page_start=7,
        page_end=7,
        doc_filename="财报.pdf",
        images=[image],
    )
    built = build_chat_prompt("峰值在哪", [chunk])
    content = built.messages[-1]["content"]

    # 找到图片的位置，它前应是 <img> 标记、后应是来源标注 + 正文
    image_position = next(
        index for index, item in enumerate(content) if item["type"] == "image_url"
    )
    before = content[image_position - 1]
    after = content[image_position + 1]
    assert before["type"] == "text"
    assert 'id="d_a_p7_i1"' in before["text"]
    assert "柱状图" in before["text"]
    assert after["type"] == "text"
    assert "【来源：《财报.pdf》第 7 页】" in after["text"]
    assert "42.3" in after["text"]


def test_text_only_chunk_gets_source_label() -> None:
    chunk = PromptChunk(
        chunk_id="d_a_c0001",
        text="这是一段纯文字。",
        page_start=3,
        page_end=3,
        doc_filename="手册.pdf",
    )
    built = build_chat_prompt("问题", [chunk])
    texts = _texts(built.messages[-1]["content"])
    assert any("【来源：《手册.pdf》第 3 页】" in text for text in texts)
    assert not any(item["type"] == "image_url" for item in built.messages[-1]["content"])


def test_image_order_maps_to_figure_index() -> None:
    """【图N】 的 N = 送模图片列表的 1 基下标，用户图在前。"""
    user = PromptImage(image_id="user_1", data_url="data:image/jpeg;base64,AAA", is_user=True)
    doc_image = PromptImage(image_id="d_a_p7_i1", data_url="data:image/jpeg;base64,BBB")
    chunk = PromptChunk(chunk_id="d_a_c0007", text="正文", images=[doc_image])
    built = build_chat_prompt("问题", [chunk], [user])

    assert built.index_of("user_1") == 1
    assert built.index_of("d_a_p7_i1") == 2


def test_system_prompt_is_first_and_contains_citation_instructions() -> None:
    built = build_chat_prompt("问题", [])
    assert built.messages[0]["role"] == "system"
    assert "[[cite:" in built.messages[0]["content"]


def test_history_is_inserted_before_user_message() -> None:
    history = [
        {"role": "user", "content": "上一轮"},
        {"role": "assistant", "content": "上一轮回答"},
    ]
    built = build_chat_prompt("这一轮", [], [], history)
    assert [message["role"] for message in built.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]


def test_caption_quotes_are_escaped() -> None:
    """caption 里的引号不能破坏 <img> 标记的结构。"""
    image = PromptImage(
        image_id="d_a_p7_i1",
        data_url="data:image/jpeg;base64,BBB",
        caption='他说"这是营收图"',
    )
    chunk = PromptChunk(chunk_id="d_a_c0007", text="正文", images=[image])
    built = build_chat_prompt("问题", [chunk])
    texts = _texts(built.messages[-1]["content"])
    marker = next(text for text in texts if "<img" in text)
    assert 'caption="他说\'这是营收图\'"' in marker


# ---------------------------------------------------------------------------
# RRF
# ---------------------------------------------------------------------------


def test_rrf_formula_matches_manual_calculation() -> None:
    """docs/06 §3.2 的手工验算示例。"""
    routes = {
        "bm25": [("A", 1.0), ("B", 2.0)],
        "vector": [("C", 1.0), ("D", 2.0), ("A", 3.0)],
        "image": [("C", 1.0)],
    }
    fused = rrf_fuse(routes, k=60)
    scores = {chunk_id: score for chunk_id, score, _r, _h in fused}

    assert abs(scores["A"] - (1 / 61 + 1 / 63)) < 1e-9
    assert abs(scores["B"] - (1 / 62)) < 1e-9
    assert abs(scores["C"] - (1 / 61 + 1 / 61)) < 1e-9
    # C 只在两路出现（都是第一）因此胜出
    assert fused[0][0] == "C"


def test_rrf_ranks_start_from_one() -> None:
    fused = rrf_fuse({"bm25": [("A", 1.0)]}, k=60)
    assert fused[0][2] == {"bm25": 1}


def test_rrf_records_which_routes_hit() -> None:
    routes = {"bm25": [("A", 1.0)], "vector": [("A", 1.0)], "image": []}
    fused = rrf_fuse(routes, k=60)
    assert fused[0][3] == {"bm25", "vector"}


def test_rrf_empty_routes() -> None:
    assert rrf_fuse({}) == []
    assert rrf_fuse({"bm25": []}) == []
