"""检索层：BGE + CLIP 三路召回，RRF 融合。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app import embeddings, milvus
from app.config import get_settings
from app.db import Session
from app.models import File
from app.schemas import SourceItem

logger = logging.getLogger(__name__)

_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")


def merge_rrf(
    hit_lists: list[list[dict]],
    top_k: int,
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion：按名次融合，避免不同通道分数量纲不一致。"""
    scores: dict[tuple[int, str], float] = {}
    best: dict[tuple[int, str], dict] = {}

    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            key = (hit["file_id"], hit["text"][:120])
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in best:
                best[key] = hit
            else:
                previous = best[key].get("score")
                current = hit.get("score")
                if current is not None and (
                    previous is None or current > previous
                ):
                    best[key] = hit

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    fused: list[dict] = []
    for key, score in ordered[:top_k]:
        hit = dict(best[key])
        hit["score"] = score
        fused.append(hit)
    return fused


def _mock_hits(
    file_ids: list[int], top_k: int, use_clip: bool
) -> list[dict]:
    with Session() as session:
        rows = (
            session.query(File)
            .filter(File.id.in_(file_ids))
            .order_by(File.id.asc())
            .limit(top_k)
            .all()
        )

    hits: list[dict] = []
    for index, row in enumerate(rows):
        method = "bge"
        if use_clip and index % 2 == 1:
            method = "clip_text"
        hits.append(
            {
                "text": (
                    f"{row.filename}：产品A在 Q3 销售额环比下降 18%，"
                    "主要受渠道调整影响。"
                ),
                "file_id": row.id,
                "file_name": row.filename,
                "file_path": row.filepath,
                "page": "3",
                "score": 1.0 - index * 0.01,
                "retrieval_method": method,
            }
        )
    return hits[:top_k]


def retrieve(
    question: str,
    file_ids: list[int],
    *,
    top_k: int = 5,
    use_clip: bool = True,
) -> list[dict]:
    settings = get_settings()
    if not file_ids:
        return []

    if settings.mock_external:
        return _mock_hits(file_ids, top_k, use_clip)

    bge_vector, clip_vector = embeddings.embed_texts([question])[0]
    per_channel = max(top_k * 2, top_k + 2)

    channels = [
        milvus.search_vectors(
            bge_vector,
            "text_vector",
            per_channel,
            file_ids=file_ids,
            retrieval_method="bge",
        )
    ]
    if use_clip:
        channels.append(
            milvus.search_vectors(
                clip_vector,
                "clip_text_vector",
                per_channel,
                file_ids=file_ids,
                retrieval_method="clip_text",
            )
        )
        channels.append(
            milvus.search_vectors(
                clip_vector,
                "clip_image_vector",
                per_channel,
                file_ids=file_ids,
                retrieval_method="clip_image",
            )
        )

    return merge_rrf(channels, top_k)


def _image_urls(text: str, file_path: str) -> list[str]:
    settings = get_settings()
    document_dir = Path(settings.processed_dir) / Path(file_path).stem
    urls: list[str] = []
    for reference in _IMAGE_RE.findall(text):
        name = Path(reference).name
        if not document_dir.is_dir():
            continue
        matches = sorted(document_dir.rglob(name))
        if not matches:
            continue
        relative = matches[0].relative_to(settings.processed_dir).as_posix()
        url = f"/files/processed/{relative}"
        if url not in urls:
            urls.append(url)
    return urls


def to_source(hit: dict) -> SourceItem:
    text = hit.get("text", "")
    page = hit.get("page")
    return SourceItem(
        file_id=hit["file_id"],
        file_name=hit.get("file_name", ""),
        file_path=hit.get("file_path", ""),
        text_snippet=text[:300],
        page=str(page) if page else None,
        image_urls=_image_urls(text, hit.get("file_path", "")),
        retrieval_method=hit.get("retrieval_method"),
        score=hit.get("score"),
    )
