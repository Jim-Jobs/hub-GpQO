"""会话路由（只读）。

对应文档：``docs/04-API契约.md`` §5。

会话的**写入**发生在问答链路（M3）：``POST /api/chat`` 结束后把这一轮追加进
``mmrag-sessions``。这里只提供读取，方便前端恢复历史。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.errors import not_found
from app.schemas.documents import SessionSummary
from app.storage.index_schema import INDEX_SESSIONS

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(request: Request) -> list[SessionSummary]:
    result = await request.app.state.es_client.search(
        INDEX_SESSIONS,
        {
            "query": {"match_all": {}},
            "size": 100,
            "sort": [{"updated_at": {"order": "desc", "unmapped_type": "date"}}],
        },
    )
    summaries: list[SessionSummary] = []
    for hit in result["hits"]["hits"]:
        source: dict[str, Any] = hit["_source"]
        messages = source.get("messages") or []
        first_user = next(
            (
                message.get("content", "")
                for message in messages
                if message.get("role") == "user"
            ),
            "",
        )
        summaries.append(
            SessionSummary(
                session_id=hit["_id"],
                title=str(first_user)[:60],
                message_count=len(messages),
                created_at=source.get("created_at"),
                updated_at=source.get("updated_at"),
            )
        )
    return summaries


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict[str, Any]:
    stored = await request.app.state.es_client.get(INDEX_SESSIONS, session_id)
    if stored is None:
        raise not_found(f"会话不存在: {session_id}")
    return {"session_id": session_id, "messages": stored.get("messages", [])}
