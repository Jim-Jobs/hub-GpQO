import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.services.event_bus import EVENT_CHANNEL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["events"])

HEARTBEAT_SECONDS = 25


class EventHub:
    """订阅 Redis pub/sub，向所有 SSE 连接分发；满队列的慢客户端被剔除（其重连后会全量拉取）。"""

    def __init__(self):
        self._queues: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._subscribe_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    async def _subscribe_loop(self) -> None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        while True:
            try:
                pubsub = client.pubsub()
                await pubsub.subscribe(EVENT_CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        self._fanout(message["data"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("redis 订阅异常, 3s 后重连")
                await asyncio.sleep(3)

    def _fanout(self, data: str) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._queues.discard(q)


hub = EventHub()


@router.get("/events")
async def events(request: Request):
    q = hub.subscribe()

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
