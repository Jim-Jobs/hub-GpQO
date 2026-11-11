"""补偿任务：收敛僵尸 PENDING 与超时 PARSING。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime, timedelta

from app.config import get_settings
from app.db import Session, utcnow
from app.errors import ENQUEUE_FAILED, PARSE_INTERRUPTED, STALE_PENDING
from app.ingest import state
from app.ingest.queue import enqueue_parse_task
from app.ingest.service import build_task_payload
from app.models import File

logger = logging.getLogger(__name__)


def sweep_once(now: datetime | None = None) -> dict[str, int]:
    settings = get_settings()
    now = now or utcnow()
    stats = {
        "requeued": 0,
        "failed_pending": 0,
        "failed_parsing": 0,
        "cleaned_images": 0,
    }

    # 1) PENDING 超时：重投或判失败
    pending_cutoff = now - timedelta(seconds=settings.pending_stale_sec)
    with Session() as session:
        rows = (
            session.query(File)
            .filter(File.status == state.PENDING, File.created_at < pending_cutoff)
            .limit(50)
            .all()
        )
        pending = [
            (row.id, row.requeue_count or 0, bool(row.kafka_sent)) for row in rows
        ]

    for file_id, requeue_count, kafka_sent in pending:
        if kafka_sent and requeue_count < settings.max_requeue:
            with Session() as session:
                row = session.get(File, file_id)
                if row is None:
                    continue
                row.requeue_count = requeue_count + 1
                row.kafka_sent = False
                row.updated_at = now
                session.commit()
                payload = build_task_payload(row)

            try:
                enqueue_parse_task(payload)
            except Exception as exc:
                state.mark_failed(
                    file_id,
                    code=ENQUEUE_FAILED,
                    message=f"补偿重投失败: {exc}",
                )
                stats["failed_pending"] += 1
                continue

            with Session() as session:
                row = session.get(File, file_id)
                if row is not None:
                    row.kafka_sent = True
                    row.updated_at = now
                    session.commit()
            stats["requeued"] += 1
            continue

        if state.mark_failed(
            file_id,
            code=STALE_PENDING,
            message=f"排队超过 {settings.pending_stale_sec}s 未被消费",
        ):
            stats["failed_pending"] += 1

    # 2) PARSING 超时：Worker 崩溃 / 被 kill
    parsing_cutoff = now - timedelta(
        seconds=int(settings.parse_timeout_sec * 1.5) + 60
    )
    with Session() as session:
        rows = (
            session.query(File)
            .filter(
                File.status == state.PARSING,
                File.started_at.is_not(None),
                File.started_at < parsing_cutoff,
            )
            .all()
        )
        parsing_ids = [row.id for row in rows]

    for file_id in parsing_ids:
        if state.mark_failed(
            file_id,
            code=PARSE_INTERRUPTED,
            message="解析超时或 Worker 中断，可重新解析",
        ):
            stats["failed_parsing"] += 1

    stats["cleaned_images"] = cleanup_question_images()

    if any(stats.values()):
        logger.info("sweeper stats: %s", stats)
    return stats


def cleanup_question_images(
    ttl_hours: int | None = None, *, now_ts: float | None = None
) -> int:
    """删除超过 TTL 的提问图片目录。"""
    settings = get_settings()
    ttl = settings.question_image_ttl_hours if ttl_hours is None else ttl_hours
    if ttl <= 0:
        return 0
    root = settings.questions_dir
    if not root.is_dir():
        return 0

    cutoff = (now_ts if now_ts is not None else time.time()) - ttl * 3600
    cleaned = 0
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                cleaned += 1
        except OSError:
            continue
    return cleaned


async def run_forever(stop: asyncio.Event) -> None:
    settings = get_settings()
    if not settings.sweep_enabled:
        return
    logger.info(
        "sweeper started: interval=%ss pending_stale=%ss",
        settings.sweep_interval_sec,
        settings.pending_stale_sec,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(sweep_once)
        except Exception:  # pragma: no cover
            logger.exception("sweeper iteration failed")
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.sweep_interval_sec
            )
        except asyncio.TimeoutError:
            continue
