import json
import logging

import redis

from app.config import settings

logger = logging.getLogger(__name__)

EVENT_CHANNEL = "rag:events"

_pool: redis.ConnectionPool | None = None


def get_redis() -> redis.Redis:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(settings.redis_url, decode_responses=True)
    return redis.Redis(connection_pool=_pool)


def publish_event(event_type: str, payload: dict) -> None:
    """状态事件发布。SSE 只是通知通道，失败不影响主流程。"""
    try:
        get_redis().publish(
            EVENT_CHANNEL,
            json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False),
        )
    except Exception:
        logger.exception("publish event failed: %s", event_type)


def publish_doc_status(row) -> None:
    publish_event("doc_status", {
        "id": row["id"],
        "file_name": row["file_name"],
        "description": row["description"],
        "status": row["status"],
        "error_msg": row["error_msg"],
        "chunk_count": row["chunk_count"],
        "page_count": row["page_count"],
        "updated_at": row["updated_at"],
    })
