"""HTTP API 接收任务并将 Redis 请求事件流转换为 SSE。"""

from contextlib import asynccontextmanager
import os
from typing import AsyncIterator
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import TypeAdapter
from redis.asyncio import Redis

from backend.config import load_environment
from backend.events import publish_event
from backend.schemas import (
    ChatAccepted,
    ChatRequest,
    CompletedEvent,
    FailureEvent,
    ProgressEvent,
    RedisChatJob,
    WorkflowEvent,
)

REQUESTS = "chat:requests"
event_adapter = TypeAdapter(WorkflowEvent)
load_environment()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """创建并关闭 API 进程的 Redis 连接。

    Args:
        app: 当前 FastAPI 应用实例。

    Returns:
        用于 FastAPI 生命周期管理的异步上下文管理器。

    Yields:
        Redis 客户端可用期间的空生命周期标记。
    """
    app.state.redis = Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )
    yield
    await app.state.redis.aclose()


app = FastAPI(title="LangGraph 情绪识别与回应", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _stream_id_key(stream_id: str) -> tuple[int, int]:
    """将 Redis Stream ID 转为可比较的数值对。

    Args:
        stream_id: Redis Stream 的 `毫秒时间-序号` 格式编号。

    Returns:
        由毫秒时间和序号组成的整数元组。

    Raises:
        ValueError: Stream ID 格式不符合 Redis 约定。
    """
    milliseconds, sequence = stream_id.split("-", maxsplit=1)
    return int(milliseconds), int(sequence)


@app.get("/health")
async def health() -> dict[str, str]:
    """返回 API 存活状态。

    Args:
        无。

    Returns:
        包含 `status` 字段的状态对象。
    """
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatAccepted, status_code=202)
async def submit_chat(request: ChatRequest) -> ChatAccepted:
    """接受单轮聊天请求，写入排队状态和 Redis 请求流。

    Args:
        request: 包含一条用户输入的请求体。

    Returns:
        包含请求编号和 SSE 订阅地址的立即响应。

    Raises:
        HTTPException: Redis 无法接收任务时返回 503。
    """
    redis: Redis = app.state.redis
    request_id = uuid4()
    job = RedisChatJob(request_id=request_id, text=request.text)
    events_url = f"/api/chat/{request_id}/events"
    try:
        await publish_event(redis, ProgressEvent(
            event_type="progress", request_id=request_id,
            stage="queued", message="已收到消息，正在排队…",
        ))
        await redis.xadd(REQUESTS, {
            "request_id": str(request_id),
            "payload": job.model_dump_json(),
        })
    except Exception as error:
        # 返回给页面的错误不携带 Redis 地址、连接串或异常细节。
        raise HTTPException(status_code=503, detail="当前无法提交处理请求") from error
    return ChatAccepted(request_id=request_id, events_url=events_url)


async def _event_stream(
    request: Request,
    redis: Redis,
    request_id: UUID,
    last_event_id: str | None,
) -> AsyncIterator[str]:
    """逐条读取请求事件并编码为 SSE 文本帧。

    Args:
        request: 用于检测浏览器是否断开连接的 HTTP 请求。
        redis: 当前 API 进程的异步 Redis 客户端。
        request_id: 要订阅的请求编号。
        last_event_id: 浏览器断线重连时上次收到的 Redis Stream ID。

    Returns:
        按 SSE 格式编码的事件字符串异步迭代器。

    Raises:
        pydantic.ValidationError: Redis 中的事件数据不符合 SSE 契约。
        redis.exceptions.RedisError: 读取 Redis 事件流失败。
    """
    stream_key = f"chat:events:{request_id}"
    cursor = last_event_id or "0-0"
    terminal_key = f"chat:terminal:{request_id}"

    while not await request.is_disconnected():
        terminal_id = await redis.get(terminal_key)
        if terminal_id and _stream_id_key(cursor) >= _stream_id_key(terminal_id):
            return

        batches = await redis.xread({stream_key: cursor}, count=20, block=15000)
        if not batches:
            yield ": keep-alive\n\n"
            continue

        for _, entries in batches:
            for event_id, fields in entries:
                event = event_adapter.validate_json(fields["payload"])
                cursor = event_id
                yield (
                    f"id: {event_id}\n"
                    f"event: {event.event_type}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
                if isinstance(event, (CompletedEvent, FailureEvent)):
                    return


@app.get("/api/chat/{request_id}/events")
async def stream_chat_events(
    request_id: UUID,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> Response:
    """订阅并回放指定请求的进度和最终结果 SSE 事件。

    Args:
        request_id: POST 接口返回的唯一请求编号。
        request: 当前 HTTP 请求对象。
        last_event_id: 浏览器自动携带的断线续传事件编号。

    Returns:
        `text/event-stream` 响应；已读到终态后重连时返回 204。

    Raises:
        HTTPException: 请求事件流不存在或已过期时返回 404。
        ValueError: Last-Event-ID 格式无效时返回 400。
    """
    redis: Redis = app.state.redis
    stream_key = f"chat:events:{request_id}"
    terminal_id = await redis.get(f"chat:terminal:{request_id}")
    if not await redis.exists(stream_key):
        raise HTTPException(status_code=404, detail="请求不存在或进度事件已过期")
    if last_event_id and terminal_id:
        try:
            if _stream_id_key(last_event_id) >= _stream_id_key(terminal_id):
                return Response(status_code=204)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Last-Event-ID 格式无效") from error

    return StreamingResponse(
        _event_stream(request, redis, request_id, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
