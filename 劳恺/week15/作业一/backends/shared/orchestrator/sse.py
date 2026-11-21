"""SSE 事件编码。"""

from __future__ import annotations

import json
from typing import Any


def sse_event(event: str, data: dict[str, Any]) -> str:
    """编码一条 SSE 消息。``data`` 必须是可 JSON 序列化的字典。"""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


#: 聊天流程中出现的全部事件类型（README 第 13 节）。
SSE_EVENTS: tuple[str, ...] = (
    "started",
    "emotion_analysis",
    "emotion_completed",
    "response_generation",
    "quality_check",
    "retrying",
    "completed",
    "failed",
)
