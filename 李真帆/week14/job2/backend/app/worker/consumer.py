import json
import logging
import uuid

from kafka import KafkaConsumer

from app.config import settings
from app.database import get_conn
from app.services.event_bus import get_redis, publish_doc_status
from app.utils import now_iso
from app.worker.pipeline import DocDeleted, process

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker")

LOCK_TTL = 3600
RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


def _try_lock(doc_id: str) -> str | None:
    token = uuid.uuid4().hex
    ok = get_redis().set(f"doc:lock:{doc_id}", token, nx=True, ex=LOCK_TTL)
    return token if ok else None


def _release_lock(doc_id: str, token: str) -> None:
    try:
        get_redis().eval(RELEASE_LUA, 1, f"doc:lock:{doc_id}", token)
    except Exception:
        logger.exception("release lock failed, doc=%s", doc_id)


def _mark_failed(doc_id: str, err: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET status='failed', error_msg=?, updated_at=? WHERE id=?",
            (err[:500], now_iso(), doc_id),
        )
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is not None:
        publish_doc_status(row)


def _handle(msg) -> None:
    payload = msg.value or {}
    doc_id = payload.get("doc_id")
    if not doc_id:
        logger.warning("消息缺少 doc_id，跳过: %s", msg.value)
        return
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone()
    if exists is None:
        logger.info("文档已删除，跳过: %s", doc_id)
        return
    token = _try_lock(doc_id)
    if token is None:
        logger.info("文档任务处理中（锁被占用），跳过: %s", doc_id)
        return
    try:
        process(doc_id, payload.get("file_path", ""), payload.get("file_name", ""))
    except DocDeleted:
        logger.info("解析过程中文档被删除: %s", doc_id)
    except Exception as e:
        logger.exception("解析失败: %s", doc_id)
        _mark_failed(doc_id, f"{type(e).__name__}: {e}")
    finally:
        _release_lock(doc_id, token)


def main() -> None:
    consumer = KafkaConsumer(
        settings.kafka_topic,
        group_id="rag-mineru-worker",
        bootstrap_servers=settings.kafka_bootstrap_servers,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    logger.info("worker 已启动, topic=%s, brokers=%s", settings.kafka_topic, settings.kafka_bootstrap_servers)
    try:
        for msg in consumer:
            try:
                _handle(msg)
            except Exception:
                logger.exception("处理消息异常: %s", msg.value)
            finally:
                consumer.commit()
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
