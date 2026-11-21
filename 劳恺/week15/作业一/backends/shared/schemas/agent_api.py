"""Agent 服务的 HTTP 协议（两套框架完全一致）。

Agent 1 永远是 ``POST /analyze``，Agent 2 永远是 ``POST /respond``；
Agent 之间不直接通信，一律由 FastAPI 编排器调度。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backends.shared.schemas.emotion import EmotionResult
from backends.shared.schemas.response import ResponseResult


class HistoryMessage(BaseModel):
    """历史消息。完整传入，不做截断。"""

    role: Literal["user", "assistant"]
    content: str


class AnalyzeRequest(BaseModel):
    """Agent 1 请求体。"""

    conversation_id: str | None = None
    message: str = Field(min_length=1)


class AnalyzeResponse(EmotionResult):
    """Agent 1 响应体（直接复用 :class:`EmotionResult` 字段）。"""


class RespondRequest(BaseModel):
    """Agent 2 请求体。

    ``retry_feedback`` 由编排器在质量检查失败时回填，携带上一次回复的具体问题，
    Agent 2 应当据此修正。
    """

    conversation_id: str | None = None
    message: str = Field(min_length=1)
    emotion_result: EmotionResult
    history: list[HistoryMessage] = Field(default_factory=list)
    retry_feedback: str | None = None


class RespondResponse(ResponseResult):
    """Agent 2 响应体。"""


class HealthResponse(BaseModel):
    """Agent / 编排器健康检查响应。"""

    status: Literal["ok", "degraded", "error"] = "ok"
    service: str
    framework: str
    version: str = "0.1.0"
