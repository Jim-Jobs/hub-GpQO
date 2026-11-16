"""Kafka 消费 worker：rag-data 消息 -> mineru 解析 -> 三路向量入库。

拓扑：3 分区 × 3 worker 进程（python -m mrag.worker 各起一份，同消费者组）。
offset 手动 commit：单条消息处理结束（成功 done / 失败 failed）后提交，
解析进程崩溃时消息不丢（重启重消费）；失败不阻塞分区。
"""

import logging
import signal
from pathlib import Path

from kafka import KafkaConsumer
from sqlalchemy.orm import Session

from mrag import zilliz_store
from mrag.config import get_settings
from mrag.models import (
    STATUS_FAILED,
    STATUS_PARSING,
    FileRow,
    init_db,
)
from mrag.worker.ingest import ingest_markdown
from mrag.worker.mineru_parser import MineruError, run_mineru

log = logging.getLogger("mrag.worker")

_stop = False


def _handle_signal(signum, frame):
    global _stop
    _stop = True
    log.info("收到信号 %s，处理完当前消息后退出", signum)


def process_message(session: Session, value: dict, client=None) -> str:
    """处理一条消息，返回终态（done/failed/skipped）。异常不外抛（不阻塞队列）。"""
    file_id = value.get("file_id")
    row = session.get(FileRow, file_id)
    if row is None:
        log.warning("文件记录不存在（可能已删除），跳过: %s", value)
        return "skipped"

    row.status = STATUS_PARSING
    row.error = ""
    session.commit()
    try:
        out_dir = Path(value["out_dir"])
        md_path = run_mineru(value["file_path"], out_dir, wait_sec=get_settings().mineru_timeout_sec)
        n = ingest_markdown(session, row, md_path, client=client)
        log.info("文档 %s(%s) 解析入库完成，%d chunks", row.id, row.filename, n)
        return "done"
    except MineruError as e:
        return _mark_failed(session, row, f"解析失败: {e}")
    except Exception as e:  # noqa: BLE001 - 单条失败不拖垮 worker
        return _mark_failed(session, row, f"{type(e).__name__}: {e}")


def _mark_failed(session: Session, row: FileRow, msg: str) -> str:
    log.error("文档 %s(%s) 失败: %s", row.id, row.filename, msg)
    row.status = STATUS_FAILED
    row.error = msg[:2000]
    session.commit()
    return "failed"


def make_consumer(bootstrap: str, topic: str, group_id: str) -> KafkaConsumer:
    return KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        enable_auto_commit=False,
        value_deserializer=lambda m: json_loads(m),
        consumer_timeout_ms=1000,  # 让循环能响应停止信号
        auto_offset_reset="earliest",
    )


def json_loads(raw: bytes):
    import json

    return json.loads(raw.decode("utf-8"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    s = get_settings()
    s.ensure_dirs()
    Session = init_db()
    zilliz_store.ensure_collection()
    consumer = make_consumer(s.kafka_bootstrap, s.kafka_topic, s.kafka_group_id)
    log.info(
        "worker 启动: bootstrap=%s topic=%s group=%s", s.kafka_bootstrap, s.kafka_topic, s.kafka_group_id
    )

    global _stop
    while not _stop:
        batch = consumer.poll(timeout_ms=1000, max_records=1)
        for _tp, records in batch.items():
            for record in records:
                outcome = process_message(Session(), record.value)
                consumer.commit()  # done/failed/skipped 均提交，失败态由前端手动重试
                log.info("offset %s -> %s", record.offset, outcome)
    consumer.close()
    log.info("worker 退出")


if __name__ == "__main__":
    main()
