"""解析流水线：PDF -> markdown/图片 -> 切片 -> 向量化 -> 切片表 + 向量库。

这段逻辑由 Kafka 消费者（worker/main.py）调用，和 Web 上传完全解耦：
上传接口只负责落盘 + 投递消息，慢任务全部在这里异步完成。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import numpy as np
from sqlalchemy import delete

from ..chunking import ChunkDraft, split_markdown, text_without_images
from ..config import settings
from ..db import session_scope
from ..embedding import get_embedder
from ..models import Chunk, Document
from ..parsing import ParseError, get_parser
from ..status import STAGE_CHUNKING, STAGE_EMBEDDING, STAGE_INDEXING, STAGE_PARSING
from ..store import VectorRecord, get_store

ProgressReporter = Callable[[str, int], None]


def _resolve_image(parsed_dir: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    root = parsed_dir.resolve()
    candidate = (root / rel.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _fill_pages(drafts: list[ChunkDraft], meta: dict) -> None:
    """MinerU 的 markdown 没有页码标记时，用 content_list.json 的文本前缀反查页码。"""
    text_pages = meta.get("text_pages") or []
    if not text_pages:
        return
    for draft in drafts:
        if draft.page_no is not None:
            continue
        compact = " ".join(text_without_images(draft.text).split())[:60]
        for prefix, page_no in text_pages:
            head = compact[:20]
            if head and head in prefix:
                draft.page_no = int(page_no)
                break


def process_document(document: Document, report: ProgressReporter) -> dict:
    """执行一次完整解析，返回统计信息；异常向上抛，由 worker 决定重试/失败。"""
    started = time.perf_counter()
    parser = get_parser()
    parsed_root = settings.parsed_dir / document.id
    parsed_root.mkdir(parents=True, exist_ok=True)

    report(STAGE_PARSING, 15)
    result = parser.parse(Path(document.stored_path), parsed_root)
    if not result.markdown.strip():
        raise ParseError("解析结果为空（markdown 没有内容）")

    report(STAGE_CHUNKING, 55)
    drafts = split_markdown(result.markdown, settings.chunk_size)
    if not drafts:
        raise ParseError("文档切分后没有产生任何切片")
    _fill_pages(drafts, result.meta)

    # 图片路径校验（MinerU 的图默认在 md 同级的 images/ 下）
    image_by_draft: dict[int, Path | None] = {}
    for draft in drafts:
        image_path = _resolve_image(result.parsed_dir, draft.image_rel)
        image_by_draft[draft.seq] = image_path
        if image_path is None:
            draft.image_rel = None
        else:
            draft.image_rel = image_path.relative_to(result.parsed_dir.resolve()).as_posix()

    embedder = get_embedder()
    texts = [text_without_images(draft.text) or draft.text for draft in drafts]

    report(STAGE_EMBEDDING, 72)
    text_vectors = embedder.encode_texts(texts)
    if embedder.supports_cross_modal:
        clip_text_vectors = embedder.encode_clip_texts(texts)
    else:
        clip_text_vectors = np.zeros((len(texts), embedder.clip_dim), dtype=np.float32)

    image_drafts = [draft for draft in drafts if image_by_draft.get(draft.seq)]
    if image_drafts and embedder.supports_cross_modal:
        image_vectors = embedder.encode_images(
            [str(image_by_draft[draft.seq]) for draft in image_drafts]
        )
    else:
        image_vectors = None

    report(STAGE_INDEXING, 90)
    store = get_store()
    store.delete_document(document.id)

    records: list[VectorRecord] = []
    with session_scope() as session:
        session.execute(delete(Chunk).where(Chunk.doc_id == document.id))

        image_map: dict[int, object] = {}
        if image_vectors is not None:
            for row, draft in enumerate(image_drafts):
                image_map[draft.seq] = image_vectors[row]

        for draft in drafts:
            chunk = Chunk(
                doc_id=document.id,
                seq=draft.seq,
                text=draft.text,
                page_no=draft.page_no,
                image_rel=draft.image_rel,
            )
            session.add(chunk)
            session.flush()  # 拿到 chunk.id

            vector = image_map.get(draft.seq)
            records.append(
                VectorRecord(
                    chunk_id=chunk.id,
                    doc_id=document.id,
                    text_vector=text_vectors[draft.seq],
                    clip_text_vector=clip_text_vectors[draft.seq],
                    clip_image_vector=vector,  # 无图切片不写图像向量
                )
            )

        db_document = session.get(Document, document.id)
        if db_document is not None:
            db_document.parsed_dir = str(result.parsed_dir)
            db_document.chunk_count = len(drafts)
            db_document.image_count = len(result.images)

    store.upsert(records)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "chunk_count": len(drafts),
        "image_count": len(result.images),
        "elapsed_ms": elapsed_ms,
        "parser": result.engine,
        "parsed_dir": str(result.parsed_dir),
    }
