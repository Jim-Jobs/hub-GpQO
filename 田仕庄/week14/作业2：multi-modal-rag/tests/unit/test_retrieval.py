from __future__ import annotations

from app.chat.retriever import merge_rrf


def _hit(file_id: int, text: str, method: str, score: float = 1.0) -> dict:
    return {
        "file_id": file_id,
        "text": text,
        "file_name": "a.pdf",
        "file_path": "uploads/a.pdf",
        "page": "1",
        "score": score,
        "retrieval_method": method,
    }


def test_rrf_prefers_hits_from_multiple_channels():
    bge = [_hit(1, "first", "bge"), _hit(1, "shared", "bge")]
    clip = [_hit(1, "shared", "clip_text"), _hit(2, "other", "clip_text")]

    fused = merge_rrf([bge, clip], top_k=3)

    assert fused[0]["text"] == "shared"
    assert len(fused) == 3


def test_rrf_deduplicates_same_chunk():
    first = [_hit(1, "same", "bge")]
    second = [_hit(1, "same", "clip_text")]

    fused = merge_rrf([first, second], top_k=5)

    assert len(fused) == 1
    assert 0 < fused[0]["score"] < 1
