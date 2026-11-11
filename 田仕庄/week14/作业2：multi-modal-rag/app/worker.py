"""解析 Worker：消费任务 → 解析 → 分块 → 入库 → 回写状态。

支持两种运行方式：
- 真实：confluent-kafka 消费者；
- `--mock`：DB 轮询 PENDING（不依赖 Kafka），供本地演示与测试。
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from pathlib import Path

from app.config import Settings, get_settings
from app.db import Session, init_db, utcnow
from app.errors import PARSE_FAILED, PARSE_OUTPUT_MISSING
from app.ingest import state
from app.ingest.chunker import chunk_blocks
from app.ingest.indexer import index_document
from app.ingest.parser import ParseError, parse_document
from app.ingest.service import build_task_payload
from app.logging_setup import setup_logging, trace_id_var
from app.models import File

logger = logging.getLogger(__name__)


class ParseWorker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        init_db()

    # ------------------------------------------------------------------
    # 单条任务
    # ------------------------------------------------------------------
    def task_payload(self, file_id: int) -> dict | None:
        with Session() as session:
            row = session.get(File, file_id)
            if row is None:
                return None
            return build_task_payload(row)

    def process_payload(self, payload: dict) -> str:
        trace_id = payload.get("trace_id") if isinstance(payload, dict) else None
        token = trace_id_var.set(str(trace_id or "-"))
        try:
            return self._process_payload(payload)
        finally:
            trace_id_var.reset(token)

    def _process_payload(self, payload: dict) -> str:
        file_id = payload.get("id")
        if not isinstance(file_id, int):
            logger.error("bad payload without id: %s", payload)
            return "BAD_PAYLOAD"

        with Session() as session:
            row = session.get(File, file_id)
            if row is None:
                return "SKIP_MISSING"
            status = row.status
            filepath = row.filepath
            filename = row.filename
            attempts = row.attempts or 0

        if status == state.DONE:
            logger.info("skip duplicate payload: file_id=%s status=DONE", file_id)
            return "SKIP_DONE"
        if status == state.FAILED:
            logger.info("skip duplicate payload: file_id=%s status=FAILED", file_id)
            return "SKIP_FAILED"
        if status == state.PARSING:
            logger.info("skip duplicate payload: file_id=%s status=PARSING", file_id)
            return "SKIP_BUSY"

        if not filepath or not Path(filepath).is_file():
            self._fail(
                file_id,
                PARSE_FAILED,
                "源文件不存在，可能已被删除",
                payload,
            )
            return "FAILED"

        attempt = attempts + 1
        if not state.mark_parsing(file_id, attempt=attempt):
            return "SKIP_RACE"

        try:
            parsed = self._parse_with_retries(filepath)
            chunks = chunk_blocks(parsed.blocks)
            if not chunks:
                raise ParseError(PARSE_OUTPUT_MISSING)
            count = index_document(
                file_id,
                chunks,
                file_name=filename,
                file_path=filepath,
            )
        except ParseError as exc:
            self._fail(file_id, exc.code, exc.message, payload)
            return "FAILED"
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("unexpected parse error: file_id=%s", file_id)
            self._fail(file_id, PARSE_FAILED, str(exc), payload)
            return "FAILED"

        state.mark_done(
            file_id,
            page_count=parsed.page_count,
            chunk_count=count,
        )
        logger.info(
            "file parsed: file_id=%s pages=%s chunks=%s",
            file_id,
            parsed.page_count,
            count,
        )
        return "DONE"

    def _parse_with_retries(self, filepath: str):
        last: ParseError | None = None
        attempts = self.settings.parse_retries + 1
        for index in range(attempts):
            try:
                return parse_document(
                    filepath, self.settings.processed_dir, self.settings
                )
            except ParseError as exc:
                last = exc
                logger.warning(
                    "parse attempt %s/%s failed: [%s] %s",
                    index + 1,
                    attempts,
                    exc.code,
                    exc.message,
                )
                if index < attempts - 1:
                    delay = self.settings.worker_retry_base_sec * (3**index)
                    if delay:
                        time.sleep(delay)
        if last is None:  # pragma: no cover
            last = ParseError(PARSE_FAILED)
        raise last

    def _fail(
        self, file_id: int, code: str, message: str, payload: dict
    ) -> None:
        state.mark_failed(file_id, code=code, message=message)
        logger.error(
            "file failed: file_id=%s code=%s message=%s", file_id, code, message
        )
        self._append_dlq(payload, code, message)

    def _append_dlq(self, payload: dict, code: str, message: str) -> None:
        path = self.settings.worker_dlq_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": utcnow().isoformat(),
                "code": code,
                "message": message[:500],
                "payload": payload,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:  # pragma: no cover
            logger.warning("append dlq failed: %s", path, exc_info=True)

    # ------------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------------
    def process_pending_once(self, limit: int = 50) -> int:
        with Session() as session:
            rows = (
                session.query(File)
                .filter(File.status == state.PENDING, File.kafka_sent.is_(True))
                .order_by(File.id.asc())
                .limit(limit)
                .all()
            )
            file_ids = [row.id for row in rows]

        for file_id in file_ids:
            payload = self.task_payload(file_id)
            if payload is None:
                continue
            try:
                self.process_payload(payload)
            except Exception:  # pragma: no cover
                logger.exception("worker crashed on file %s", file_id)
        return len(file_ids)

    def run_mock(self, stop: threading.Event | None = None) -> None:
        logger.info(
            "worker mock loop started: interval=%ss",
            self.settings.worker_poll_interval_sec,
        )
        while stop is None or not stop.is_set():
            try:
                processed = self.process_pending_once()
            except Exception:
                logger.exception("mock poll failed")
                processed = 0
            if processed == 0 and stop is not None:
                stop.wait(self.settings.worker_poll_interval_sec)
            elif processed == 0:
                time.sleep(self.settings.worker_poll_interval_sec)

    def run_kafka(self) -> None:
        from confluent_kafka import Consumer

        consumer = Consumer(
            {
                "bootstrap.servers": self.settings.kafka_bootstrap,
                "group.id": self.settings.kafka_group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
                "max.poll.interval.ms": self.settings.kafka_max_poll_interval_ms,
            }
        )
        consumer.subscribe([self.settings.kafka_topic])
        logger.info(
            "worker kafka loop started: topic=%s group=%s",
            self.settings.kafka_topic,
            self.settings.kafka_group_id,
        )
        try:
            while True:
                message = consumer.poll(1.0)
                if message is None:
                    continue
                if message.error():
                    logger.warning("kafka message error: %s", message.error())
                    continue
                try:
                    payload = json.loads(message.value().decode("utf-8"))
                    result = self.process_payload(payload)
                    logger.info(
                        "kafka task done: file_id=%s result=%s",
                        payload.get("id") if isinstance(payload, dict) else None,
                        result,
                    )
                except Exception:  # pragma: no cover
                    logger.exception("worker crashed on message")
                finally:
                    consumer.commit(message=message, asynchronous=False)
        except KeyboardInterrupt:  # pragma: no cover
            logger.info("worker interrupted")
        finally:
            consumer.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Parse worker")
    parser.add_argument("--mock", action="store_true", help="DB 轮询，不连 Kafka")
    parser.add_argument("--once", action="store_true", help="处理一轮后退出")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging("worker")
    worker = ParseWorker(settings)

    if args.once:
        processed = worker.process_pending_once()
        logger.info("worker once finished: processed=%s", processed)
        return
    if args.mock or settings.mock_external:
        worker.run_mock()
    else:
        worker.run_kafka()


if __name__ == "__main__":
    main()
