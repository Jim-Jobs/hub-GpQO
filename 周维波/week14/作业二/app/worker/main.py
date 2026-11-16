"""Kafka 消费者（离线解析 worker）。

职责：
1. 消费 rag.parse.request，把文档状态从“待解析”推进到“解析中”；
2. 调用 pipeline 完成 MinerU 解析、切片、向量化、入库；
3. 成功 -> 解析完成；失败 -> 未达上限则重新投递重试，达上限则进 DLQ 并置为解析失败；
4. 每个阶段都把状态事件写到 rag.parse.status，供 API 的 SSE 实时推送。

启动：python -m app.worker.main
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
import traceback
from typing import Any

from confluent_kafka import Consumer, KafkaError

from ..config import settings
from ..db import init_db, session_scope
from ..kafka_bus import ensure_topics, publish_dlq, publish_parse_request, publish_status
from ..models import Document
from ..schemas import document_payload
from ..status import (
    STAGE_DONE,
    STAGE_FAILED,
    STAGE_PARSING,
    STAGE_QUEUED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARSING,
    STATUS_PENDING,
)
from .pipeline import process_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rag.worker")

_RUNNING = True


def _stop(signum, _frame) -> None:  # noqa: ANN001
    global _RUNNING
    _RUNNING = False
    logger.info("收到信号 %s，准备停止消费者…", signum)


def _consumer() -> Consumer:
    max_poll = max(600_000, settings.mineru_timeout_sec * 1000 + 180_000)
    return Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": settings.worker_group,
            "client.id": "rag-parse-worker",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "max.poll.interval.ms": max_poll,
            "session.timeout.ms": 45_000,
            "heartbeat.interval.ms": 15_000,
            "partition.assignment.strategy": "cooperative-sticky",
        }
    )


def _update_document(document_id: str, **fields: Any) -> dict | None:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            return None
        for key, value in fields.items():
            setattr(document, key, value)
        session.flush()
        return document_payload(document)


def _report(document_id: str, stage: str, progress: int) -> None:
    payload = _update_document(
        document_id,
        status=STATUS_PARSING,
        stage=stage,
        progress=progress,
    )
    if payload:
        publish_status(payload, stage)
        logger.info("[%s] %s %s%%", document_id[:8], stage, progress)


def handle_message(raw_value: bytes) -> None:
    try:
        payload = json.loads(raw_value.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("无法解析的消息，直接丢弃：%s (%s)", raw_value[:200], exc)
        return

    document_id = payload.get("document_id") or payload.get("id")
    if not document_id:
        logger.error("消息缺少 document_id：%s", payload)
        return

    attempt = int(payload.get("attempt") or 1)

    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            logger.warning("[%s] 文档记录不存在，跳过（可能已被删除）", document_id[:8])
            return
        filename = document.filename
        stored_path = document.stored_path
        session.flush()
        document.attempts = attempt

    parsing_payload = _update_document(
        document_id,
        status=STATUS_PARSING,
        stage=STAGE_PARSING,
        progress=10,
        error=None,
        attempts=attempt,
    )
    if parsing_payload:
        publish_status(parsing_payload, STAGE_PARSING)

    started = time.perf_counter()
    try:
        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is None:
                return
            session.expunge(document)

        stats = process_document(document, lambda stage, progress: _report(document_id, stage, progress))

        finished = _update_document(
            document_id,
            status=STATUS_COMPLETED,
            stage=STAGE_DONE,
            progress=100,
            error=None,
            chunk_count=stats["chunk_count"],
            image_count=stats["image_count"],
            parsed_dir=stats["parsed_dir"],
            elapsed_ms=stats["elapsed_ms"],
        )
        if finished:
            publish_status(finished, STAGE_DONE)
        logger.info(
            "[%s] 解析完成：%s 切片 / %s 图片 / %.1fs",
            document_id[:8],
            stats["chunk_count"],
            stats["image_count"],
            (time.perf_counter() - started),
        )
        return

    except Exception as exc:  # 解析失败：区分“可重试”和“达上限”
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error("[%s] 解析失败（第 %s 次）：%s", document_id[:8], attempt, error_message)
        logger.debug("%s", traceback.format_exc())

        if attempt < settings.parse_max_attempts and _RUNNING:
            retry_payload = _update_document(
                document_id,
                status=STATUS_PENDING,
                stage=STAGE_QUEUED,
                progress=0,
                error=error_message,
            )
            if retry_payload:
                publish_status(retry_payload, STAGE_QUEUED)
            delay = settings.retry_backoff_sec * attempt
            logger.info("第 %s 次失败，%.1fs 后重新投递重试", attempt, delay)
            time.sleep(delay)
            try:
                publish_parse_request(document_id, filename, stored_path, attempt=attempt + 1)
            except Exception as publish_error:
                logger.error("重试消息投递失败：%s", publish_error)
            return

        failed_payload = _update_document(
            document_id,
            status=STATUS_FAILED,
            stage=STAGE_FAILED,
            progress=0,
            error=error_message,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        if failed_payload:
            publish_status(failed_payload, STAGE_FAILED)
        try:
            publish_dlq(
                {
                    "type": "parse_failed",
                    "document_id": document_id,
                    "filename": filename,
                    "attempts": attempt,
                    "error": error_message,
                    "failed_at": time.time(),
                }
            )
        except Exception as dlq_error:
            logger.error("DLQ 投递失败：%s", dlq_error)


def main() -> int:
    init_db()
    try:
        ensure_topics()
    except Exception as exc:
        logger.error(
            "Kafka 不可用（%s: %s）。请先启动 broker：scripts\\kafka-up.ps1",
            type(exc).__name__,
            exc,
        )
        return 2

    consumer = _consumer()
    consumer.subscribe([settings.topic_parse_request])

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    logger.info(
        "worker 已启动：topic=%s group=%s broker=%s parser=%s embed=%s store=%s",
        settings.topic_parse_request,
        settings.worker_group,
        settings.kafka_bootstrap,
        settings.parser_backend,
        settings.embed_backend,
        settings.vector_store,
    )

    try:
        while _RUNNING:
            message = consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.warning("消费错误：%s", message.error())
                continue

            logger.info(
                "收到解析任务 partition=%s offset=%s key=%s",
                message.partition(),
                message.offset(),
                message.key().decode() if message.key() else None,
            )
            try:
                handle_message(message.value())
            except Exception:
                logger.error("处理消息异常：%s", traceback.format_exc())
            finally:
                try:
                    consumer.commit(asynchronous=False)
                except Exception as exc:
                    logger.warning("offset 提交失败：%s", exc)
    finally:
        consumer.close()
        logger.info("worker 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
