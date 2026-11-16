"""chunker 纯函数单测。"""

from mrag.chunker import DEFAULT_CHUNK_SIZE, chunk_markdown, split_text2_chunks


def test_empty_and_blank_lines():
    assert chunk_markdown("") == []
    assert chunk_markdown("\n\n  \n") == []


def test_accumulate_until_chunk_size():
    # 累积规则（与参考一致）：判断的是拼接前的块长，块可越界 —— a+b(201) 继续拼 c(302)，
    # 到 d 时上一块 302 > 256 才开新块
    lines = ["a" * 100, "b" * 100, "c" * 100, "d" * 100]
    chunks = split_text2_chunks(lines)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 100 + "\n" + "b" * 100 + "\n" + "c" * 100  # len 302，越界但已拼入
    assert chunks[1] == "d" * 100


def test_reference_lines_filtered():
    lines = ["正文内容", "# References", "[1] 某某论文", "[12] 另一篇", "结尾"]
    chunks = split_text2_chunks(lines)
    assert chunks == ["正文内容\n结尾"]


def test_reference_like_but_not_citation_kept():
    lines = ["[abc] 不是引用（第二个字符非数字）"]
    assert split_text2_chunks(lines) == ["[abc] 不是引用（第二个字符非数字）"]


def test_image_markdown_line_stays_in_chunk():
    lines = ["第一段文字", "![图1](images/fig1.jpg)", "后续文字"]
    chunks = split_text2_chunks(lines)
    assert len(chunks) == 1
    assert "![图1](images/fig1.jpg)" in chunks[0]


def test_default_chunk_size_constant():
    assert DEFAULT_CHUNK_SIZE == 256
