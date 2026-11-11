from __future__ import annotations

from app.embeddings import embed_chunks, embed_image, embed_texts
from app.ingest.chunker import Chunk


def test_mock_text_vectors_have_configured_dims(isolated_data_dir):
    from app.config import get_settings

    settings = get_settings()
    vectors = embed_texts(["hello", "world"])

    assert len(vectors) == 2
    for bge_vector, clip_vector in vectors:
        assert len(bge_vector) == settings.bge_dim
        assert len(clip_vector) == settings.clip_dim


def test_mock_vectors_are_deterministic(isolated_data_dir):
    first = embed_texts(["same text"])[0]
    second = embed_texts(["same text"])[0]
    assert first == second


def test_mock_image_vector_dim(isolated_data_dir):
    from app.config import get_settings

    assert len(embed_image("C:/nonexistent.png")) == get_settings().clip_dim


def test_embed_chunks_without_image_uses_zero_vector(isolated_data_dir):
    from app.config import get_settings

    chunks = [Chunk(text="plain text", page=1)]
    vectors = embed_chunks(chunks)

    assert len(vectors) == 1
    _, _, clip_image = vectors[0]
    assert clip_image == [0.0] * get_settings().clip_dim
