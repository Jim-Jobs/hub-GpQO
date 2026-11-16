"""Mock LLM 的单测。

Mock 不是"返回一句写死的话"——它读 Prompt 生成有意义的回答，
这样 ``MOCK_MODE=true`` 也能验证整条问答链路。
"""

from __future__ import annotations

from app.llm.prompts import PromptChunk, PromptImage, build_chat_prompt
from app.llm.vllm_client import MockLLMClient


def _prompt(with_image: bool = True):
    images = (
        [
            PromptImage(
                image_id="d_a_p7_i1",
                data_url="data:image/jpeg;base64,AAA",
                page_no=7,
                caption="柱状图",
            )
        ]
        if with_image
        else []
    )
    chunk = PromptChunk(
        chunk_id="d_a_c0007",
        text="2024 年前三季度营收分别为 28.1 / 33.5 / 42.3 亿元。",
        page_start=7,
        page_end=7,
        doc_filename="财报.pdf",
        images=images,
    )
    return build_chat_prompt("营收峰值在哪个季度？", [chunk])


def test_mock_quotes_retrieved_text_before_citing_image() -> None:
    """先给**基于检索内容的正文**，再带图片引用。"""
    text = MockLLMClient()._full_text(_prompt().messages)
    assert "2024 年前三季度营收分别为 28.1 / 33.5 / 42.3 亿元" in text
    assert "《财报.pdf》第 7 页" in text
    # 正文在引用标记之前
    assert text.index("28.1") < text.index("[[cite:")


def test_mock_emits_citation_marker_for_prompt_image() -> None:
    text = MockLLMClient()._full_text(_prompt().messages)
    assert "[[cite:d_a_p7_i1]]" in text


def test_mock_ignores_few_shot_example_in_system_prompt() -> None:
    """system prompt 里的示例也带【来源：...】，不能被当成资料。"""
    text = MockLLMClient()._full_text(_prompt().messages)
    assert "2024年度财报.pdf" not in text
    assert "d_ab12_p7_i1" not in text


def test_mock_handles_text_only_chunk() -> None:
    text = MockLLMClient()._full_text(_prompt(with_image=False).messages)
    assert "28.1" in text
    assert "[[cite:" not in text
    assert "没有绑定图片" in text


def test_mock_says_nothing_available_when_no_reference() -> None:
    from app.llm.prompts import build_chat_prompt as build

    text = MockLLMClient()._full_text(build("问题", []).messages)
    assert "没有可引用" in text


async def test_mock_stream_yields_incrementally() -> None:
    client = MockLLMClient(chunk_size=4)
    parts = [part async for part in client.chat_stream(_prompt().messages)]
    assert len(parts) > 1
    assert "".join(parts) == client._full_text(_prompt().messages)
