"""开发工具：把 PDF 投递到 ``pdf.upload``（模拟 ``POST /api/documents``）。

    cd backend
    PYTHONIOENCODING=utf-8 \\
        "C:/Users/80627/anaconda3/envs/py312/python.exe" scripts/enqueue_one.py docs/大模型API.pdf

需要 Kafka 已启动，Worker 在跑时才会被消费。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import shutil
import sys
from pathlib import Path

from app.config import get_settings
from app.kafka.client import KafkaProducerClient, ensure_topics
from app.schemas.index import DocumentDoc, new_doc_id, new_task_id, utcnow
from app.storage.es_client import ESClient
from app.storage.index_schema import INDEX_DOCUMENTS


async def main() -> int:
    parser = argparse.ArgumentParser(description="投递一个 PDF 到 pdf.upload")
    parser.add_argument("pdf")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s :: %(message)s")
    settings = get_settings()
    settings.ensure_data_dirs()

    source = Path(args.pdf).resolve()
    if not source.is_file():
        print(f"文件不存在: {source}", file=sys.stderr)
        return 2

    doc_id = new_doc_id()
    target = settings.uploads_dir / f"{doc_id}.pdf"
    shutil.copyfile(source, target)

    doc = DocumentDoc(
        doc_id=doc_id,
        filename=source.name,
        file_path=str(target),
        content_hash=hashlib.sha256(target.read_bytes()).hexdigest(),
        size_bytes=target.stat().st_size,
        status="pending",
        progress=0.0,
        stage="queued",
    )

    es = ESClient(settings)
    await es.ensure_indices()
    await es.index_document(INDEX_DOCUMENTS, doc_id, doc.to_es())

    await asyncio.to_thread(ensure_topics, settings)
    producer = KafkaProducerClient(settings)
    await producer.send_upload(
        {
            "task_id": new_task_id(),
            "doc_id": doc_id,
            "filename": doc.filename,
            "content_hash": doc.content_hash,
            "file_path": doc.file_path,
            "uploaded_at": utcnow().isoformat(),
            "attempt": 0,
        }
    )
    await producer.close()
    await es.close()

    print(f"已投递 doc_id={doc_id} filename={doc.filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
