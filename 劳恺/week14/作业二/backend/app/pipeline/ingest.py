"""摄取编排：parse → chunk → caption → embed → index。

对应文档：``docs/09-开发计划.md`` M2 任务 2.7、``docs/01-架构设计.md`` §3.1。

**幂等**（``docs/03`` §5.1）：写 ES 之前先按 ``doc_id`` 删除旧块旧图 —— 这是
"序号做 chunk_id" 方案在消息重放时能保持数据正确的前提。

**失败语义**：任何异常都向上抛，由 Worker 决定重试还是进 DLQ；但**文档状态必须
落到明确的终态**，绝不允许永远停在 ``processing``。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.llm.embedding_client import EmbeddingClient
from app.pipeline.caption import Captioner
from app.pipeline.chunking import ChunkDraft, ChunkingStats, build_chunks
from app.pipeline.image_store import delete_doc_images, store_images
from app.pipeline.mineru_client import MineruClient
from app.retrieval.jieba_tokenize import tokenize
from app.schemas.index import (
    ChunkDoc,
    DocumentDoc,
    ImageDoc,
    ImageRef,
    chunk_id_for,
    utcnow,
)
from app.storage.es_client import ESClient
from app.storage.index_schema import INDEX_CHUNKS, INDEX_DOCUMENTS, INDEX_IMAGES

logger = logging.getLogger(__name__)

#: 状态回调：(status, progress, stage, error)
StatusReporter = Callable[[str, float, str, str | None], Awaitable[None]]

_PROCESSING = "processing"


@dataclass
class IngestResult:
    chunk_count: int
    image_count: int
    duration_ms: int
    stats: ChunkingStats


@dataclass
class IngestContext:
    """摄取所需依赖的集合（便于测试时整体替换）。"""

    settings: Settings
    es: ESClient
    mineru: MineruClient
    embedder: EmbeddingClient
    captioner: Captioner


async def _noop_report(status: str, progress: float, stage: str, error: str | None) -> None:
    return None


async def ingest_document(
    ctx: IngestContext,
    doc: DocumentDoc,
    pdf_path: Path,
    *,
    report: StatusReporter = _noop_report,
) -> IngestResult:
    """跑完整条摄取流水线，把块与图写进 ES。"""
    started = time.monotonic()
    settings = ctx.settings

    # ---------- 1. 解析 ----------
    await report(_PROCESSING, 0.05, "mineru_parse", None)
    result = await ctx.mineru.parse_pdf(pdf_path, doc.doc_id)
    work_dir = result.work_dir or (settings.work_dir / doc.doc_id)
    logger.info(
        "MinerU 解析完成 doc=%s task=%s blocks=%d",
        doc.doc_id,
        result.task_id,
        len(result.content_list),
    )

    # ---------- 2. 切块 ----------
    await report(_PROCESSING, 0.45, "chunking", None)
    chunks, stats = build_chunks(
        result.content_list,
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        min_tokens=settings.chunk_min_tokens,
        max_tokens=settings.chunk_max_tokens,
    )
    if not chunks:
        raise ValueError(
            "解析结果中没有可入库的文字块（PDF 可能是纯扫描件，请把 MINERU_IS_OCR 设为 true）"
        )

    # ---------- 3. 图片落盘 ----------
    await report(_PROCESSING, 0.50, "chunking", None)
    # 重解析前先清掉旧图片文件，避免残留
    delete_doc_images(doc.doc_id, settings.images_dir)
    ordered_images = [image for chunk in chunks for image in chunk.images]
    stored = store_images(
        ordered_images,
        doc_id=doc.doc_id,
        work_dir=work_dir,
        image_root=settings.images_dir,
    )
    stored_by_source = {item.source_path: item for item in stored}

    # ---------- 4. caption ----------
    await report(_PROCESSING, 0.55, "caption", None)
    caption_items = [
        (item.image_id, settings.images_dir / item.path)
        for item in stored
        if (settings.images_dir / item.path).is_file()
    ]
    captions = await ctx.captioner.caption_many(caption_items)
    for item in stored:
        generated = captions.get(item.image_id)
        if generated and generated.caption:
            item.caption = generated.caption
        # caption 失败时保留 MinerU 自带的 caption（可能为空）—— 不阻断入库

    # ---------- 5. 向量化 ----------
    await report(_PROCESSING, 0.70, "embedding", None)
    chunk_vectors = await ctx.embedder.embed_texts([chunk.text for chunk in chunks])
    image_vectors = await _embed_images(ctx, stored, settings)

    # ---------- 6. 写 ES ----------
    await report(_PROCESSING, 0.85, "indexing", None)
    # 文档可能已被用户删除 —— 写块之前先确认它还在（docs/04 §2.4）
    if not await ctx.es.exists(INDEX_DOCUMENTS, doc.doc_id):
        logger.warning("文档 %s 在解析期间被删除，丢弃本次结果", doc.doc_id)
        raise DocumentDeletedError(doc.doc_id)

    await ctx.es.delete_document_content(doc.doc_id)

    chunk_docs, image_docs = _build_documents(
        doc=doc,
        chunks=chunks,
        stored_by_source=stored_by_source,
        chunk_vectors=chunk_vectors,
        image_vectors=image_vectors,
    )
    await ctx.es.bulk_index(
        INDEX_CHUNKS, [(item.chunk_id, item.to_es()) for item in chunk_docs]
    )
    # 图片没有向量时**不能写入** —— ES 的 dense_vector 要求维度严格一致，
    # 空向量会让整批 bulk 被拒。这类图片仍留在 chunk 的嵌套 images 里，
    # 只是不进独立的图片索引（第三路召回会漏掉它）。
    indexable_images = [item for item in image_docs if item.vector]
    skipped = len(image_docs) - len(indexable_images)
    if skipped:
        logger.warning(
            "%d 张图片没有向量（caption 为空且嵌入后端不支持图片），未写入图片索引",
            skipped,
        )
    await ctx.es.bulk_index(
        INDEX_IMAGES, [(item.image_id, item.to_es()) for item in indexable_images]
    )

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "入库完成 doc=%s chunks=%d images=%d 用时=%dms",
        doc.doc_id,
        len(chunk_docs),
        len(image_docs),
        duration_ms,
    )
    return IngestResult(
        chunk_count=len(chunk_docs),
        image_count=len(indexable_images),
        duration_ms=duration_ms,
        stats=stats,
    )


class DocumentDeletedError(RuntimeError):
    """文档在解析期间被删除 —— 结果应当丢弃，但不算失败。"""

    def __init__(self, doc_id: str) -> None:
        super().__init__(f"文档 {doc_id} 已被删除")
        self.doc_id = doc_id


async def _embed_images(
    ctx: IngestContext, stored: list, settings: Settings
) -> dict[str, list[float]]:
    """图片向量。

    ``local`` 后端：真正把图片编码进图文统一空间。
    ``openai`` 后端：降级为 caption 文本向量（与 BM25 高度重叠，收益有限 ——
    见 ``docs/06`` §2.2，评测报告必须如实说明）。
    """
    vectors: dict[str, list[float]] = {}
    if not stored:
        return vectors

    if ctx.embedder.supports_image_embedding:
        for item in stored:
            path = settings.images_dir / item.path
            if not path.is_file():
                continue
            try:
                vectors[item.image_id] = await ctx.embedder.embed_image(path)
            except Exception:  # noqa: BLE001 —— 单张图向量失败不应中断整份文档
                logger.warning("图片向量化失败: %s", item.image_id, exc_info=True)
        return vectors

    # 降级：用 caption 文本的向量顶替图片向量
    captioned = [item for item in stored if item.caption]
    if not captioned:
        return vectors
    texts = await ctx.embedder.embed_texts([item.caption for item in captioned])
    for item, vector in zip(captioned, texts, strict=False):
        vectors[item.image_id] = vector
    return vectors


def _build_documents(
    *,
    doc: DocumentDoc,
    chunks: list[ChunkDraft],
    stored_by_source: dict,
    chunk_vectors: list[list[float]],
    image_vectors: dict[str, list[float]],
) -> tuple[list[ChunkDoc], list[ImageDoc]]:
    """把切块结果组装成 ES 文档。"""
    chunk_docs: list[ChunkDoc] = []
    image_docs: list[ImageDoc] = []

    for chunk, vector in zip(chunks, chunk_vectors, strict=False):
        chunk_id = chunk_id_for(doc.doc_id, chunk.seq)
        refs: list[ImageRef] = []
        for parsed in chunk.images:
            stored = stored_by_source.get(parsed.img_path)
            if stored is None:
                continue
            refs.append(
                ImageRef(
                    image_id=stored.image_id,
                    doc_id=doc.doc_id,
                    page_no=stored.page_no,
                    idx=stored.idx,
                    path=stored.path,
                    url=stored.url,
                    caption=stored.caption,
                    bbox=stored.bbox,
                    width=stored.width,
                    height=stored.height,
                )
            )
            image_docs.append(
                ImageDoc(
                    image_id=stored.image_id,
                    doc_id=doc.doc_id,
                    doc_filename=doc.filename,
                    chunk_id=chunk_id,
                    page_no=stored.page_no,
                    idx=stored.idx,
                    path=stored.path,
                    url=stored.url,
                    caption=stored.caption,
                    tokens=tokenize(stored.caption),
                    bbox=stored.bbox,
                    width=stored.width,
                    height=stored.height,
                    vector=image_vectors.get(stored.image_id, []),
                )
            )

        chunk_docs.append(
            ChunkDoc(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                doc_filename=doc.filename,
                seq=chunk.seq,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                text=chunk.text,
                tokens=tokenize(chunk.text),
                block_types=chunk.block_types,
                bboxes=chunk.bboxes,
                images=refs,
                vector=vector,
                created_at=utcnow(),
            )
        )
    return chunk_docs, image_docs
