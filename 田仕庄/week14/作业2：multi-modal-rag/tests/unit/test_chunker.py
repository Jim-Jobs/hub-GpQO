from __future__ import annotations

from app.ingest.chunker import chunk_blocks
from app.ingest.parser import ParsedBlock


def test_chunks_keep_pages_and_images():
    blocks = [
        ParsedBlock("first page", page=1),
        ParsedBlock("", page=2, image_path="C:/tmp/fig.png"),
        ParsedBlock("third page", page=3),
    ]

    chunks = chunk_blocks(blocks, chunk_size=50)

    assert [chunk.page for chunk in chunks] == [1, 2, 3]
    assert "![fig](images/fig.png)" in chunks[1].text
    assert chunks[1].image_path == "C:/tmp/fig.png"


def test_long_text_is_split():
    text = "x" * 300
    chunks = chunk_blocks([ParsedBlock(text, page=1)], chunk_size=100)

    assert len(chunks) >= 3
    assert all(len(chunk.text) <= 100 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == text
