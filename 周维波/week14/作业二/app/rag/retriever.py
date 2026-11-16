"""多模态检索：文本向量召回 + CLIP 以文搜图/以图搜图 + 切片自带图片。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sqlalchemy import select

from ..chunking import text_without_images
from ..config import settings
from ..db import session_scope
from ..embedding import get_embedder
from ..models import Chunk, Document
from ..status import STATUS_COMPLETED
from ..store import get_store
from .assets import absolutize_images, image_url, resolve_asset


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float
    kind: str  # text | image
    filename: str
    image_path: Path | None = None


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    context: str = ""
    sources: list[dict] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.chunks


def _load_documents(doc_ids: list[str] | None) -> dict[str, Document]:
    with session_scope() as session:
        stmt = select(Document).where(Document.status == STATUS_COMPLETED)
        if doc_ids:
            stmt = stmt.where(Document.id.in_([doc_id for doc_id in doc_ids if doc_id]))
        documents = list(session.execute(stmt).scalars())
        for document in documents:
            session.expunge(document)
    return {document.id: document for document in documents}


def _hydrate_chunks(hit_scores: dict[str, float]) -> dict[str, Chunk]:
    if not hit_scores:
        return {}
    with session_scope() as session:
        rows = list(
            session.execute(select(Chunk).where(Chunk.id.in_(list(hit_scores)))).scalars()
        )
        for row in rows:
            session.expunge(row)
    return {row.id: row for row in rows}


def _snippet(text: str, limit: int = 120) -> str:
    plain = " ".join(text_without_images(text).split())
    return plain[:limit] + ("…" if len(plain) > limit else "")


def retrieve(
    question: str,
    doc_ids: list[str] | None = None,
    query_image_path: str | None = None,
    top_k_text: int | None = None,
    top_k_image: int | None = None,
) -> RetrievalResult:
    """检索入口：返回拼好的上下文、来源列表与给 VLM 的图片。"""
    embedder = get_embedder()
    store = get_store()
    documents = _load_documents(doc_ids)
    allowed_doc_ids = list(documents)
    if not allowed_doc_ids:
        return RetrievalResult()

    limit_text = top_k_text or settings.top_k_text
    limit_image = top_k_image or settings.top_k_image

    # ---- 1) 文本召回 ----
    scores: dict[str, float] = {}
    kinds: dict[str, str] = {}
    query_text = question.strip()
    if query_text:
        text_vector = embedder.encode_texts([query_text])[0]
        for hit in store.search(text_vector, "text", limit_text, allowed_doc_ids):
            scores[hit.chunk_id] = max(scores.get(hit.chunk_id, 0.0), hit.score)
            kinds[hit.chunk_id] = "text"

    # ---- 2) 图像召回（跨模态时启用；否则靠文本命中切片带图 + 用户图搜图） ----
    clip_hits: list[tuple[str, float]] = []
    if embedder.supports_cross_modal and (query_image_path or query_text):
        if query_image_path and os.path.exists(query_image_path):
            vector = embedder.encode_images([query_image_path])[0]
        else:
            vector = embedder.encode_clip_texts([query_text])[0]
        if float(np.linalg.norm(vector)) > 0:
            clip_hits = [
                (hit.chunk_id, hit.score)
                for hit in store.search(vector, "clip_image", limit_image, allowed_doc_ids)
            ]

    for chunk_id, score in clip_hits:
        if chunk_id not in scores or score > scores[chunk_id]:
            scores[chunk_id] = score
            kinds[chunk_id] = "image"

    chunks = _hydrate_chunks(scores)

    # ---- 3) 排序、拼上下文（文本命中切片若带图，图会一并进入上下文与来源） ----
    ordered_ids = sorted(scores, key=lambda item: scores[item], reverse=True)
    results: list[RetrievedChunk] = []
    seen_images: set[tuple[str, str]] = set()
    context_parts: list[str] = []
    sources: list[dict] = []
    image_urls: list[str] = []
    image_paths: list[str] = []
    used_chars = 0

    for chunk_id in ordered_ids:
        chunk = chunks.get(chunk_id)
        if chunk is None:
            continue
        document = documents.get(chunk.doc_id)
        if document is None:
            continue

        image_path = resolve_asset(document.parsed_dir, chunk.image_rel) if chunk.image_rel else None
        is_image_hit = kinds.get(chunk_id) == "image"
        if is_image_hit and image_path is None:
            continue

        text = absolutize_images(chunk.text, chunk.doc_id)
        header = f"【资料 {len(results) + 1}｜文件：{document.filename}"
        if chunk.page_no:
            header += f"｜第 {chunk.page_no} 页"
        header += f"｜相关度 {scores[chunk_id]:.2f}】"

        block = f"{header}\n{text}"
        if used_chars + len(block) > settings.max_context_chars and results:
            break
        used_chars += len(block)
        context_parts.append(block)

        results.append(
            RetrievedChunk(
                chunk=chunk,
                score=scores[chunk_id],
                kind=kinds.get(chunk_id, "text"),
                filename=document.filename,
                image_path=image_path,
            )
        )

        if image_path is not None:
            key = (chunk.doc_id, chunk.image_rel or "")
            if key not in seen_images:
                seen_images.add(key)
                url = image_url(chunk.doc_id, chunk.image_rel or "")
                image_urls.append(url)
                if len(image_paths) < settings.max_images_to_llm:
                    image_paths.append(str(image_path))

        sources.append(
            {
                "document_id": chunk.doc_id,
                "filename": document.filename,
                "page_no": chunk.page_no,
                "chunk_seq": chunk.seq,
                "score": round(float(scores[chunk_id]), 4),
                "kind": kinds.get(chunk_id, "text"),
                "snippet": _snippet(chunk.text),
                "image_url": (
                    image_url(chunk.doc_id, chunk.image_rel) if chunk.image_rel else None
                ),
            }
        )

    return RetrievalResult(
        chunks=results,
        context="\n\n".join(context_parts),
        sources=sources,
        image_urls=image_urls,
        image_paths=image_paths,
    )
