"""Redis 保存文档状态和解析结果，Kafka 只传递解析任务。

连接优云智算上已经启动的服务：
Redis  /workspace/software/redis-7.2.5          localhost:6379
Kafka  /workspace/software/kafka_2.13-4.4.0    localhost:9092
"""

import json
import os
import time
import uuid

import redis
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

from indexlib import status_text

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC_PARSE = "pdf.parse"
DOCS_KEY = "rag:docs"


def connect_redis() -> redis.Redis:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    last_error: Exception | None = None
    for _ in range(10):
        try:
            client.ping()
            return client
        except redis.exceptions.RedisError as exc:
            last_error = exc
            time.sleep(1)
    raise SystemExit(f"无法连接 Redis {REDIS_URL}: {last_error}")


def doc_key(doc_id: str) -> str:
    return f"rag:doc:{doc_id}"


def chunks_key(doc_id: str) -> str:
    return f"rag:doc:{doc_id}:chunks"


def new_doc(filename: str) -> dict:
    now = str(int(time.time() * 1000))
    return {
        "id": uuid.uuid4().hex[:12],
        "filename": filename,
        "status": "pending",
        "error": "",
        "created_at": now,
        "updated_at": now,
        "chunk_count": "0",
    }


def save_doc(client: redis.Redis, doc: dict) -> None:
    client.hset(doc_key(doc["id"]), mapping=doc)
    client.rpush(DOCS_KEY, doc["id"])


def set_status(client: redis.Redis, doc_id: str, status: str, error: str = "") -> None:
    client.hset(
        doc_key(doc_id),
        mapping={
            "status": status,
            "error": error,
            "updated_at": str(int(time.time() * 1000)),
        },
    )


def list_docs(client: redis.Redis) -> list[dict]:
    docs = []
    for doc_id in client.lrange(DOCS_KEY, 0, -1):
        data = client.hgetall(doc_key(doc_id))
        if not data:
            continue
        data["status_text"] = status_text(data.get("status", ""))
        docs.append(data)
    return docs


def replace_chunks(client: redis.Redis, doc_id: str, chunks: list[dict]) -> None:
    key = chunks_key(doc_id)
    pipe = client.pipeline()
    pipe.delete(key)
    if chunks:
        pipe.rpush(key, *[json.dumps(chunk, ensure_ascii=False) for chunk in chunks])
    pipe.hset(doc_key(doc_id), "chunk_count", str(len(chunks)))
    pipe.execute()


def load_done_chunks(client: redis.Redis) -> list[dict]:
    chunks = []
    for doc_id in client.lrange(DOCS_KEY, 0, -1):
        meta = client.hgetall(doc_key(doc_id))
        if meta.get("status") != "done":
            continue
        for raw in client.lrange(chunks_key(doc_id), 0, -1):
            chunks.append(json.loads(raw))
    return chunks


def ensure_topic() -> None:
    admin = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP})
    futures = admin.create_topics([NewTopic(TOPIC_PARSE, num_partitions=1, replication_factor=1)])
    for future in futures.values():
        try:
            future.result()
        except Exception as exc:
            if "already exists" in str(exc).lower() or "TOPIC_ALREADY_EXISTS" in str(exc):
                continue
            raise


def make_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
            "enable.idempotence": True,
            "max.in.flight.requests.per.connection": 5,
        }
    )


def publish_parse_job(producer: Producer, doc_id: str, filename: str, path: str) -> None:
    payload = json.dumps(
        {"doc_id": doc_id, "filename": filename, "path": path, "ts": int(time.time() * 1000)},
        ensure_ascii=False,
    )
    delivered: dict = {}

    def on_delivery(err, _msg) -> None:
        if err is not None:
            delivered["err"] = err
        else:
            delivered["ok"] = True

    producer.produce(TOPIC_PARSE, key=doc_id.encode(), value=payload.encode(), on_delivery=on_delivery)
    remaining = producer.flush(10)
    if remaining or delivered.get("err") is not None or not delivered.get("ok"):
        raise RuntimeError(delivered.get("err") or "解析任务没有发到 Kafka")
