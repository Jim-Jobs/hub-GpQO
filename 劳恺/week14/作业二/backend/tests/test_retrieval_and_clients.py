"""jieba 预分词与适配器 mock 的单测。"""

from __future__ import annotations

from pathlib import Path

from app.llm.embedding_client import MockEmbeddingClient
from app.pipeline.mineru_client import MOCK_CONTENT_LIST, MockMineruClient, TaskState
from app.retrieval.jieba_tokenize import tokenize


def test_tokenize_splits_chinese() -> None:
    tokens = tokenize("营收峰值出现在第三季度").split()
    assert len(tokens) > 1
    assert "营收" in tokens


def test_tokenize_preserves_content() -> None:
    """分词只改空白，不丢字符。

    不断言具体词边界 —— jieba 词典会随版本变化（例如「第三季度」是整体一个词，
    而非「第三」+「季度」），断言性质比断言分词结果稳。
    """
    text = "营收峰值出现在第三季度 42 亿元"
    tokens = tokenize(text).split()
    assert "".join(tokens) == "".join(text.split())


def test_tokenize_keeps_punctuation_inside_token() -> None:
    """token 内部的标点保留（"42.3" 是一个完整 term）；独立标点 token 才丢弃。"""
    assert "42.3" in tokenize("营收 42.3 亿元").split()


def test_tokenize_drops_pure_punctuation() -> None:
    """纯标点对 BM25 是噪声，应当丢掉。"""
    tokens = tokenize("营收增长，、。！").split()
    assert tokens == ["营收", "增长"]


def test_tokenize_keeps_numbers_and_english() -> None:
    tokens = tokenize("2024Q3 营收 42.3 亿元").split()
    assert "2024Q3" in tokens
    assert "42.3" in tokens


def test_tokenize_empty_input() -> None:
    assert tokenize("") == ""
    assert tokenize("   ") == ""


async def test_mock_embedding_is_deterministic() -> None:
    client = MockEmbeddingClient(dim=1024)
    first = await client.embed_texts(["hello"])
    second = await client.embed_texts(["hello"])
    assert first == second
    assert len(first[0]) == 1024
    # 单位向量
    assert abs(sum(v * v for v in first[0]) - 1.0) < 1e-9


async def test_mock_embedding_image() -> None:
    client = MockEmbeddingClient(dim=1024)
    vector = await client.embed_image(Path("/tmp/a.png"))
    assert len(vector) == 1024
    assert client.supports_image_embedding is True


async def test_mock_mineru_returns_all_block_types() -> None:
    """Mock 的 content_list 要覆盖实测到的类型（含应被跳过的 footer）。"""
    result = await MockMineruClient().parse_pdf(Path("/tmp/x.pdf"))
    types = {item.get("type") for item in result.content_list}
    assert types == {"text", "table", "equation", "footer"}
    assert len(result.content_list) == len(MOCK_CONTENT_LIST)


def test_task_state_defaults() -> None:
    state = TaskState(state="running")
    assert state.progress == 0.0
    assert state.zip_url is None
    assert state.error is None
