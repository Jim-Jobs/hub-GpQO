"""解析 Worker：消费 ``pdf.upload``，跑解析流水线。

对应文档：``docs/03-Kafka设计.md`` §5.1。

```
┌─── Worker 主循环 ────────────────────────────────────────────┐
│  1. poll() 取一条消息 msg                                     │
│  2. 执行解析流水线：MinerU → 切块 → caption → 向量化 → 写 ES   │
│  3. 写 ES 成功？                                              │
│       ├─ 是 → 提交 offset ✅                                  │
│       └─ 否 → 不提交 offset（消息将被重放）                    │
│  4. 失败且重试耗尽 → 发 dlq + 状态 failed → 提交 offset        │
└───────────────────────────────────────────────────────────────┘
```

**绝不留下卡在 ``processing`` 的文档**：任何异常都会落到 ``failed``（重试耗尽后）
或重新入队（还有重试次数时）。
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.kafka.client import (
    KafkaConsumerClient,
    KafkaProducerClient,
    commit_offset,
    ensure_topics,
    poll_once,
)
from app.llm.embedding_client import build_embedding_client
from app.llm.vllm_client import build_llm_client
from app.pipeline.caption import Captioner
from app.pipeline.ingest import (
    DocumentDeletedError,
    IngestContext,
    ingest_document,
)
from app.pipeline.mineru_client import build_mineru_client
from app.schemas.index import DocumentDoc, new_task_id, utcnow
from app.storage.es_client import ESClient
from app.storage.index_schema import INDEX_DOCUMENTS

logger = logging.getLogger(__name__)

PROCESSING = "processing"
FINAL_STATUSES = frozenset({"done", "failed"})


def build_status_event(
    doc: DocumentDoc,
    *,
    status: str,
    progress: float,
    stage: str,
    error: str | None = None,
) -> dict[str, Any]:
    """构造 ``pdf.parse.status`` 事件（``docs/03`` §3.2）。"""
    return {
        "doc_id": doc.doc_id,
        "status": status,
        "progress": round(progress, 3),
        "stage": stage,
        "error": error,
        "chunk_count": doc.chunk_count if status == "done" else None,
        "image_count": doc.image_count if status == "done" else None,
        "updated_at": utcnow().isoformat(),
    }


class Worker:
    """解析 Worker。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        es: ESClient | None = None,
        producer: KafkaProducerClient | None = None,
        consumer: KafkaConsumerClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._es = es or ESClient(self.settings)
        self._producer = producer or KafkaProducerClient(self.settings)
        self._consumer = consumer
        self._stopping = False
        self._ctx: IngestContext | None = None

    # ---------- 装配 ----------

    def _build_context(self) -> IngestContext:
        if self._ctx is None:
            llm = build_llm_client(self.settings)
            self._ctx = IngestContext(
                settings=self.settings,
                es=self._es,
                mineru=build_mineru_client(self.settings),
                embedder=build_embedding_client(self.settings),
                captioner=Captioner(
                    llm,
                    max_edge=self.settings.image_max_edge,
                    quality=self.settings.image_jpeg_quality,
                ),
            )
        return self._ctx

    async def start(self) -> None:
        self.settings.ensure_data_dirs()
        await self._es.ensure_indices()
        await asyncio.to_thread(ensure_topics, self.settings)
        if self._consumer is None:
            self._consumer = KafkaConsumerClient(
                self.settings,
                topic=self.settings.kafka_topic_upload,
                group_id=self.settings.kafka_consumer_group,
            )

    # ---------- 状态写回 ----------

    async def _set_status(
        self,
        doc: DocumentDoc,
        *,
        status: str,
        progress: float,
        stage: str,
        error: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """写 ES（状态真相）并 produce 状态事件（变更通知）。"""
        doc.status = status  # type: ignore[assignment]
        doc.progress = progress
        doc.stage = stage
        doc.error = error
        doc.updated_at = utcnow()
        for key, value in extra.items():
            setattr(doc, key, value)
        if status in FINAL_STATUSES:
            doc.finished_at = utcnow()

        await self._es.index_document(INDEX_DOCUMENTS, doc.doc_id, doc.to_es())
        event = build_status_event(doc, status=status, progress=progress, stage=stage, error=error)
        await self._producer.send_status(event)
        return event

    # ---------- 单条消息 ----------

    async def handle_message(self, message: dict[str, Any]) -> None:
        """处理一条 ``pdf.upload`` 消息。异常在此收敛，调用方负责提交 offset。"""
        doc_id = message.get("doc_id") or ""
        attempt = int(message.get("attempt") or 0)
        if not doc_id:
            logger.error("收到缺少 doc_id 的消息，直接丢弃: %s", message)
            return

        stored = await self._es.get(INDEX_DOCUMENTS, doc_id)
        if stored is None:
            # 文档在上传后被删除 —— 不再解析（docs/04 §2.4）
            logger.warning("文档 %s 不存在（可能已删除），跳过", doc_id)
            return

        doc = DocumentDoc.model_validate({**stored, "doc_id": doc_id})
        pdf_path = Path(message.get("file_path") or doc.file_path)
        if not pdf_path.is_file():
            await self._fail(doc, attempt, f"原始文件不存在: {pdf_path}")
            return

        logger.info("开始解析 doc=%s attempt=%d file=%s", doc_id, attempt, pdf_path.name)
        await self._set_status(doc, status=PROCESSING, progress=0.02, stage="mineru_parse")

        async def report(
            status: str, progress: float, stage: str, error: str | None
        ) -> None:
            await self._set_status(doc, status=status, progress=progress, stage=stage, error=error)

        try:
            result = await asyncio.wait_for(
                ingest_document(self._build_context(), doc, pdf_path, report=report),
                timeout=self.settings.parse_timeout_seconds,
            )
        except TimeoutError:
            await self._fail(
                doc,
                attempt,
                f"解析超时（{self.settings.parse_timeout_seconds / 60:.0f} 分钟）",
            )
            return
        except DocumentDeletedError:
            logger.info("文档 %s 已删除，丢弃解析结果", doc_id)
            return
        except Exception as exc:  # noqa: BLE001 —— 任何失败都必须落到明确终态
            logger.exception("解析失败 doc=%s attempt=%d", doc_id, attempt)
            await self._fail(doc, attempt, _readable_error(exc))
            return

        await self._set_status(
            doc,
            status="done",
            progress=1.0,
            stage="finished",
            chunk_count=result.chunk_count,
            image_count=result.image_count,
            parse_duration_ms=result.duration_ms,
        )
        logger.info(
            "解析完成 doc=%s chunks=%d images=%d %dms",
            doc_id,
            result.chunk_count,
            result.image_count,
            result.duration_ms,
        )

    async def _fail(self, doc: DocumentDoc, attempt: int, error: str) -> None:
        """失败处理：还有重试次数就重新入队，否则进 DLQ 并置 failed。"""
        max_attempts = self.settings.kafka_max_attempts
        if attempt + 1 < max_attempts:
            next_attempt = attempt + 1
            logger.warning(
                "重试 doc=%s attempt=%d/%d: %s", doc.doc_id, next_attempt, max_attempts, error
            )
            await self._set_status(
                doc,
                status="pending",
                progress=0.0,
                stage="queued",
                error=None,
            )
            await self._producer.send_upload(
                {
                    "task_id": new_task_id(),
                    "doc_id": doc.doc_id,
                    "filename": doc.filename,
                    "content_hash": doc.content_hash,
                    "file_path": doc.file_path,
                    "uploaded_at": utcnow().isoformat(),
                    "attempt": next_attempt,
                }
            )
            return

        # 重试耗尽 → 死信 + 终态 failed
        logger.error("重试耗尽 doc=%s，进入 DLQ: %s", doc.doc_id, error)
        await self._producer.send_dlq(
            {
                "task_id": new_task_id(),
                "doc_id": doc.doc_id,
                "filename": doc.filename,
                "file_path": doc.file_path,
                "attempt": attempt,
                "last_error": error,
                "failed_at": utcnow().isoformat(),
            }
        )
        await self._set_status(
            doc,
            status="failed",
            progress=doc.progress,
            stage=doc.stage,
            error=error,
        )

    # ---------- 主循环 ----------

    async def run_forever(self, *, poll_timeout_ms: int = 1000) -> None:
        await self.start()
        assert self._consumer is not None
        logger.info(
            "Worker 已启动，topic=%s group=%s",
            self.settings.kafka_topic_upload,
            self.settings.kafka_consumer_group,
        )
        try:
            while not self._stopping:
                messages = await poll_once(self._consumer, timeout_ms=poll_timeout_ms)
                for message in messages:
                    payload = message.value
                    if not isinstance(payload, dict):
                        logger.error("消息格式非法，丢弃: %r", payload)
                        await commit_offset(self._consumer)
                        continue
                    try:
                        await self.handle_message(payload)
                    except Exception:  # noqa: BLE001
                        logger.exception("handle_message 未捕获异常，不提交 offset")
                        continue
                    # 只有业务处理成功（或已明确落入终态/重新入队）才提交
                    await commit_offset(self._consumer)
        finally:
            await self.close()

    def stop(self) -> None:
        self._stopping = True

    async def close(self) -> None:
        if self._consumer is not None:
            await asyncio.to_thread(self._consumer.close)
        await self._producer.close()
        await self._es.close()


def _readable_error(exc: Exception) -> str:
    """异常 → 用户可读的中文原因。"""
    text = str(exc).strip()
    if text:
        return text
    return f"{type(exc).__name__}（无详细信息）"


async def _main() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )
    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):  # Windows 不支持 add_signal_handler
            loop.add_signal_handler(sig, worker.stop)
    await worker.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Worker 已停止")
