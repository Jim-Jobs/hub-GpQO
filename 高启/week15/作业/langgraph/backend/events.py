"""发布 Redis 中的请求进度事件并维护本地演示过期时间。"""

from redis.asyncio import Redis

from backend.schemas import CompletedEvent, FailureEvent, WorkflowEvent

EVENT_TTL_SECONDS = 3600


async def publish_event(redis: Redis, event: WorkflowEvent) -> str:
    """追加工作流事件，并在请求终态时记录终态事件编号。

    Args:
        redis: 当前进程使用的异步 Redis 客户端。
        event: 已校验的进度、完成或失败事件。

    Returns:
        Redis Stream 生成的事件编号。

    Raises:
        redis.exceptions.RedisError: 写入事件或设置过期时间失败。
    """
    request_key = str(event.request_id)
    stream_key = f"chat:events:{request_key}"
    event_id = await redis.xadd(stream_key, {"payload": event.model_dump_json()})
    await redis.expire(stream_key, EVENT_TTL_SECONDS)
    if isinstance(event, (CompletedEvent, FailureEvent)):
        terminal_key = f"chat:terminal:{request_key}"
        await redis.set(terminal_key, event_id, ex=EVENT_TTL_SECONDS)
    return event_id
