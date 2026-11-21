"""提供聊天提交接口和按请求回放的 SSE 工作流事件。"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from redis.asyncio import Redis

from backend.config import load_environment
from backend.schemas import (
    ChatAccepted,
    ChatRequest,
    WorkflowEvent,
    RedisChatJob,
    ProgressEvent,
)

REQUESTS = "chat:requests"
EVENTS_PREFIX = "chat:events:"
EVENT_TTL_SECONDS = 300
WORKFLOW_EVENT_ADAPTER = TypeAdapter(WorkflowEvent)
load_environment()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """创建并关闭 API 进程使用的 Redis 客户端。

    Args:
        app: FastAPI 应用实例。

    Returns:
        异步上下文管理器对象。

    Yields:
        API 运行期间可用的 Redis 客户端上下文。
    """
    app.state.redis = Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()


app = FastAPI(title="OpenAI Agents 情感陪伴", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/health")
async def health() -> dict[str, str]:
    """返回 API 进程存活状态。

    Args:
        无。

    Returns:
        含 `status` 字段的健康状态对象。
    """
    return {"status": "ok"}


@app.post(
    "/api/chat",
    response_model=ChatAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_chat(request: ChatRequest) -> ChatAccepted:
    """投递单轮聊天请求并返回用于订阅进度的编号。

    Args:
        request: 前端提交的聊天文本。
    Returns:
        请求编号和 SSE 订阅地址。

    Raises:
        redis.exceptions.RedisError: Redis 写入请求或进度事件失败。
    """
    redis: Redis = app.state.redis
    request_id = uuid4()
    job = RedisChatJob(request_id=request_id, text=request.text)
    key = f"{EVENTS_PREFIX}{request_id}"
    queued = ProgressEvent(
        event_type="progress",
        request_id=request_id,
        stage="queued",
        message="请求已提交，等待处理…",
    )
    await redis.xadd(key, {"payload": queued.model_dump_json()})
    await redis.expire(key, EVENT_TTL_SECONDS)
    await redis.xadd(
        REQUESTS,
        {"request_id": str(request_id), "payload": job.model_dump_json()},
    )
    return ChatAccepted(
        request_id=request_id,
        events_url=f"/api/chat/{request_id}/events",
    )


async def event_stream(
    request: Request, request_id: UUID, after_id: str
) -> AsyncIterator[str]:
    """从 Redis Stream 回放事件并持续推送后续状态。

    Args:
        request: HTTP 请求对象，用于检测客户端断开。
        request_id: 要订阅的工作流编号。
        after_id: Redis Stream 游标；新订阅默认从头回放。

    Returns:
        按 SSE 协议编码的事件文本迭代器。

    Raises:
        redis.exceptions.RedisError: Redis 读取事件失败。
    """
    redis: Redis = request.app.state.redis
    key = f"{EVENTS_PREFIX}{request_id}"
    cursor = after_id or "0-0"
    while not await request.is_disconnected():
        batches = await redis.xread({key: cursor}, count=20, block=15000)
        if not batches:
            yield ": keep-alive\n\n"
            continue
        for _, entries in batches:
            for event_id, fields in entries:
                cursor = event_id
                event = WORKFLOW_EVENT_ADAPTER.validate_json(fields["payload"])
                yield (
                    f"id: {event_id}\n"
                    f"event: {event.event_type}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
                if event.event_type in {"completed", "failed"}:
                    return


@app.get("/api/chat/{request_id}/events")
async def chat_events(
    request: Request,
    request_id: UUID,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """订阅或重连到指定请求的 SSE 进度事件流。

    Args:
        request: 当前 HTTP 请求。
        request_id: POST 接口返回的工作流编号。
        last_event_id: 浏览器断线重连时最后收到的 Redis Stream 编号。

    Returns:
        返回进度事件和最终结果的 SSE 响应。

    Raises:
        HTTPException: 请求编号无对应事件流时返回 404。
        redis.exceptions.RedisError: Redis 查询失败。
    """
    key = f"{EVENTS_PREFIX}{request_id}"
    redis: Redis = request.app.state.redis
    if not await redis.exists(key):
        raise HTTPException(status_code=404, detail="请求不存在或进度已过期")
    return StreamingResponse(
        event_stream(request, request_id, last_event_id or ""),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
