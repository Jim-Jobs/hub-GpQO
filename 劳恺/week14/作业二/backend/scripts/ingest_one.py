"""开发工具：把单个 PDF 直接跑完整条摄取链路（不经过 Kafka）。

用途：在没有 Worker / API 的情况下验证「MinerU → 切块 → caption → 向量化 → 写 ES」。

    cd backend
    PYTHONIOENCODING=utf-8 \\
        "C:/Users/80627/anaconda3/envs/py312/python.exe" scripts/ingest_one.py docs/大模型API.pdf

真正走 Kafka 的链路请用 ``python -m app.pipeline.worker`` + ``POST /api/documents``。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path

from app.config import get_settings
from app.llm.embedding_client import build_embedding_client
from app.llm.vllm_client import build_llm_client
from app.pipeline.caption import Captioner
from app.pipeline.ingest import IngestContext, ingest_document
from app.pipeline.mineru_client import build_mineru_client
from app.schemas.index import DocumentDoc, new_doc_id, utcnow
from app.storage.es_client import ESClient
from app.storage.index_schema import INDEX_CHUNKS, INDEX_DOCUMENTS, INDEX_IMAGES


async def main() -> int:
    parser = argparse.ArgumentParser(description="单文件摄取（绕过 Kafka）")
    parser.add_argument("pdf", help="PDF 路径")
    parser.add_argument("--keep", action="store_true", help="保留 ES 数据（默认保留）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )
    settings = get_settings()
    settings.ensure_data_dirs()

    source = Path(args.pdf).resolve()
    if not source.is_file():
        print(f"文件不存在: {source}", file=sys.stderr)
        return 2

    doc_id = new_doc_id()
    target = settings.uploads_dir / f"{doc_id}.pdf"
    shutil.copyfile(source, target)
    content_hash = hashlib.sha256(target.read_bytes()).hexdigest()

    doc = DocumentDoc(
        doc_id=doc_id,
        filename=source.name,
        file_path=str(target),
        content_hash=content_hash,
        size_bytes=target.stat().st_size,
        status="processing",
        progress=0.0,
        stage="queued",
    )

    es = ESClient(settings)
    await es.ensure_indices()
    await es.index_document(INDEX_DOCUMENTS, doc_id, doc.to_es())

    llm = build_llm_client(settings)
    ctx = IngestContext(
        settings=settings,
        es=es,
        mineru=build_mineru_client(settings),
        embedder=build_embedding_client(settings),
        captioner=Captioner(
            llm,
            max_edge=settings.image_max_edge,
            quality=settings.image_jpeg_quality,
        ),
    )

    async def report(status: str, progress: float, stage: str, error: str | None) -> None:
        print(f"  [{progress:5.0%}] {status:10s} {stage}")

    try:
        result = await ingest_document(ctx, doc, target, report=report)
    except Exception as exc:  # noqa: BLE001
        doc.status = "failed"
        doc.error = str(exc)
        doc.updated_at = utcnow()
        await es.index_document(INDEX_DOCUMENTS, doc_id, doc.to_es())
        print(f"\n✗ 摄取失败: {exc}", file=sys.stderr)
        await es.close()
        return 1

    doc.status = "done"
    doc.progress = 1.0
    doc.stage = "finished"
    doc.chunk_count = result.chunk_count
    doc.image_count = result.image_count
    doc.parse_duration_ms = result.duration_ms
    doc.finished_at = utcnow()
    doc.updated_at = utcnow()
    await es.index_document(INDEX_DOCUMENTS, doc_id, doc.to_es())

    await es.raw.indices.refresh(index=f"{INDEX_CHUNKS},{INDEX_IMAGES}")

    print("\n===== 结果 =====")
    print(f"doc_id={doc_id}")
    print(f"chunk_count={result.chunk_count} image_count={result.image_count}")
    print(f"duration={result.duration_ms}ms")
    print(f"stats={result.stats}")

    chunks = await es.raw.search(
        index=INDEX_CHUNKS,
        query={"term": {"doc_id": doc_id}},
        size=3,
        sort=[{"seq": "asc"}],
    )
    print("\n===== ES 中的前 3 个 chunk =====")
    for hit in chunks["hits"]["hits"]:
        source_doc = hit["_source"]
        print(
            f"- _id={hit['_id']} seq={source_doc['seq']} "
            f"pages={source_doc['page_start']}-{source_doc['page_end']} "
            f"images={len(source_doc.get('images', []))} "
            f"vector_dim={len(source_doc.get('vector', []))}"
        )
        print(f"  tokens: {source_doc.get('tokens', '')[:100]}")
        print(f"  text:   {source_doc.get('text', '')[:120].replace(chr(10), ' | ')}")

    images = await es.raw.search(
        index=INDEX_IMAGES, query={"term": {"doc_id": doc_id}}, size=2
    )
    print("\n===== ES 中的图片 =====")
    print(f"总数: {images['hits']['total']['value']}")
    for hit in images["hits"]["hits"]:
        source_doc = hit["_source"]
        print(
            json.dumps(
                {
                    "image_id": hit["_id"],
                    "chunk_id": source_doc.get("chunk_id"),
                    "url": source_doc.get("url"),
                    "caption": source_doc.get("caption", "")[:60],
                    "vector_dim": len(source_doc.get("vector", [])),
                },
                ensure_ascii=False,
            )
        )

    await es.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
