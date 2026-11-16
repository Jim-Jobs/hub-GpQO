"""Redis 历史和 Kafka 消息共用的键、连接和写入约定。

服务器上已经装好的程序：
Redis  /workspace/software/redis-7.2.5          localhost:6379
Kafka  /workspace/software/kafka_2.13-4.4.0    localhost:9092
"""

import json
import os
import time
import uuid
from datetime import datetime

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC_EVENTS = "chat.events"
TOPIC_REPLIES = "chat.replies"
MAX_HISTORY = 200
SEEN_TTL_SECONDS = 86400

# 同一条 msg_id 只追加一次。脚本在 Redis 里原子执行，避免“去重键写上了、列表还没写上”。
_STORE_ONCE = """
local seen = KEYS[1]
local list = KEYS[2]
local meta = KEYS[3]
if redis.call('EXISTS', seen) == 1 then
  return 0
end
redis.call('SET', seen, 'stored', 'EX', ARGV[3])
redis.call('RPUSH', list, ARGV[1])
redis.call('LTRIM', list, -tonumber(ARGV[4]), -1)
redis.call('HSET', meta, 'updated_at', ARGV[2])
return 1
"""

_store_once = None


def connect_redis(max_attempts: int = 30) -> redis.Redis:
    client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            client.ping()
            return client
        except redis.exceptions.RedisError as exc:
            last_error = exc
            print(f"等待 Redis ({attempt}/{max_attempts}): {exc}", flush=True)
            time.sleep(2)
    raise SystemExit(f"无法连接 Redis {REDIS_URL}: {last_error}")


def msgs_key(conversation_id: str) -> str:
    return f"conv:{conversation_id}:msgs"


def meta_key(conversation_id: str) -> str:
    return f"conv:{conversation_id}:meta"


def seen_key(msg_id: str) -> str:
    return f"msg:seen:{msg_id}"


def published_key(msg_id: str) -> str:
    return f"msg:published:{msg_id}"


def new_message(
    conversation_id: str,
    role: str,
    content: str,
    msg_id: str | None = None,
    reply_to: str | None = None,
) -> dict:
    message = {
        "msg_id": msg_id or str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "ts": int(time.time() * 1000),
    }
    if reply_to:
        message["reply_to"] = reply_to
    return message


def dumps(message: dict) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str) -> dict:
    return json.loads(raw)


def load_history(client: redis.Redis, conversation_id: str) -> list[dict]:
    rows = client.lrange(msgs_key(conversation_id), 0, -1)
    return [loads(row) for row in rows]


def format_history(messages: list[dict]) -> str:
    if not messages:
        return "(还没有历史对话)"
    lines = []
    for message in messages:
        stamp = datetime.fromtimestamp(message["ts"] / 1000).strftime("%H:%M:%S")
        lines.append(f"[{stamp}] {message['role']}: {message['content']}")
    return "\n".join(lines)


def ensure_stored(client: redis.Redis, message: dict) -> bool:
    """把消息追加进会话列表。重复的 msg_id 返回 False。"""
    global _store_once
    if _store_once is None:
        _store_once = client.register_script(_STORE_ONCE)
    written = _store_once(
        keys=[
            seen_key(message["msg_id"]),
            msgs_key(message["conversation_id"]),
            meta_key(message["conversation_id"]),
        ],
        args=[dumps(message), str(message["ts"]), SEEN_TTL_SECONDS, MAX_HISTORY],
    )
    return bool(written)


def is_published(client: redis.Redis, msg_id: str) -> bool:
    return client.exists(published_key(msg_id)) == 1


def mark_published(client: redis.Redis, msg_id: str) -> None:
    client.set(published_key(msg_id), "1", ex=SEEN_TTL_SECONDS)
