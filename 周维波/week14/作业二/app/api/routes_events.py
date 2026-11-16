"""SSE 接口：/api/events —— 文档状态实时推送（含首帧全量快照）。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..db import session_scope
from ..models import Document
from ..schemas import document_payload
from .events import hub

router = APIRouter(prefix="/api", tags=["events"])


def _snapshot() -> dict:
    with session_scope() as session:
        rows = list(session.execute(select(Document).order_by(Document.created_at.desc())).scalars())
        return {"documents": [document_payload(row) for row in rows]}


def _encode(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    hub.attach_loop(asyncio.get_running_loop())
    queue = hub.register()

    async def stream():
        try:
            yield _encode("snapshot", _snapshot())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                event_type = event.get("type", "message")
                yield _encode(event_type, event)
        finally:
            hub.unregister(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
