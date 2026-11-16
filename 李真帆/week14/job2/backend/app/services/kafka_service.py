import json
import logging
import threading

from kafka import KafkaProducer

from app.config import settings
from app.utils import now_iso

logger = logging.getLogger(__name__)

_producer: KafkaProducer | None = None
_lock = threading.Lock()


def get_producer() -> KafkaProducer:
    global _producer
    if _producer is None:
        with _lock:
            if _producer is None:
                _producer = KafkaProducer(
                    bootstrap_servers=settings.kafka_bootstrap_servers,
                    key_serializer=str.encode,
                    value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                    acks="all",
                    retries=3,
                )
    return _producer


def send_parse_task(doc_id: str, file_path: str, file_name: str) -> None:
    msg = {
        "doc_id": doc_id,
        "file_path": file_path,
        "file_name": file_name,
        "sent_at": now_iso(),
    }
    future = get_producer().send(settings.kafka_topic, key=doc_id, value=msg)
    future.get(timeout=10)
